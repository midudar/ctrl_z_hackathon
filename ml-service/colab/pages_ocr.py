"""Полный OCR одной страницы-картинки — общий код для второго прохода по ИД.

Этот файл целиком вставляется в ноутбук colab_pages.ipynb (build_pages_notebook.py), и его же можно
вызвать локально для проверки. Повторяет ml.pages._ocr_page: страница в 200 dpi режется на плитки
2000 px с перекрытием 200 px (EasyOCR ужимает картинки больше ~2560 px), белые плитки пропускаются,
дубли из зоны перекрытия убираются. Координаты — в пунктах PDF от левого верхнего угла, как в кэше ml.pages.
"""
TILE_PX = 2000
OVERLAP_PX = 200


def _iou(a, b):
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def ocr_image(reader, img, dpi=200, batch_size=16):
    """Серая картинка страницы (numpy, h × w) → [[текст, x0, y0, x1, y1, уверенность], ...] в пунктах PDF.

    batch_size — сколько строк распознаётся за раз: на GPU это заметно быстрее, на результат не влияет.
    """
    zoom = dpi / 72
    h, w = img.shape[:2]
    step = TILE_PX - OVERLAP_PX
    tokens = []
    for ty in range(0, h, step):
        for tx in range(0, w, step):
            tile = img[ty:ty + TILE_PX, tx:tx + TILE_PX]
            if tile.size == 0 or tile.min() > 200:          # на плитке нет ничего тёмного
                continue
            for box, text, conf in reader.readtext(tile, batch_size=batch_size):
                xs = [p[0] for p in box]
                ys = [p[1] for p in box]
                tokens.append([text, round((tx + min(xs)) / zoom, 2), round((ty + min(ys)) / zoom, 2),
                               round((tx + max(xs)) / zoom, 2), round((ty + max(ys)) / zoom, 2),
                               round(float(conf), 3)])
    # дубли из зоны перекрытия плиток: оставляем более уверенный
    tokens.sort(key=lambda t: -t[5])
    kept = []
    for t in tokens:
        if all(_iou(t[1:5], k[1:5]) < 0.5 for k in kept):
            kept.append(t)
    return kept
