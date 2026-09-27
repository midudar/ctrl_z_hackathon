"""Собирает colab/colab_ocr.ipynb. Логика OCR повторяет ml/pages.py один в один, чтобы кэш был совместим."""
import json
from pathlib import Path

CELLS = [
    ("markdown", """# OCR чертежей в Colab (GPU)

1. Ядро: Colab, среда **T4 GPU**.
2. Ячейка 1 — диагностика. Запусти её первой и сохрани ноутбук (Ctrl+S): Claude прочитает вывод.
3. Ячейки 2–5 — данные, модель, OCR, выгрузка `cache.zip`.
4. `cache.zip` распаковать в `D:\\hakaton\\ltc\\out\\cache\\`."""),

    ("code", """# 1. Диагностика: есть ли GPU и как можно передавать файлы
import os, subprocess, sys
print("python", sys.version.split()[0])
print(subprocess.run(["nvidia-smi", "-L"], capture_output=True, text=True).stdout or "GPU не найдена")
try:
    import google.colab
    print("google.colab: есть")
except ImportError:
    print("google.colab: нет (это не среда Colab)")
print("cwd:", os.getcwd())
print("файлы:", sorted(os.listdir("."))[:20])
try:
    import torch
    print("torch", torch.__version__, "cuda:", torch.cuda.is_available())
except ImportError:
    print("torch не установлен")"""),

    ("code", """# 2. Данные: colab_bundle.zip (PDF + jobs.json)
# Способы по порядку: (а) файл уже лежит в текущей папке; (б) ссылка на Google Drive (BUNDLE_ID);
# (в) Google Drive, файл MyDrive/colab_bundle.zip.
import os, re, shutil, subprocess, zipfile
BUNDLE_ID = ""   # ссылка «Поделиться» на Drive или только ID из неё

if os.path.exists("colab_bundle.zip") and not zipfile.is_zipfile("colab_bundle.zip"):
    os.remove("colab_bundle.zip")          # остаток неудачной загрузки
if not os.path.exists("colab_bundle.zip"):
    if BUNDLE_ID:
        subprocess.run(["pip", "install", "-q", "gdown"], check=True)
        import gdown
        # принимаем и полную ссылку «Поделиться», и голый ID
        m = re.search(r"/d/([\\w-]+)|[?&]id=([\\w-]+)", BUNDLE_ID)
        file_id = (m.group(1) or m.group(2)) if m else BUNDLE_ID.strip()
        print("Drive ID:", file_id)
        gdown.download(id=file_id, output="colab_bundle.zip", quiet=False)
    else:
        from google.colab import drive
        drive.mount("/content/drive")
        shutil.copy("/content/drive/MyDrive/colab_bundle.zip", "colab_bundle.zip")
zipfile.ZipFile("colab_bundle.zip").extractall("bundle")
print(sorted(os.listdir("bundle")))"""),

    ("code", """# 3. EasyOCR на GPU
import subprocess
subprocess.run(["pip", "install", "-q", "easyocr", "pymupdf"], check=True)
import easyocr, torch
reader = easyocr.Reader(["ru", "en"], gpu=torch.cuda.is_available(), verbose=False)
print("модель загружена, GPU:", torch.cuda.is_available())"""),

    ("code", """# 4. OCR страниц → cache/<sha16>/<стр>_ocr.json (тот же формат, что у ml/pages.py)
import json, os, time
import numpy as np
import pymupdf

OCR_DPI, TILE_PX, OVERLAP_PX = 200, 2000, 200

def iou(a, b):
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0])); iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0

def ocr_page(page):
    zoom = OCR_DPI / 72
    tokens, step = [], TILE_PX - OVERLAP_PX
    W, H = page.rect.width * zoom, page.rect.height * zoom
    for ty in range(0, int(H), step):
        for tx in range(0, int(W), step):
            clip = pymupdf.Rect(tx / zoom, ty / zoom, min(tx + TILE_PX, W) / zoom, min(ty + TILE_PX, H) / zoom)
            pix = page.get_pixmap(dpi=OCR_DPI, clip=clip, colorspace=pymupdf.csGRAY)
            img = np.frombuffer(pix.samples, np.uint8).reshape(pix.h, pix.w)
            if img.min() > 200:
                continue
            for box, text, conf in reader.readtext(img):
                xs = [p[0] for p in box]; ys = [p[1] for p in box]
                tokens.append({"text": text,
                               "bbox": [clip.x0 + min(xs) / zoom, clip.y0 + min(ys) / zoom,
                                        clip.x0 + max(xs) / zoom, clip.y0 + max(ys) / zoom],
                               "conf": round(float(conf), 3), "src": "ocr"})
    tokens.sort(key=lambda t: -t["conf"])
    kept = []
    for t in tokens:
        if all(iou(t["bbox"], k["bbox"]) < 0.5 for k in kept):
            kept.append(t)
    return kept

jobs = json.load(open("bundle/jobs.json", encoding="utf-8"))
t_all = time.time()
for job in jobs:
    doc = pymupdf.open(f"bundle/{job['file_id']}.pdf")
    for pno in job["pages"]:
        out = f"cache/{job['sha16']}/{pno:04d}_ocr.json"
        if os.path.exists(out):
            continue
        t = time.time()
        page = doc[pno - 1]
        text_tokens = [{"text": w[4], "bbox": [round(v, 2) for v in w[:4]], "conf": 1.0, "src": "text"}
                       for w in page.get_text("words")]
        tokens = list(text_tokens)
        for tk in ocr_page(page):
            if all(iou(tk["bbox"], k["bbox"]) < 0.3 for k in text_tokens):
                tokens.append(tk)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            json.dump({"file_id": job["file_id"], "page": pno, "width": page.rect.width,
                       "height": page.rect.height, "rotation": page.rotation, "ocr": True,
                       "tokens": tokens}, f, ensure_ascii=False, indent=2)
        n_ocr = sum(tk["src"] == "ocr" for tk in tokens)
        print(f"{job['file_id']} стр.{pno}: токенов {len(tokens)}, OCR {n_ocr}, {time.time() - t:.0f} с", flush=True)
print(f"готово за {time.time() - t_all:.0f} с")"""),

    ("code", """# 5. Выгрузка результата: cache.zip → скачать и распаковать в D:\\hakaton\\ltc\\out\\cache\\
import shutil
shutil.make_archive("cache", "zip", "cache")
print("cache.zip:", round(os.path.getsize("cache.zip") / 2**20, 1), "МБ")
try:
    from google.colab import files
    files.download("cache.zip")
except Exception as e:
    print("files.download не сработал:", e)
    if os.path.exists("/content/drive/MyDrive"):
        shutil.copy("cache.zip", "/content/drive/MyDrive/cache.zip")
        print("скопировано в MyDrive/cache.zip")"""),
]


def main():
    nb = {
        "nbformat": 4, "nbformat_minor": 5,
        "metadata": {"kernelspec": {"name": "python3", "display_name": "Python 3"},
                     "language_info": {"name": "python"}, "accelerator": "GPU"},
        "cells": [],
    }
    for i, (kind, src) in enumerate(CELLS):
        cell = {"cell_type": kind, "id": f"c{i}", "metadata": {}, "source": src.splitlines(keepends=True)}
        if kind == "code":
            cell.update({"execution_count": None, "outputs": []})
        nb["cells"].append(cell)
    out = Path(__file__).resolve().parent / "colab_ocr.ipynb"
    out.write_text(json.dumps(nb, ensure_ascii=False, indent=1), encoding="utf-8")
    print(out)


if __name__ == "__main__":
    main()
