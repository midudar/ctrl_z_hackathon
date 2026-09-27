"""Забирает результаты второго прохода из сохранённого colab_pages.ipynb → кэш OCR ml.pages.

Каждая порция печатает блок `===PAGES_BEGIN part=N sha256=…===` … `===PAGES_END===`. Скрипт проверяет
контрольные суммы и пишет каждую страницу в out/cache/<sha16>/<стр>_ocr.json — в том же виде, что
ml.pages.read_page(..., ocr=True): токены текстового слоя + OCR-токены, которые с ними не пересекаются.
Дальше их сам читает трек ИД (ml.acts: текстовый слой, иначе кэш OCR). Уже лежащий кэш не перезаписывается
(--force — перезаписать). Запускать можно после каждой порции.

    python colab/fetch_pages.py
    python colab/fetch_pages.py путь/к/ноутбуку.ipynb
"""
import argparse
import base64
import hashlib
import io
import json
import re
import sys
import zipfile
from collections import Counter
from pathlib import Path

import pymupdf

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from ml import paths                                       # noqa: E402
from ml.pages import _iou, cache_path, file_path, has_ocr  # noqa: E402

NOTEBOOK = HERE / "colab_pages.ipynb"
BUNDLE = HERE / "pages_bundle.zip"
BLOCK = re.compile(r"===PAGES_BEGIN part=(\d+) sha256=([0-9a-f]{64})===\s*(.*?)\s*===PAGES_END===", re.S)


def notebook_text(path):
    nb = json.loads(Path(path).read_text(encoding="utf-8"))
    text = ""
    for cell in nb["cells"]:
        for out in cell.get("outputs", []):
            t = out.get("text", "")
            text += "".join(t) if isinstance(t, list) else t
    return text


def cache_record(rec, page):
    """Запись кэша как у ml.pages.read_page(ocr=True)."""
    text_tokens = [{"text": w[4], "bbox": [round(v, 2) for v in w[:4]], "conf": 1.0, "src": "text"}
                   for w in page.get_text("words")]
    tokens = list(text_tokens)
    for text, x0, y0, x1, y1, conf in rec["tokens"]:
        t = {"text": text, "bbox": [x0, y0, x1, y1], "conf": conf, "src": "ocr"}
        if all(_iou(t["bbox"], k["bbox"]) < 0.3 for k in text_tokens):
            tokens.append(t)
    return {"file_id": rec["file_id"], "page": rec["page"], "width": page.rect.width, "height": page.rect.height,
            "rotation": page.rotation, "ocr": True, "tokens": tokens, "ocr_source": rec.get("engine", "colab_pages")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("notebook", nargs="?", default=NOTEBOOK)
    ap.add_argument("--force", action="store_true", help="перезаписать уже лежащий кэш")
    args = ap.parse_args()

    records = {}
    for part, sha, payload in BLOCK.findall(notebook_text(args.notebook)):
        data = base64.b64decode(re.sub(r"\s+", "", payload))
        if hashlib.sha256(data).hexdigest() != sha:
            print(f"порция part={part}: контрольная сумма не совпала (вывод обрезан) — пропускаю")
            continue
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            lines = [l for l in z.read("results.jsonl").decode("utf-8").splitlines() if l.strip()]
        for line in lines:
            r = json.loads(line)
            records[(r["file_id"], r["page"])] = r
        print(f"порция part={part}: {len(lines)} страниц")

    written, kept, docs = Counter(), 0, {}
    for (file_id, pno), rec in sorted(records.items()):
        if has_ocr(file_id, pno) and not args.force:
            kept += 1
            continue
        if file_id not in docs:
            docs.clear()                                           # держим открытым один файл
            docs[file_id] = pymupdf.open(file_path(file_id))
        paths.write_json(cache_path(file_id, pno, True), cache_record(rec, docs[file_id][pno - 1]))
        written[rec.get("kind", "?")] += 1
    print(f"\nзаписано в кэш OCR: {sum(written.values())} страниц {dict(written)}; уже было: {kept}")

    if BUNDLE.exists():
        idx = [json.loads(l) for l in zipfile.ZipFile(BUNDLE).read("pages_index.jsonl").decode("utf-8").splitlines()]
        done = sum(has_ocr(r["file_id"], r["page"]) for r in idx)
        print(f"по архиву {BUNDLE.name}: распознано {done} из {len(idx)} страниц")


if __name__ == "__main__":
    main()
