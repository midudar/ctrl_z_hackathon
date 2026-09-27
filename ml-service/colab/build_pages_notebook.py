"""Собирает colab/colab_pages.ipynb — второй проход OCR по ИД: целые страницы из pages_bundle.zip (make_pages.py).

Устроен как ноутбук полос (build_strips_notebook.py): порции по ~10 минут, каждая порция — своя ячейка,
которая в конце печатает свой результат в вывод; после Ctrl+S он хранится в .ipynb на компьютере, и обрыв
связи с Colab стоит максимум одной незаконченной порции. Забирает результат colab/fetch_pages.py.

OCR страницы — код из colab/pages_ocr.py, он вставляется в ячейку 3 целиком (тот же, что проверяется локально).
Страницы, для которых кэш OCR уже есть (out/cache/…_ocr.json), в порции не попадают.

    python colab/build_pages_notebook.py
"""
import json
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from ml.pages import has_ocr             # noqa: E402

BUNDLE = HERE / "pages_bundle.zip"
PORTION = 150           # страниц: при 2–4 с на страницу на T4 это 5–10 минут

HEADER = """# Второй проход OCR по ИД: целые страницы бланков актов и реестров

1. Ядро: Colab, среда **T4 GPU** — так же, как для полос.
2. Ячейка 2: вставь ссылку Google Drive на `pages_bundle.zip` (доступ «все, у кого есть ссылка»).
3. Запусти ячейки 1–3, потом **порции по очереди**. Порция — до 150 страниц, около 5–10 минут; в конце она
   сама печатает свой результат. По первой порции видно настоящую скорость (с/стр).
4. **После каждой порции сохраняй ноутбук (Ctrl+S).** Забрать готовые порции на компьютере:
   `python colab/fetch_pages.py` — можно после каждой порции.

Если связь с Colab оборвалась: подключись заново, запусти ячейки 2 и 3 и продолжи с первой порции, у которой
нет строки «порция готова». Готовые порции перезапускать не нужно.
Если ноутбук поменялся на диске — перезагрузи его в VS Code, иначе выполнится старая версия ячеек."""

DIAG = """# 1. Диагностика: есть ли GPU
import shutil, subprocess, sys
print("python", sys.version.split()[0])
if shutil.which("nvidia-smi"):
    print(subprocess.run(["nvidia-smi", "-L"], capture_output=True, text=True).stdout)
else:
    print("GPU НЕТ: ноутбук подключён к среде Colab без видеокарты. Смени ядро на новый сервер Colab с T4 GPU "
          "(выбор ядра → Colab → новый сервер → GPU → T4). Если T4 не дают — исчерпан бесплатный лимит GPU, "
          "он восстанавливается через несколько часов.")"""

DATA = """# 2. Данные: pages_bundle.zip (чёрно-белые PNG страниц в 200 dpi + pages_index.jsonl)
import json, os, re, shutil, subprocess, zipfile
BUNDLE = ""   # ссылка «Поделиться» на Google Drive или только ID из неё

NAME = "pages_bundle.zip"

def is_pages(path):
    # тот ли это архив: в pages_bundle.zip есть pages_index.jsonl, в strips_bundle.zip — index.jsonl
    return zipfile.is_zipfile(path) and "pages_index.jsonl" in zipfile.ZipFile(path).namelist()

if os.path.exists(NAME) and not is_pages(NAME):
    os.remove(NAME)                      # неудачная загрузка или не тот архив
    shutil.rmtree("pages_bundle", ignore_errors=True)
if not os.path.exists(NAME):
    subprocess.run(["pip", "install", "-q", "gdown"], check=True)
    import gdown
    m = re.search(r"/d/([\\w-]+)|[?&]id=([\\w-]+)", BUNDLE)
    file_id = (m.group(1) or m.group(2)) if m else BUNDLE.strip()
    print("Drive ID:", file_id)
    gdown.download(id=file_id, output=NAME, quiet=False)
    if not is_pages(NAME):
        size = os.path.getsize(NAME) / 2**20 if os.path.exists(NAME) else 0
        os.remove(NAME)
        raise SystemExit(f"По ссылке не тот файл ({size:.0f} МБ): нужен pages_bundle.zip. "
                         "Загрузи его на Drive, открой доступ по ссылке и вставь новую ссылку в BUNDLE.")
if not os.path.exists("pages_bundle/pages_index.jsonl"):
    shutil.rmtree("pages_bundle", ignore_errors=True)
    zipfile.ZipFile(NAME).extractall("pages_bundle")
index = [json.loads(l) for l in open("pages_bundle/pages_index.jsonl", encoding="utf-8")]
print("страниц:", len(index))"""

MODEL_HEAD = """# 3. EasyOCR на GPU и функция одной порции
import base64, hashlib, io, json, subprocess, time, zipfile
subprocess.run(["pip", "install", "-q", "easyocr"], check=True)
import cv2, easyocr, torch
if not torch.cuda.is_available():
    # на процессоре Colab страница идёт ~30 с, все порции — полдня: без GPU не начинаем
    raise SystemExit("GPU нет — порции не запускаю. Смени ядро на сервер Colab с T4 GPU (см. ячейку 1).")
reader = easyocr.Reader(["ru", "en"], gpu=True, verbose=False)
print("модель загружена, GPU:", torch.cuda.get_device_name(0))
"""

# Функции порции. Отдельной строкой, чтобы локальная проверка выполняла ровно этот же код.
FUNCS = (HERE / "pages_ocr.py").read_text(encoding="utf-8") + """

def ocr_row(r):
    img = cv2.imread(f"pages_bundle/{r['img']}", cv2.IMREAD_GRAYSCALE)
    rec = {k: r[k] for k in ("file_id", "page", "kind", "page_w", "page_h", "rotation")}
    rec["tokens"] = [] if img is None else ocr_image(reader, img, dpi=r["dpi"], batch_size=16)
    return rec

def run_portion(start, end):
    rows, lines, t0 = index[start:end], [], time.time()
    for i, r in enumerate(rows, 1):
        lines.append(json.dumps(ocr_row(r), ensure_ascii=False))
        if i % 25 == 0 or i == len(rows):
            rate = (time.time() - t0) / i
            print(f"{i}/{len(rows)}  {rate:.1f} с/стр, осталось ~{rate * (len(rows) - i) / 60:.0f} мин", flush=True)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        z.writestr("results.jsonl", "\\n".join(lines) + "\\n")
    data = buf.getvalue()
    b64 = base64.b64encode(data).decode()
    print(f"===PAGES_BEGIN part={start} sha256={hashlib.sha256(data).hexdigest()}===")
    print("\\n".join(b64[i:i + 4000] for i in range(0, len(b64), 4000)))
    print("===PAGES_END===")
    print(f"порция готова: {len(rows)} страниц за {(time.time() - t0) / 60:.0f} мин. Сохрани ноутбук (Ctrl+S).")
"""


def load_index(bundle=BUNDLE):
    return [json.loads(l) for l in zipfile.ZipFile(bundle).read("pages_index.jsonl").decode("utf-8").splitlines()]


def portions(idx):
    """[(начало, конец, подпись)]: подряд идущие страницы без кэша OCR, поровну, около PORTION в порции."""
    todo = [i for i, r in enumerate(idx) if not has_ocr(r["file_id"], r["page"])]
    runs = []                                                   # непрерывные куски: между ними уже распознанное
    for i in todo:
        if runs and i == runs[-1][-1] + 1:
            runs[-1].append(i)
        else:
            runs.append([i])
    out = []
    for run in runs:
        size = -(-len(run) // max(1, round(len(run) / PORTION)))  # поровну, без хвоста из пары страниц
        for s in range(0, len(run), size):
            a, b = run[s], run[min(s + size, len(run)) - 1]
            out.append((a, b + 1, f"{idx[a]['file_id']} стр.{idx[a]['page']} – "
                                  f"{idx[b]['file_id']} стр.{idx[b]['page']}, {b - a + 1} страниц"))
    return out


def saved_link(path):
    """Строка BUNDLE = "…" из уже лежащего ноутбука: вставленную ссылку при пересборке не теряем."""
    if not path.exists():
        return None
    for cell in json.loads(path.read_text(encoding="utf-8"))["cells"]:
        for line in "".join(cell["source"]).splitlines():
            if line.startswith('BUNDLE = "') and not line.startswith('BUNDLE = ""'):
                return line
    return None


def main():
    idx = load_index()
    out = HERE / "colab_pages.ipynb"
    data = DATA
    link = saved_link(out)
    if link:
        data = "\n".join(link if l.startswith("BUNDLE = ") else l for l in DATA.splitlines())
    cells = [("markdown", HEADER), ("code", DIAG), ("code", data), ("code", MODEL_HEAD + "\n" + FUNCS)]
    for n, (start, end, label) in enumerate(portions(idx), 1):
        cells.append(("code", f"# Порция {n} — {label}\nrun_portion({start}, {end})"))
    nb = {
        "nbformat": 4, "nbformat_minor": 5,
        "metadata": {"kernelspec": {"name": "python3", "display_name": "Python 3"},
                     "language_info": {"name": "python"}, "accelerator": "GPU"},
        "cells": [],
    }
    for i, (kind, src) in enumerate(cells):
        cell = {"cell_type": kind, "id": f"p{i}", "metadata": {}, "source": src.splitlines(keepends=True)}
        if kind == "code":
            cell.update({"execution_count": None, "outputs": []})
        nb["cells"].append(cell)
    out.write_text(json.dumps(nb, ensure_ascii=False, indent=1), encoding="utf-8")
    print(out, f"— страниц в архиве {len(idx)}, порций {len(cells) - 4}")
    for c in cells[4:]:
        print("  ", c[1].splitlines()[0])


if __name__ == "__main__":
    main()
