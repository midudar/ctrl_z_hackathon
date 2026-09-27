"""Этап 1: страница → токены {text, bbox, conf, src} с кэшем на диске.

Токены берутся из двух источников и сливаются:
- текстовый слой PDF (src="text", conf=1.0);
- OCR отрендеренной страницы (src="ocr"). Нужен не только для сканов: на чертежах РД подписи
  (марки систем, типы решёток) экспортированы из CAD линиями и в текстовом слое отсутствуют.

bbox — в пунктах PDF, начало координат в левом верхнем углу страницы (как у pymupdf).

    python -m ml.pages F0201 17-20,22-25          # OCR страниц и запись в кэш
"""
import hashlib
import json
import os
import re
import sys
import time

import numpy as np
import pymupdf

from ml import paths

CACHE = paths.CACHE
MODELS = paths.MODELS / "easyocr"
# Веса, которые нужны Reader(["ru", "en"]). Если они на месте, скачивание запрещено:
# на стенде жюри интернета может не быть, а внешние обращения запрещены.
MODEL_FILES = ("craft_mlt_25k.pth", "cyrillic_g2.pth")
OCR_DPI = 200
TILE_PX = 2000          # EasyOCR ужимает картинки больше ~2560 px, поэтому режем на плитки
OVERLAP_PX = 200

_reader = None

# Постобработка текста OCR — латинские буквы-двойники кириллицы (распознаватель их не различает):
# - внутри русского слова → кириллица («CMEСИ» → «СМЕСИ», «Бетoн» → «Бетон»);
# - слово только из букв-двойников без цифр в строке, где кириллицы больше, чем латиницы, → кириллица
#   («AKT» → «АКТ», оси «2/B-2/Г» → «2/В-2/Г»).
# Слова с цифрами и строки с преобладанием латиницы не трогаются: «B25», «A500C», «W8» бывают латиницей и в документах.
# Замер на пилоте организаторов (600 строк, строго, `python -m ml.ocr_eval --bench`): PaddleOCR 0.947 → 0.950,
# Tesseract 0.929 → 0.931, EasyOCR 0.801 → 0.823; хуже стала 1 строка из 1 800.
_LAT2CYR = str.maketrans("ABCEHKMOPTXYaceopxy", "АВСЕНКМОРТХУасеорху")
_CYR_RE = re.compile(r"[А-Яа-яЁё]")
_LAT_RE = re.compile(r"[A-Za-z]")
_LOOKALIKE_RE = re.compile(r"^[ABCEHKMOPTXYaceopxy]+$")


def fix_homoglyphs(text):
    out = []
    for line in (text or "").split("\n"):
        russian = len(_CYR_RE.findall(line)) > len(_LAT_RE.findall(line))

        def word(m):
            w = m.group(0)
            core = w.strip(".,;:()«»\"'")
            if _CYR_RE.search(w) or (russian and core and _LOOKALIKE_RE.match(core)):
                return w.translate(_LAT2CYR)
            return w
        out.append(re.sub(r"\S+", word, line))
    return "\n".join(out)


def use_gpu():
    """GPU, если он есть. INSPECTOR_OCR_GPU=0/1 переопределяет автоопределение."""
    flag = os.environ.get("INSPECTOR_OCR_GPU")
    if flag in ("0", "1"):
        return flag == "1"
    try:
        import torch
        return torch.cuda.is_available()
    except Exception:
        return False


def ocr_reader(gpu=None):
    global _reader
    if _reader is None:
        import easyocr
        gpu = use_gpu() if gpu is None else gpu
        offline = all((MODELS / f).exists() for f in MODEL_FILES)
        try:
            _reader = easyocr.Reader(["ru", "en"], gpu=gpu, model_storage_directory=str(MODELS),
                                     download_enabled=not offline, verbose=False)
        except RuntimeError as e:
            # GPU на стенде могут делить несколько команд: без памяти работаем на процессоре
            if not gpu:
                raise
            print(f"  OCR: GPU недоступен ({e}), перехожу на CPU", file=sys.stderr)
            _reader = easyocr.Reader(["ru", "en"], gpu=False, model_storage_directory=str(MODELS),
                                     download_enabled=not offline, verbose=False)
    return _reader


def _readtext(img):
    """readtext с переходом на CPU, если посреди работы кончилась видеопамять."""
    global _reader
    try:
        return ocr_reader().readtext(img)
    except RuntimeError as e:
        if "out of memory" not in str(e).lower():
            raise
        print("  OCR: кончилась видеопамять, дальше на CPU", file=sys.stderr)
        _reader = None
        return ocr_reader(gpu=False).readtext(img)


_manifest = None


def manifest():
    global _manifest
    if _manifest is None:
        _manifest = {r["file_id"]: r for r in paths.read_jsonl(paths.MANIFEST)}
    return _manifest


def file_path(file_id):
    path = paths.DOCS / manifest()[file_id]["relative_path"]
    # Windows без префикса \\?\ не открывает пути длиннее 260 символов (F0152 Тюменской — 294); на Linux не нужно
    if sys.platform == "win32" and len(str(path)) >= 260:
        path = type(path)("\\\\?\\" + str(path.resolve()))
    return path


def file_sha(file_id):
    sha = manifest()[file_id].get("sha256")
    if sha:
        return sha
    h = hashlib.sha256()
    with open(file_path(file_id), "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def _iou(a, b):
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def _ocr_page(page):
    """OCR страницы плитками. Пустые (белые) плитки пропускаются."""
    zoom = OCR_DPI / 72
    tokens = []
    step = TILE_PX - OVERLAP_PX
    W, H = page.rect.width * zoom, page.rect.height * zoom
    for ty in range(0, int(H), step):
        for tx in range(0, int(W), step):
            clip = pymupdf.Rect(tx / zoom, ty / zoom, min(tx + TILE_PX, W) / zoom, min(ty + TILE_PX, H) / zoom)
            pix = page.get_pixmap(dpi=OCR_DPI, clip=clip, colorspace=pymupdf.csGRAY)
            img = np.frombuffer(pix.samples, np.uint8).reshape(pix.h, pix.w)
            if img.min() > 200:          # на плитке нет ничего тёмного
                continue
            for box, text, conf in _readtext(img):
                xs = [p[0] for p in box]
                ys = [p[1] for p in box]
                tokens.append({
                    "text": text,
                    "bbox": [clip.x0 + min(xs) / zoom, clip.y0 + min(ys) / zoom,
                             clip.x0 + max(xs) / zoom, clip.y0 + max(ys) / zoom],
                    "conf": round(float(conf), 3),
                    "src": "ocr",
                })
    # дубли из зоны перекрытия плиток: оставляем более уверенный
    tokens.sort(key=lambda t: -t["conf"])
    kept = []
    for t in tokens:
        if all(_iou(t["bbox"], k["bbox"]) < 0.5 for k in kept):
            kept.append(t)
    return kept


# Tesseract — движок для сканов ИД (акты): на пилоте организаторов 0.94 по символам против 0.83 у EasyOCR, 4 с на
# страницу A4 на CPU (замер 24.09, CLAUDE.md «Сравнение распознавателей»). Чертежи ОВ остаются на EasyOCR.
# Модели — models/tesseract (rus из tessdata_best), бинарник — INSPECTOR_TESSERACT, иначе стандартный путь.
TESSDATA = paths.MODELS / "tesseract"
_WIN_TESSERACT = r"C:\Program Files\Tesseract-OCR\tesseract.exe"


def tesseract_bin():
    env = os.environ.get("INSPECTOR_TESSERACT")
    if env:
        return env
    return _WIN_TESSERACT if sys.platform == "win32" and os.path.exists(_WIN_TESSERACT) else "tesseract"


def tesseract_image(img, dpi=OCR_DPI, psm=3):
    """OCR картинки видимой страницы (numpy, серый или ч/б) Tesseract'ом.

    Возвращает (токены-строки с рамками в точках видимой страницы, текст в порядке чтения Tesseract). Токен — целая
    строка, как у EasyOCR, а не слово; текст идёт блоками и абзацами, так поля бланка не перемешиваются с соседней
    колонкой (разбор актов проверен именно на таком тексте)."""
    import subprocess
    import tempfile
    from PIL import Image
    fd, tmp = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    try:
        Image.fromarray(img).save(tmp)
        out = subprocess.run([tesseract_bin(), tmp, "stdout", "--tessdata-dir", str(TESSDATA), "-l", "rus+eng",
                              "--psm", str(psm), "--dpi", str(dpi), "tsv"], capture_output=True, check=True)
    finally:
        os.remove(tmp)
    k = 72 / dpi
    lines, order = {}, []
    for row in out.stdout.decode("utf-8", "replace").splitlines()[1:]:
        f = row.split("\t")
        if len(f) < 12 or f[0] != "5" or not f[11].strip():
            continue
        key = (f[2], f[3], f[4])                      # блок, абзац, строка
        x, y, w, h, conf = int(f[6]), int(f[7]), int(f[8]), int(f[9]), float(f[10])
        if key not in lines:
            lines[key] = {"words": [], "box": [x, y, x + w, y + h], "conf": []}
            order.append(key)
        ln = lines[key]
        ln["words"].append(f[11])
        ln["box"] = [min(ln["box"][0], x), min(ln["box"][1], y), max(ln["box"][2], x + w), max(ln["box"][3], y + h)]
        ln["conf"].append(max(conf, 0) / 100)
    tokens, text, prev_block = [], [], None
    for key in order:
        ln = lines[key]
        if prev_block is not None and key[:2] != prev_block:
            text.append("")                           # новый абзац — пустая строка, как в выводе Tesseract
        prev_block = key[:2]
        text.append(" ".join(ln["words"]))
        tokens.append({"text": text[-1], "bbox": [round(v * k, 2) for v in ln["box"]],
                       "conf": round(sum(ln["conf"]) / len(ln["conf"]), 3), "src": "ocr"})
    return tokens, "\n".join(text)


def binarize(img):
    """Порог Оцу — так же рендерились страницы для Colab (pages_bundle): ч/б PNG."""
    import cv2
    _, bw = cv2.threshold(img, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return bw


def tesseract_page(page, dpi=OCR_DPI):
    """OCR страницы PDF Tesseract'ом: рендер видимой страницы в dpi, ч/б, → (токены, текст)."""
    pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)
    img = np.frombuffer(pix.samples, np.uint8).reshape(pix.h, pix.w)
    return tesseract_image(binarize(img), dpi)


def ocr_record(file_id, pno, page, ocr_tokens, ocr_text, source):
    """Запись кэша OCR в формате read_page(ocr=True) + текст в порядке чтения (`ocr_text`, его берёт ml.acts)."""
    text_tokens = [{"text": w[4], "bbox": [round(v, 2) for v in w[:4]], "conf": 1.0, "src": "text"}
                   for w in page.get_text("words")]
    tokens = text_tokens + [t for t in ocr_tokens if all(_iou(t["bbox"], k["bbox"]) < 0.3 for k in text_tokens)]
    return {"file_id": file_id, "page": pno, "width": page.rect.width, "height": page.rect.height,
            "rotation": page.rotation, "ocr": True, "tokens": tokens, "ocr_text": ocr_text, "ocr_source": source}


def cache_path(file_id, pno, ocr):
    return CACHE / file_sha(file_id)[:16] / f"{pno:04d}{'_ocr' if ocr else ''}.json"


def has_ocr(file_id, pno):
    return cache_path(file_id, pno, True).exists()


def read_page(file_id, pno, ocr=False):
    """Токены страницы pno (с 1). С ocr=True добавляются OCR-токены, не дублирующие текстовый слой."""
    cache = cache_path(file_id, pno, ocr)
    if cache.exists():
        return paths.read_json(cache)
    doc = pymupdf.open(file_path(file_id))
    page = doc[pno - 1]
    text_tokens = [{"text": w[4], "bbox": [round(v, 2) for v in w[:4]], "conf": 1.0, "src": "text"}
                   for w in page.get_text("words")]
    tokens = list(text_tokens)
    if ocr:
        for t in _ocr_page(page):
            if all(_iou(t["bbox"], k["bbox"]) < 0.3 for k in text_tokens):
                tokens.append(t)
    result = {"file_id": file_id, "page": pno, "width": page.rect.width, "height": page.rect.height,
              "rotation": page.rotation, "ocr": ocr, "tokens": tokens}
    paths.write_json(cache, result)
    return result


def parse_pages(spec):
    pages = []
    for part in spec.split(","):
        if "-" in part:
            a, b = part.split("-")
            pages.extend(range(int(a), int(b) + 1))
        else:
            pages.append(int(part))
    return pages


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return
    file_id, spec = argv[0], argv[1]
    for pno in parse_pages(spec):
        t = time.time()
        page = read_page(file_id, pno, ocr=True)
        n_ocr = sum(tk["src"] == "ocr" for tk in page["tokens"])
        print(f"{file_id} стр.{pno}: токенов {len(page['tokens'])}, из них OCR {n_ocr}, {time.time() - t:.0f} с", flush=True)


if __name__ == "__main__":
    main(sys.argv[1:])
