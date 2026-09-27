"""Второй проход по ИД локально, Tesseract вместо EasyOCR в Colab: pages_bundle.zip → кэш OCR (out/cache).

Картинки те же, что уходили в Colab (make_pages.py: целые страницы, 200 dpi, ч/б, видимая страница), OCR —
`ml.pages.tesseract_image` в несколько процессов (~5 с на страницу на процесс). Запись кэша — `ml.pages.ocr_record`:
токены-строки + текст в порядке чтения (`ocr_text`, его берёт ml.acts). Уже лежащий кэш **перезаписывается**
(прежний EasyOCR-кэш сохранён в out/cache_easyocr_backup, 24.09).

    python colab/tess_pages.py [--workers 4] [--limit N]
"""
import argparse
import io
import json
import sys
import time
import zipfile
from multiprocessing import Pool
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
BUNDLE = HERE / "pages_bundle.zip"

_zip = None


def _ocr(item):
    global _zip
    import numpy as np
    from PIL import Image
    from ml.pages import tesseract_image
    if _zip is None:
        _zip = zipfile.ZipFile(BUNDLE)
    img = np.array(Image.open(io.BytesIO(_zip.read(item["img"]))).convert("L"))
    tokens, text = tesseract_image(img, dpi=item["dpi"])
    return item, tokens, text


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    import os
    os.environ.setdefault("OMP_THREAD_LIMIT", "1")      # один поток Tesseract на процесс, процессов — несколько
    import pymupdf
    from ml import paths
    from ml.pages import cache_path, file_path, ocr_record

    with zipfile.ZipFile(BUNDLE) as z:
        name = next(n for n in z.namelist() if n.endswith(".jsonl"))
        items = [json.loads(l) for l in z.read(name).decode("utf-8").splitlines() if l.strip()]
    if args.limit:
        items = items[:args.limit]
    print(f"страниц: {len(items)}, процессов: {args.workers}", flush=True)
    docs, t0 = {}, time.time()
    with Pool(args.workers) as pool:
        for i, (item, tokens, text) in enumerate(pool.imap_unordered(_ocr, items), 1):
            f, pno = item["file_id"], item["page"]
            if f not in docs:
                docs[f] = pymupdf.open(file_path(f))
            rec = ocr_record(f, pno, docs[f][pno - 1], tokens, text, "tesseract")
            path = cache_path(f, pno, True)
            path.parent.mkdir(parents=True, exist_ok=True)
            paths.write_json(path, rec)
            if i % 50 == 0 or i == len(items):
                el = time.time() - t0
                print(f"  {i}/{len(items)} за {el:.0f} с, осталось ~{el / i * (len(items) - i) / 60:.0f} мин", flush=True)


if __name__ == "__main__":
    main()
