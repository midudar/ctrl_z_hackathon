"""Картинки страниц PDF для интерфейса и протокола.

    python -m ml.render <файл.pdf> <стр> <out.png> [--max 2000] [--region x0,y0,x1,y1]

Страница рисуется такой, какой её видит человек (CropBox и /Rotate учтены), поэтому рамки доказательств `bbox_norm`
(доли видимой страницы, начало — левый верхний угол) накладываются на картинку простым умножением на её размер.
`--region` — только часть страницы в тех же долях: на листах A0 целая страница в 2000 px нечитаема, а область вокруг
рамки — читается.
"""
import argparse
import os
import sys

import pymupdf

MAX_DPI = 200


def open_pdf(path):
    path = str(path)
    # Windows без префикса \\?\ не открывает пути длиннее 260 символов
    if sys.platform == "win32" and len(path) >= 260 and not path.startswith("\\\\?\\"):
        path = "\\\\?\\" + os.path.abspath(path)
    return pymupdf.open(path)


def region_rect(page, region):
    """Доли видимой страницы → прямоугольник в координатах видимой страницы (в них же PyMuPDF ждёт clip)."""
    w, h = page.rect.width, page.rect.height
    x0, y0, x1, y1 = (max(0.0, min(1.0, v)) for v in region)
    return pymupdf.Rect(x0 * w, y0 * h, x1 * w, y1 * h)


def pixmap(page, max_px, region=None):
    area = region_rect(page, region) if region else page.rect
    zoom = min(max_px / max(area.width, area.height), MAX_DPI / 72)
    return page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=area if region else None, alpha=False)


def render(pdf, page_no, out, max_px=2000, region=None):
    doc = open_pdf(pdf)
    try:
        if not 1 <= page_no <= doc.page_count:
            raise ValueError(f"в файле {doc.page_count} стр., запрошена {page_no}")
        pix = pixmap(doc[page_no - 1], max_px, region)
        os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
        tmp = f"{out}.{os.getpid()}.tmp"
        pix.save(tmp, output="png")
        os.replace(tmp, out)                       # атомарно: параллельный читатель не увидит половину файла
        return pix.width, pix.height
    finally:
        doc.close()


def context_region(bbox, min_w=0.28, min_h=0.2, grow=3.0):
    """Область вокруг рамки: рамка в центре, запас по сторонам (для карточек протокола и интерфейса)."""
    x0, y0, x1, y1 = bbox
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    w = min(1.0, max(min_w, (x1 - x0) * grow))
    h = min(1.0, max(min_h, (y1 - y0) * grow))
    left = min(max(0.0, cx - w / 2), 1.0 - w)
    top = min(max(0.0, cy - h / 2), 1.0 - h)
    return [left, top, left + w, top + h]


def main(argv):
    ap = argparse.ArgumentParser(description="Картинка страницы PDF")
    ap.add_argument("pdf")
    ap.add_argument("page", type=int)
    ap.add_argument("out")
    ap.add_argument("--max", type=int, default=2000, help="длинная сторона, пикселей")
    ap.add_argument("--region", help="x0,y0,x1,y1 в долях видимой страницы")
    a = ap.parse_args(argv)
    region = [float(v) for v in a.region.split(",")] if a.region else None
    w, h = render(a.pdf, a.page, a.out, a.max, region)
    print(f"{a.out}: {w}×{h}")


if __name__ == "__main__":
    main(sys.argv[1:])
