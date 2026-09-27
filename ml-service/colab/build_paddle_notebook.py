"""Собирает colab/colab_paddle.ipynb — PaddleOCR на GPU Colab: замер точности, проверка на актах и 910 страниц ИД.

Зачем: локально PaddleOCR лучший по точности (0.976 на 150 строках пилота против 0.947 у Tesseract и 0.827 у
EasyOCR), но на CPU ноутбука непрактичен (~2 мин на страницу, на 200 dpi падает). На T4 он должен идти секунды.
Ноутбук:
  1. проверка GPU; 2. установка paddlepaddle-gpu + paddleocr (версии как в локальном замере);
  3. данные: pages_bundle.zip (910 страниц ИД Речникова, ссылка уже вставлена) и paddle_bench.zip (make_paddle_bench.py);
  4. модель: PP-OCRv5_server_det + eslav_PP-OCRv5_mobile_rec — как при локальном замере;
  5. замер на 600 строках пилота (печатает таблицу сразу в Colab) → блок BENCH;
  6. 81 страница 20 актов Новослободской → блок ACTS (сверка разбора с текстовым слоем — локально);
  7+. порции страниц Речникова → блоки PAGES в том же формате, что colab_pages (engine = paddle).
Результаты забирает colab/fetch_paddle.py из сохранённого (скачанного) .ipynb.

    python colab/build_paddle_notebook.py
"""
import json
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

PAGES_BUNDLE = HERE / "pages_bundle.zip"
PORTION = 230           # страниц в порции: при ~1–2 с на страницу на T4 — 5–10 минут
PAGES_LINK = ""        # ссылка «Поделиться» на pages_bundle.zip на Google Drive (или только ID) — вставить свою;
                      # в репозиторий не коммитить: архив содержит сканы документов организаторов

HEADER = """# PaddleOCR на GPU: замер точности, акты Новослободской и 910 страниц ИД Речникова

1. Среда: **T4 GPU** (Среда выполнения → Сменить среду → T4 GPU).
2. Ячейка 3: вставь ссылку Google Drive на `paddle_bench.zip` в `BENCH` (доступ «все, у кого есть ссылка»).
   Ссылка на `pages_bundle.zip` уже вставлена.
3. Запусти ячейки 1–4 по очереди (установка ~2–3 мин), потом 5 (замер, ~2 мин) и 6 (акты, ~2 мин),
   потом **порции по очереди**. Каждая порция в конце сама печатает свой результат.
4. В конце: Файл → Скачать → .ipynb, положи файл в `D:\\hakaton\\ltc\\colab\\` как `colab_paddle_done.ipynb`.
   Забрать: `python colab/fetch_paddle.py colab/colab_paddle_done.ipynb`.

Если связь оборвалась: запусти заново ячейки 1–4 и продолжи с первой порции без строки «порция готова»."""

DIAG = """# 1. Диагностика: есть ли GPU
import shutil, subprocess, sys
print("python", sys.version.split()[0])
if shutil.which("nvidia-smi"):
    print(subprocess.run(["nvidia-smi", "-L"], capture_output=True, text=True).stdout)
else:
    print("GPU НЕТ: Среда выполнения → Сменить среду → T4 GPU. Если T4 не дают — исчерпан бесплатный лимит GPU.")"""

INSTALL = """# 2. PaddlePaddle для GPU + PaddleOCR (версии как в локальном замере: paddlepaddle 3.3.1, paddleocr 3.7.0)
import subprocess, sys
def pip(*args):
    return subprocess.run([sys.executable, "-m", "pip", "install", "-q", *args]).returncode
ok = False
for cuda in ("cu126", "cu118"):
    idx = f"https://www.paddlepaddle.org.cn/packages/stable/{cuda}/"
    if pip("paddlepaddle-gpu==3.3.1", "-i", idx) == 0 or pip("paddlepaddle-gpu", "-i", idx) == 0:
        print("paddlepaddle-gpu поставлен из", cuda); ok = True; break
if not ok:
    raise SystemExit("paddlepaddle-gpu не поставился — пришли мне вывод этой ячейки")
pip("paddleocr==3.7.0")
# PaddleX при импорте трогает torch, если он установлен, а paddlepaddle-gpu откатывает NCCL под себя — и torch Colab
# падает («undefined symbol: ncclCommShrink»). Torch нам не нужен: удаляем.
subprocess.run([sys.executable, "-m", "pip", "uninstall", "-y", "-q", "torch", "torchvision", "torchaudio"])
import paddle
print("paddle", paddle.__version__, "CUDA:", paddle.device.is_compiled_with_cuda(),
      "GPU:", paddle.device.cuda.device_count())
paddle.utils.run_check()"""

DATA = """# 3. Данные
import json, os, re, shutil, subprocess, sys, zipfile
PAGES = "%s"   # ссылка на pages_bundle.zip (или только ID из неё)
BENCH = ""   # ссылка на paddle_bench.zip (или только ID из неё)

def fetch(link, name, marker):
    ok = lambda p: zipfile.is_zipfile(p) and marker in zipfile.ZipFile(p).namelist()
    if os.path.exists(name) and not ok(name):
        os.remove(name)
    if not os.path.exists(name):
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "gdown"], check=True)
        import gdown
        m = re.search(r"/d/([\\w-]+)|[?&]id=([\\w-]+)", link)
        gdown.download(id=(m.group(1) or m.group(2)) if m else link.strip(), output=name, quiet=False)
        if not ok(name):
            raise SystemExit(f"по ссылке не {name} — проверь ссылку и доступ «все, у кого есть ссылка»")
    folder = name[:-4]
    if not os.path.exists(os.path.join(folder, marker)):
        shutil.rmtree(folder, ignore_errors=True)
        zipfile.ZipFile(name).extractall(folder)
    return folder

fetch(PAGES, "pages_bundle.zip", "pages_index.jsonl")
fetch(BENCH, "paddle_bench.zip", "lines.jsonl")
index = [json.loads(l) for l in open("pages_bundle/pages_index.jsonl", encoding="utf-8")]
lines = [json.loads(l) for l in open("paddle_bench/lines.jsonl", encoding="utf-8")]
acts = [json.loads(l) for l in open("paddle_bench/acts.jsonl", encoding="utf-8")]
print("страниц ИД:", len(index), "| строк пилота:", len(lines), "| страниц актов:", len(acts))""" % PAGES_LINK

MODEL = """# 4. Модель — как в локальном замере: PP-OCRv5_server_det + eslav_PP-OCRv5_mobile_rec (русский)
import base64, hashlib, io, time, unicodedata
import numpy as np
from PIL import Image
os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"
from paddleocr import PaddleOCR
ocr = PaddleOCR(text_detection_model_name="PP-OCRv5_server_det", text_recognition_model_name="eslav_PP-OCRv5_mobile_rec",
                use_doc_orientation_classify=False, use_doc_unwarping=False, use_textline_orientation=False,
                device="gpu:0")

def paddle_tokens(img_rgb, dpi=None):
    # [(текст, x0, y0, x1, y1, уверенность)]; с dpi — рамки в точках страницы (72 dpi), иначе в пикселях
    k = 72 / dpi if dpi else 1
    out = []
    for r in ocr.predict(img_rgb):
        for text, poly, score in zip(r["rec_texts"], r["rec_polys"], r["rec_scores"]):
            xs, ys = [float(p[0]) for p in poly], [float(p[1]) for p in poly]
            out.append([text, round(min(xs) * k, 2), round(min(ys) * k, 2), round(max(xs) * k, 2),
                        round(max(ys) * k, 2), round(float(score), 3)])
    return out

def load_rgb(path):
    return np.array(Image.open(path).convert("RGB"))

def emit(tag, part, payload_lines):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        z.writestr("results.jsonl", "\\n".join(payload_lines) + "\\n")
    data = buf.getvalue()
    b64 = base64.b64encode(data).decode()
    print(f"==={tag}_BEGIN part={part} sha256={hashlib.sha256(data).hexdigest()}===")
    print("\\n".join(b64[i:i + 4000] for i in range(0, len(b64), 4000)))
    print(f"==={tag}_END===")

t = time.time(); paddle_tokens(load_rgb(f"pages_bundle/{index[0]['img']}")); print(f"прогрев: {time.time() - t:.1f} с")"""

BENCH = """# 5. Замер на 600 строках пилота (эталон — текстовый слой PDF; формула как в ml.ocr_eval: 1 − CER)
HOMO = str.maketrans("ABCEHKMOPTXYaceopxyё", "АВСЕНКМОРТХУасеорхуе")
def norm(t, h=False):
    t = " ".join(unicodedata.normalize("NFC", t or "").replace("ё", "е").replace("Ё", "Е").split())
    return t.translate(HOMO) if h else t
def lev(a, b):
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]
def prepare(path):            # как в локальном замере: 144 → 200 dpi и белое поле 12 px
    img = Image.open(path).convert("L")
    img = img.resize((round(img.width * 200 / 144), round(img.height * 200 / 144)))
    pad = Image.new("L", (img.width + 24, img.height + 24), 255); pad.paste(img, (12, 12))
    return np.array(pad.convert("RGB"))

t0, out = time.time(), []
for r in lines:
    toks = sorted(paddle_tokens(prepare(f"paddle_bench/lines/{r['line_id']}.png")), key=lambda x: x[1])
    r["paddle"] = " ".join(x[0] for x in toks)
    out.append(json.dumps({"line_id": r["line_id"], "paddle": r["paddle"]}, ensure_ascii=False))
print(f"{len(lines)} строк за {time.time() - t0:.0f} с\\n")
for n in (150, len(lines)):
    sub = lines[:n]
    for h in (False, True):
        C = sum(len(norm(r["gold"], h)) for r in sub)
        row = [f"{e} {1 - sum(lev(norm(r['gold'], h), norm(r[e], h)) for r in sub) / C:.3f}"
               for e in ("easyocr", "tesseract", "paddle")]
        print(f"строк {n:3} {'без двойников' if h else 'строго       '}: " + "   ".join(row))
exact = sum(lev(norm(r["gold"], True), norm(r["paddle"], True)) == 0 for r in lines) / len(lines)
print(f"\\nPaddle: строк без единой ошибки {exact:.2f}")
emit("BENCH", 0, out)"""

ACTS = """# 6. Акты Новослободской (обучающий объект): 81 страница как сканы — разбор сверим локально с текстовым слоем
t0, out = time.time(), []
for r in acts:
    rec = dict(r); rec["tokens"] = paddle_tokens(load_rgb(f"paddle_bench/{r['img']}"), dpi=r["dpi"]); rec["engine"] = "paddle"
    out.append(json.dumps(rec, ensure_ascii=False))
print(f"{len(acts)} страниц за {time.time() - t0:.0f} с, {(time.time() - t0) / len(acts):.1f} с/стр")
emit("ACTS", 0, out)"""

PORTION_FN = """# Функция порции страниц ИД Речникова (формат — как у colab_pages, engine = paddle)
def run_portion(start, end):
    rows, out, t0 = index[start:end], [], time.time()
    for i, r in enumerate(rows, 1):
        rec = {k: r[k] for k in ("file_id", "page", "kind", "page_w", "page_h", "rotation")}
        rec["tokens"] = paddle_tokens(load_rgb(f"pages_bundle/{r['img']}"), dpi=r["dpi"]); rec["engine"] = "paddle"
        out.append(json.dumps(rec, ensure_ascii=False))
        if i % 25 == 0 or i == len(rows):
            rate = (time.time() - t0) / i
            print(f"{i}/{len(rows)}  {rate:.1f} с/стр, осталось ~{rate * (len(rows) - i) / 60:.0f} мин", flush=True)
    emit("PAGES", start, out)
    print(f"порция готова: {len(rows)} страниц за {(time.time() - t0) / 60:.0f} мин.")"""


def main():
    n = len(zipfile.ZipFile(PAGES_BUNDLE).read("pages_index.jsonl").decode("utf-8").splitlines())
    size = -(-n // max(1, round(n / PORTION)))
    cells = [("markdown", HEADER), ("code", DIAG), ("code", INSTALL), ("code", DATA), ("code", MODEL),
             ("code", BENCH), ("code", ACTS), ("code", PORTION_FN)]
    for k, s in enumerate(range(0, n, size), 1):
        e = min(s + size, n)
        cells.append(("code", f"# Порция {k} — страницы {s + 1}–{e} из {n}\nrun_portion({s}, {e})"))
    nb = {"nbformat": 4, "nbformat_minor": 5,
          "metadata": {"kernelspec": {"name": "python3", "display_name": "Python 3"},
                       "language_info": {"name": "python"}, "accelerator": "GPU", "colab": {"gpuType": "T4"}},
          "cells": []}
    for i, (kind, src) in enumerate(cells):
        cell = {"cell_type": kind, "id": f"c{i}", "metadata": {}, "source": src.splitlines(keepends=True)}
        if kind == "code":
            cell.update({"execution_count": None, "outputs": []})
        nb["cells"].append(cell)
    out = HERE / "colab_paddle.ipynb"
    out.write_text(json.dumps(nb, ensure_ascii=False, indent=1), encoding="utf-8")
    print(out, f"— страниц {n}, порций {len(cells) - 8}")


if __name__ == "__main__":
    main()
