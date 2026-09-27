"""Собирает colab/colab_strips.ipynb — дешёвый проход OCR по полосам страниц ИД (см. make_strips.py).

Распознавание идёт порциями примерно по 10 минут, каждая порция — своя ячейка, которая в конце печатает
свой результат в вывод. После Ctrl+S вывод хранится в .ipynb на компьютере, поэтому обрыв связи с Colab
стоит максимум одной незаконченной порции. (21.09 одна ячейка на все 10 342 полосы оборвалась на 3000,
22.09 порция на 1472 полосы — на 1000; результат пропадал вместе с машиной Colab, файл лежал только
на её временном диске.)

Список порций строится из index.jsonl архива strips_bundle.zip за вычетом уже забранных полос.

    python colab/build_strips_notebook.py
"""
import json
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
BUNDLE = HERE / "strips_bundle.zip"
PORTION = 650           # полос: при ~0.8 с/полоса на T4 это ~10 минут.
# Сессия Colab в VS Code рвётся через 15–30 минут, и незаконченная порция пропадает целиком,
# поэтому порции короткие. Уже забранные полосы (out/strips/results.jsonl) в ноутбук не попадают.

SHORT = {"OBJ-NOVOSLOBODSKAYA": "Новослободская", "OBJ-RECHNIKOV-7-7": "Речников"}


def done_strips():
    """Полосы, которые уже забраны с прошлых запусков: заново их гонять не нужно."""
    path = HERE.parent / "out" / "strips" / "results.jsonl"
    if not path.exists():
        return set()
    out = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        out.add((r["file_id"], r["page"]))
    return out


def portions():
    """[(начало, конец, подпись)] — порции ровно по PORTION полос: полосы независимы, резать можно где угодно."""
    idx = [json.loads(l) for l in zipfile.ZipFile(BUNDLE).read("index.jsonl").decode("utf-8").splitlines()]
    done = done_strips()
    todo = [i for i, r in enumerate(idx) if (r["file_id"], r["page"]) not in done]
    out, start = [], None
    for k, i in enumerate(todo):
        if start is None:
            start = i
        last = k == len(todo) - 1
        gap = last or todo[k + 1] != i + 1                      # разрыв: дальше уже забранные полосы
        if gap or i - start + 1 >= PORTION:
            obj = SHORT.get(idx[start]["object_id"], idx[start]["object_id"])
            out.append((start, i + 1,
                        f"{obj}: {idx[start]['file_id']} стр.{idx[start]['page']} – "
                        f"{idx[i]['file_id']} стр.{idx[i]['page']}, {i - start + 1} полос"))
            start = None
    return out


HEADER = """# Дешёвый проход OCR по ИД (полосы страниц)

1. Ядро: Colab, среда **T4 GPU**.
2. Ячейка 2: вставь ссылку Google Drive на `strips_bundle.zip` (доступ «все, у кого есть ссылка»).
3. Запусти ячейки 1–3, потом **порции по очереди**. Каждая порция идёт около 10 минут и в конце сама
   печатает свой результат.
4. **После каждой порции сохраняй ноутбук (Ctrl+S).** Готовые порции тогда хранятся у тебя на компьютере.

Если связь с Colab оборвалась: подключись заново, запусти ячейки 2 и 3 и продолжи с первой порции, у которой
нет строки «порция готова». Готовые порции перезапускать не нужно.
Если ноутбук поменялся на диске — перезагрузи его в VS Code, иначе выполнится старая версия ячеек."""

DIAG = """# 1. Диагностика: есть ли GPU
import subprocess, sys
print("python", sys.version.split()[0])
print(subprocess.run(["nvidia-smi", "-L"], capture_output=True, text=True).stdout or "GPU не найдена")"""

DATA = """# 2. Данные: strips_bundle.zip (чёрно-белые PNG-полосы + index.jsonl)
import json, os, re, shutil, subprocess, zipfile
BUNDLE = ""   # ссылка «Поделиться» на Google Drive или только ID из неё

NAME = "strips_bundle.zip"

def is_strips(path):
    # тот ли это архив: в strips_bundle.zip есть index.jsonl, в старом colab_bundle.zip его нет
    return zipfile.is_zipfile(path) and "index.jsonl" in zipfile.ZipFile(path).namelist()

if os.path.exists(NAME) and not is_strips(NAME):
    os.remove(NAME)                      # неудачная загрузка или не тот архив
    shutil.rmtree("bundle", ignore_errors=True)
if not os.path.exists(NAME):
    subprocess.run(["pip", "install", "-q", "gdown"], check=True)
    import gdown
    m = re.search(r"/d/([\\w-]+)|[?&]id=([\\w-]+)", BUNDLE)
    file_id = (m.group(1) or m.group(2)) if m else BUNDLE.strip()
    print("Drive ID:", file_id)
    gdown.download(id=file_id, output=NAME, quiet=False)
    if not is_strips(NAME):
        size = os.path.getsize(NAME) / 2**20 if os.path.exists(NAME) else 0
        os.remove(NAME)
        raise SystemExit(f"По ссылке не тот файл ({size:.0f} МБ): нужен strips_bundle.zip, около 115 МБ. "
                         "Загрузи его на Drive, открой доступ по ссылке и вставь новую ссылку в BUNDLE.")
if not os.path.exists("bundle/index.jsonl"):
    shutil.rmtree("bundle", ignore_errors=True)      # остатки распаковки другого архива
    zipfile.ZipFile(NAME).extractall("bundle")
index = [json.loads(l) for l in open("bundle/index.jsonl", encoding="utf-8")]
print("полос:", len(index))"""

MODEL = """# 3. EasyOCR на GPU и функция одной порции
import base64, hashlib, io, json, subprocess, time, zipfile
subprocess.run(["pip", "install", "-q", "easyocr"], check=True)
import cv2, easyocr, torch
reader = easyocr.Reader(["ru", "en"], gpu=torch.cuda.is_available(), verbose=False)
print("модель загружена, GPU:", torch.cuda.is_available())

MAX_BOXES = 400        # штриховка на схемах даёт тысячи рамок: такие полосы не распознаём

def ocr_strip(r):
    img = cv2.imread(f"bundle/{r['img']}", cv2.IMREAD_GRAYSCALE)
    rec = {"file_id": r["file_id"], "page": r["page"]}
    if img is None or img.min() > 200:               # пустая полоса
        rec["tokens"] = []
        return rec
    horiz, free = reader.detect(img)
    horiz, free = horiz[0], free[0]
    if len(horiz) + len(free) > MAX_BOXES:
        rec["tokens"], rec["skipped_boxes"] = [], len(horiz) + len(free)
        return rec
    rec["tokens"] = [[t, int(min(p[0] for p in b)), int(min(p[1] for p in b)),
                      int(max(p[0] for p in b)), int(max(p[1] for p in b)), round(float(c), 2)]
                     for b, t, c in reader.recognize(img, horiz, free)]
    return rec

def run_portion(start, end):
    rows, lines, t0 = index[start:end], [], time.time()
    for i, r in enumerate(rows, 1):
        lines.append(json.dumps(ocr_strip(r), ensure_ascii=False))
        if i % 100 == 0 or i == len(rows):
            rate = (time.time() - t0) / i
            print(f"{i}/{len(rows)}  {rate:.2f} с/полоса, осталось ~{rate * (len(rows) - i) / 60:.0f} мин", flush=True)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        z.writestr("results.jsonl", "\\n".join(lines) + "\\n")
    data = buf.getvalue()
    b64 = base64.b64encode(data).decode()
    print(f"===STRIPS_BEGIN part={start} sha256={hashlib.sha256(data).hexdigest()}===")
    print("\\n".join(b64[i:i + 4000] for i in range(0, len(b64), 4000)))
    print("===STRIPS_END===")
    print(f"порция готова: {len(rows)} полос за {(time.time() - t0) / 60:.0f} мин. Сохрани ноутбук (Ctrl+S).")"""


def main():
    cells = [("markdown", HEADER), ("code", DIAG), ("code", DATA), ("code", MODEL)]
    for n, (start, end, label) in enumerate(portions(), 1):
        cells.append(("code", f"# Порция {n} — {label}\nrun_portion({start}, {end})"))
    nb = {
        "nbformat": 4, "nbformat_minor": 5,
        "metadata": {"kernelspec": {"name": "python3", "display_name": "Python 3"},
                     "language_info": {"name": "python"}, "accelerator": "GPU"},
        "cells": [],
    }
    for i, (kind, src) in enumerate(cells):
        cell = {"cell_type": kind, "id": f"s{i}", "metadata": {}, "source": src.splitlines(keepends=True)}
        if kind == "code":
            cell.update({"execution_count": None, "outputs": []})
        nb["cells"].append(cell)
    out = HERE / "colab_strips.ipynb"
    out.write_text(json.dumps(nb, ensure_ascii=False, indent=1), encoding="utf-8")
    print(out)
    for c in cells[4:]:
        print("  ", c[1].splitlines()[0])


if __name__ == "__main__":
    main()
