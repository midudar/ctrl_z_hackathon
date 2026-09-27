"""Полосы верхней части страниц ИД для дешёвого прохода OCR в Colab → colab/strips_bundle.zip.

Зачем: у Речникова ИД — 8 324 страницы сканов (5 ГБ PDF). Полный OCR всего подряд слишком долгий, а
грузить 5 ГБ в Colab неудобно. Тип страницы (бланк акта, реестр, документ о качестве, сертификат, схема)
виден по заголовку в верхней части, поэтому сначала распознаём только верхние 40% каждой страницы
в низком разрешении, а полный OCR потом делаем лишь для нужных страниц.

Новослободская (обучающая) идёт первой: у её актов есть текстовый слой, на нём проверяется, насколько
точно по полосе определяется тип страницы. Правила по Речникову не подбираются.

    python colab/make_strips.py            # оба объекта
"""
import io
import json
import sys
import time
import zipfile
from pathlib import Path

import cv2
import numpy as np
import pymupdf
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ml import paths                      # noqa: E402
from ml.acts import id_files              # noqa: E402
from ml.pages import file_path            # noqa: E402

OBJECTS = ["OBJ-NOVOSLOBODSKAYA", paths.TEST_OBJECT]
# Верхняя доля страницы. «Реестр приложений» стоит на ~9% высоты, «АКТ освидетельствования скрытых работ»
# на первой странице бланка — на ~38%, поэтому 40%.
STRIP_FRAC = 0.40
# При 90 dpi (JPEG) «Реестр приложений» не распознавался; 120 dpi в чёрно-белом PNG читается и весит
# 5–12 КБ на полосу против 40–60 КБ у JPEG.
DPI = 120


def png_1bit(pix):
    """Серая полоса → чёрно-белая (порог Оцу) → PNG 1 бит."""
    img = np.frombuffer(pix.samples, np.uint8).reshape(pix.h, pix.w)
    _, bw = cv2.threshold(img, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    buf = io.BytesIO()
    Image.fromarray(bw).convert("1").save(buf, "PNG", optimize=True)
    return buf.getvalue()


OUT = Path(__file__).resolve().parent / "strips_bundle.zip"


def main():
    t0 = time.time()
    n = 0
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_STORED) as z:      # PNG уже сжат
        index = io.StringIO()
        for object_id in OBJECTS:
            files = id_files(object_id)
            for k, file_id in enumerate(files, 1):
                with pymupdf.open(file_path(file_id)) as doc:
                    for pno, page in enumerate(doc, start=1):
                        r = page.rect
                        clip = pymupdf.Rect(r.x0, r.y0, r.x1, r.y0 + r.height * STRIP_FRAC)
                        pix = page.get_pixmap(dpi=DPI, clip=clip, colorspace=pymupdf.csGRAY)
                        name = f"strips/{file_id}_{pno:04d}.png"
                        z.writestr(name, png_1bit(pix))
                        index.write(json.dumps({"object_id": object_id, "file_id": file_id, "page": pno,
                                                "img": name, "page_w": round(r.width, 1),
                                                "page_h": round(r.height, 1), "rotation": page.rotation,
                                                "img_w": pix.w, "img_h": pix.h,
                                                "has_text": len(page.get_text().strip()) >= 80},
                                               ensure_ascii=False) + "\n")
                        n += 1
                print(f"{object_id} {k}/{len(files)} {file_id}: всего полос {n}, {time.time() - t0:.0f} с",
                      flush=True)
        z.writestr("index.jsonl", index.getvalue())
    print(f"готово: {OUT} — {n} полос, {OUT.stat().st_size / 2**20:.0f} МБ, {time.time() - t0:.0f} с")


if __name__ == "__main__":
    main()
