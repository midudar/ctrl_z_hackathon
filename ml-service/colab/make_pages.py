"""Второй проход OCR по ИД: целые страницы нужных типов → colab/pages_bundle.zip для полного OCR в Colab.

Первый проход (make_strips.py) распознал только верхние 40% страниц и дал тип каждой страницы
(ml/page_kinds.py). Для сравнения с РД нужны поля бланка акта — вид работ, шифр РД, материалы — и классы
из реестра приложений, а они лежат ниже полосы или читаются в полосе с ошибками. Поэтому здесь берутся
только бланки актов и реестры (с --quality ещё документы о качестве бетонной смеси) — около 16% страниц —
и рендерятся целиком в 200 dpi, как в ml.pages. Страница без полосы из первого прохода не берётся.

Картинки — чёрно-белые PNG (порог Оцу): 50–90 КБ на страницу против 350–780 КБ у серого JPEG. Замер 23.09
на скане документа о качестве Новослободской (F0001, стр. 5): в обоих вариантах прочитаны «БСТ В15П4…»,
«В15», «П4», номер партии; средняя уверенность 0.81 против 0.82 у серого.

После бланка акта берётся и следующая страница, если по полосе она «прочее» или пустая: на второй странице
бланка стоит поле 3 «При выполнении работ применены», а в полосе оно читается так плохо («Tlput #ыItt:енин»),
что тип не определяется. Сертификаты, схемы и протоколы после бланка — уже приложения, их не берём.

    python colab/make_pages.py                          # Речников, бланки актов и реестры
    python colab/make_pages.py --quality                # + документы о качестве бетонной смеси
    python colab/make_pages.py OBJ-NOVOSLOBODSKAYA --limit 3 --out путь.zip
"""
import argparse
import io
import json
import re
import sys
import time
import zipfile
from pathlib import Path

import cv2
import numpy as np
import pymupdf
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ml import page_kinds, paths          # noqa: E402
from ml.acts import id_files              # noqa: E402
from ml.pages import OCR_DPI, file_path   # noqa: E402

OUT = Path(__file__).resolve().parent / "pages_bundle.zip"
# Трек ИД пока сравнивает только классы бетона и арматуры. Фасады, электрика, отделка, кладка и металл — позже.
SKIP_NAME_RE = re.compile(r"нвф|электр|отделк|кладк|металл|\bэо\b|сэс", re.I)


def png_1bit(pix):
    """Серая страница → чёрно-белая (порог Оцу) → PNG 1 бит."""
    img = np.frombuffer(pix.samples, np.uint8).reshape(pix.h, pix.w)
    _, bw = cv2.threshold(img, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    buf = io.BytesIO()
    Image.fromarray(bw).convert("1").save(buf, "PNG", optimize=True)
    return buf.getvalue()


def selected_pages(object_id, kinds, limit=None):
    """[(file_id, страница, тип)] в порядке файлов и страниц."""
    strips = page_kinds.load_strips()
    manifest = {r["file_id"]: r for r in paths.read_jsonl(paths.MANIFEST)}
    out = []
    for file_id in id_files(object_id):
        name = manifest[file_id]["relative_path"].split("/")[-1]
        if SKIP_NAME_RE.search(name):
            continue
        n = manifest[file_id]["pdf_pages"] or 0
        kind = {p: page_kinds.kind_of(strips[(file_id, p)]) for p in range(1, n + 1) if (file_id, p) in strips}
        chosen = {}
        for p, k in kind.items():
            if k in kinds:
                chosen[p] = k
                if k == "act" and p + 1 <= n and kind.get(p + 1, "other") in ("other", "empty"):
                    chosen.setdefault(p + 1, "act_next")
        for p in sorted(chosen):
            # «act_next» мог стать настоящим типом, если сам попал в выборку
            out.append((file_id, p, kind.get(p) if kind.get(p) in kinds else chosen[p]))
    return out[:limit] if limit else out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("object_id", nargs="?", default=paths.TEST_OBJECT)
    ap.add_argument("--quality", action="store_true", help="добавить документы о качестве бетонной смеси")
    ap.add_argument("--limit", type=int, help="только первые N страниц (проверка)")
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()
    kinds = {"act", "register"} | ({"quality"} if args.quality else set())

    pages = selected_pages(args.object_id, kinds, args.limit)
    t0, docs = time.time(), {}
    with zipfile.ZipFile(args.out, "w", zipfile.ZIP_STORED) as z:          # PNG уже сжат
        index = io.StringIO()
        for i, (file_id, pno, kind) in enumerate(pages, 1):
            if file_id not in docs:
                docs.clear()                                                 # держим открытым один файл
                docs[file_id] = pymupdf.open(file_path(file_id))
            page = docs[file_id][pno - 1]
            pix = page.get_pixmap(dpi=OCR_DPI, colorspace=pymupdf.csGRAY)
            name = f"pages/{file_id}_{pno:04d}.png"
            z.writestr(name, png_1bit(pix))
            index.write(json.dumps({"object_id": args.object_id, "file_id": file_id, "page": pno, "kind": kind,
                                    "img": name, "dpi": OCR_DPI, "page_w": round(page.rect.width, 2),
                                    "page_h": round(page.rect.height, 2), "rotation": page.rotation,
                                    "img_w": pix.w, "img_h": pix.h}, ensure_ascii=False) + "\n")
            if i % 100 == 0 or i == len(pages):
                print(f"{i}/{len(pages)} {file_id} стр.{pno}, {time.time() - t0:.0f} с", flush=True)
        z.writestr("pages_index.jsonl", index.getvalue())
    by_kind = {}
    for _, _, k in pages:
        by_kind[k] = by_kind.get(k, 0) + 1
    print(f"готово: {args.out} — {len(pages)} страниц {by_kind}, {args.out.stat().st_size / 2**20:.0f} МБ, "
          f"{time.time() - t0:.0f} с")


if __name__ == "__main__":
    main()
