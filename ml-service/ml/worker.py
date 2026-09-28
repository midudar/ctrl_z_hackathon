"""Воркер сервиса «Инспектор ИИ»: задача «проверить объект» → `ml.run` → картинки страниц-доказательств → событие.

    python -m ml.worker job <задача.json>   одна задача; события — строки JSON в stdout (backend без брокера)
    python -m ml.worker serve               RabbitMQ (AMQP_URL) + REST для синхронных вызовов (порт WORKER_PORT, 8000)

Задача (JSON, её собирает backend):
    {"process_id", "object_id", "docs_dir", "meta_dir", "out_dir", "cache_dir", "pages_dir", "render_max", "attempt"}
    meta_dir — реестр только этого объекта + матрица + схема ответа (INSPECTOR_DATA для ml.run).
События (в RabbitMQ — очередь AMQP_EVENTS_QUEUE, по умолчанию inspector.events):
    {"type": "started" | "progress" | "log" | "done" | "failed", "process_id", ...}
    done: {"exit_code", "submission", "integrity", "model_version", "duration", "rendered"}

`ml.run` запускается отдельным процессом на каждую задачу: модули держат кэши в памяти (lru_cache), а пути читают
из переменных окружения один раз при импорте — долгоживущий процесс с разными объектами дал бы неверный результат.

REST (serve): POST /render {pdf, page, out, max, region?}, POST /protocol {payload, out, format}, GET /health.
Пути — общие с backend (в docker-compose папки документов и данных смонтированы в оба контейнера одинаково).
"""
import json
import os
import subprocess
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ML_VERSION = "inspector-ml 2026.09.28 (19 параметров с извлечением + свободный поиск)"
STEPS = 10          # 8 треков + рамки доказательств + целостность комплекта (строки лога ml.run)
CACHE_FILES = None  # файлов в кэше распознанных страниц (считается при запуске serve, отдаётся в /health)


def _read_manifest(meta_dir):
    rows = {}
    with open(Path(meta_dir) / "document_manifest.jsonl", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                r = json.loads(line)
                rows[r["file_id"]] = r
    return rows


def prerender(job, submission_path):
    """Картинки всех страниц-доказательств ответа — в кэш страниц backend (pages_dir/<file_id>/<стр>_<max>.png)."""
    from ml.render import render
    sub = json.loads(Path(submission_path).read_text(encoding="utf-8"))
    manifest = _read_manifest(job["meta_dir"])
    max_px = int(job.get("render_max") or 2000)
    pages = sorted({(e["file_id"], e["pdf_page_number"]) for c in sub["checks"] for e in c.get("evidence", [])})
    done = 0
    for file_id, page in pages:
        out = Path(job["pages_dir"]) / file_id / f"{page}_{max_px}.png"
        if out.exists() or file_id not in manifest:
            continue
        try:
            render(Path(job["docs_dir"]) / manifest[file_id]["relative_path"], page, str(out), max_px)
            done += 1
        except Exception as e:                      # картинку backend дорисует по запросу
            print(f"  страница {file_id}:{page} не отрисована: {e}", file=sys.stderr)
    return done, len(pages)


def run_job(job, emit):
    pid, object_id = job["process_id"], job["object_id"]
    t0 = time.time()
    emit({"type": "started", "process_id": pid, "message": f"обработка документов объекта {object_id}"})
    out_dir = Path(job["out_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "submission.json"
    env = dict(os.environ, INSPECTOR_DOCS=job["docs_dir"], INSPECTOR_DATA=job["meta_dir"], INSPECTOR_OUT=str(out_dir),
               PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
    if job.get("cache_dir"):
        env["INSPECTOR_CACHE"] = job["cache_dir"]
    cmd = [sys.executable, "-W", "ignore", "-m", "ml.run", object_id, "--out", str(out)]
    steps = 0
    with open(out_dir / "ml_run.log", "w", encoding="utf-8") as log:
        proc = subprocess.Popen(cmd, cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding="utf-8", errors="replace", bufsize=1)
        for line in proc.stdout:
            line = line.rstrip()
            log.write(line + "\n")
            if not line or line.startswith("MuPDF error"):
                continue
            s = line.strip()
            if s.startswith(("трек ", "рамки доказательств", "целостность комплекта")):
                steps += 1
                emit({"type": "progress", "process_id": pid, "message": s, "progress": round(min(0.9, 0.9 * steps / STEPS), 3),
                      "line": line})
            else:
                emit({"type": "log", "process_id": pid, "line": line})
        code = proc.wait()
    if not out.exists():
        emit({"type": "failed", "process_id": pid, "error": f"ml.run завершился с кодом {code}, файла ответа нет (см. ml_run.log)"})
        return
    emit({"type": "progress", "process_id": pid, "message": "картинки страниц-доказательств", "progress": 0.92})
    try:
        rendered, total = prerender(job, out)
        emit({"type": "log", "process_id": pid, "line": f"  картинки страниц: новых {rendered}, всего страниц-доказательств {total}"})
    except Exception as e:
        rendered = 0
        emit({"type": "log", "process_id": pid, "line": f"  картинки страниц: ошибка {e}"})
    emit({"type": "done", "process_id": pid, "exit_code": code, "submission": str(out),
          "integrity": str(out_dir / f"integrity_{object_id}.json"), "model_version": ML_VERSION,
          "duration": round(time.time() - t0), "rendered": rendered})


def _stdout_emit(evt):
    sys.stdout.write(json.dumps(evt, ensure_ascii=False) + "\n")
    sys.stdout.flush()


# ---------- REST для синхронных вызовов ----------

class Handler(BaseHTTPRequestHandler):
    def _send(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            self._send(200, {"status": "ok", "service": "inspector-ml-worker", "version": ML_VERSION,
                             "ocr_cache_files": CACHE_FILES})
        else:
            self._send(404, {"ok": False, "error": "нет такого пути"})

    def do_POST(self):
        try:
            data = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            if self.path == "/render":
                from ml.render import render
                w, h = render(data["pdf"], int(data["page"]), data["out"], int(data.get("max") or 2000), data.get("region"))
                self._send(200, {"ok": True, "width": w, "height": h})
            elif self.path == "/protocol":
                from ml.protocol import build
                build(data["payload"], data["out"], data.get("format", "pdf"))
                self._send(200, {"ok": True})
            else:
                self._send(404, {"ok": False, "error": "нет такого пути"})
        except Exception as e:
            traceback.print_exc()
            self._send(500, {"ok": False, "error": str(e)})

    def log_message(self, fmt, *args):             # журнал запросов — в stdout одной строкой JSON
        print(json.dumps({"timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"), "level": "INFO", "service": "inspector-ml-worker",
                          "message": fmt % args, "request_id": self.headers.get("X-Request-Id"), "user_id": None},
                         ensure_ascii=False), flush=True)


def serve_http(port):
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"REST воркера: http://0.0.0.0:{port}", flush=True)
    server.serve_forever()


# ---------- RabbitMQ ----------

def serve_amqp(url, jobs_queue, events_queue):
    import pika
    while True:
        try:
            # heartbeat выключен: задача идёт минуты внутри обработчика, а BlockingConnection в это время не отвечает
            # на heartbeat; сеть — внутренняя сеть docker-compose
            params = pika.URLParameters(url)
            params.heartbeat = 0
            conn = pika.BlockingConnection(params)
            ch = conn.channel()
            ch.queue_declare(queue=jobs_queue, durable=True)
            ch.queue_declare(queue=events_queue, durable=True)
            ch.basic_qos(prefetch_count=1)             # по одной задаче на воркер; параллельность — числом воркеров

            def emit(evt):
                ch.basic_publish(exchange="", routing_key=events_queue, body=json.dumps(evt, ensure_ascii=False).encode("utf-8"),
                                 properties=pika.BasicProperties(delivery_mode=2, content_type="application/json"))

            def on_job(channel, method, _props, body):
                job = json.loads(body)
                try:
                    run_job(job, emit)
                except Exception as e:
                    traceback.print_exc()
                    emit({"type": "failed", "process_id": job.get("process_id"), "error": f"воркер: {e}"})
                channel.basic_ack(delivery_tag=method.delivery_tag)

            ch.basic_consume(queue=jobs_queue, on_message_callback=on_job)
            print(f"RabbitMQ: жду задачи в {jobs_queue}", flush=True)
            ch.start_consuming()
        except Exception as e:
            print(f"RabbitMQ: {e}; переподключение через 5 с", file=sys.stderr, flush=True)
            time.sleep(5)


def main(argv):
    if len(argv) >= 2 and argv[0] == "job":
        job = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
        try:
            run_job(job, _stdout_emit)
        except Exception as e:
            traceback.print_exc()
            _stdout_emit({"type": "failed", "process_id": job.get("process_id"), "error": str(e)})
        return 0
    if argv and argv[0] == "serve":
        global CACHE_FILES
        cache = Path(os.environ.get("INSPECTOR_CACHE") or ROOT / "out" / "cache")
        CACHE_FILES = sum(1 for p in cache.rglob("*.json")) if cache.is_dir() else 0
        print(f"кэш распознанных страниц {cache}: {CACHE_FILES} файлов"
              + ("" if CACHE_FILES else " — сканы будут читаться только текстовым слоем (архив inspector_ml_assets.zip)"),
              flush=True)
        port = int(os.environ.get("WORKER_PORT", 8000))
        url = os.environ.get("AMQP_URL")
        if url:
            threading.Thread(target=serve_http, args=(port,), daemon=True).start()
            serve_amqp(url, os.environ.get("AMQP_JOBS_QUEUE", "inspector.jobs"), os.environ.get("AMQP_EVENTS_QUEUE", "inspector.events"))
        else:
            serve_http(port)
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
