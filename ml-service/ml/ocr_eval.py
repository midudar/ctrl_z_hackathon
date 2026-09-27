"""Замер точности OCR (ТЗ 14.3: OCR Character Accuracy ≥ 0,95) на пилоте организаторов `02_МЕТОДИКА/ocr_pilot_20260811`.

Что меряем: наш OCR (`pages._readtext`, EasyOCR ru/en с теми же настройками, что в пайплайне) на вырезках строк
пилота. Эталон — `text_normalized` строк, у которых источник — текстовый слой PDF (`PDF_TEXT_LAYER`): это SILVER,
а не GOLD (организаторы сами пишут, что 95% без ручной разметки подтвердить нельзя), но текстовый слой — надёжный
эталон символов. Строки от Tesseract (сканы) эталоном не считаются. Берутся только обучающие объекты —
Новослободская и Тюменская; Речников (тест хакатона) не трогаем.

Character Accuracy = 1 − CER, CER = расстояние Левенштейна по символам / число символов эталона (формула из
сводки пилота). Два варианта нормализации:
- strict — только NFC, пробелы, «ё» → «е»;
- homoglyph — ещё латинские двойники кириллицы (A/А, C/С, O/О…) сведены к одной букве: на картинке их не отличить.

Вырезки пилота — ~144 dpi (A4 = 1191 px), а пайплайн распознаёт в 200 dpi, поэтому вырезка увеличивается до 200 dpi.

    python -m ml.ocr_eval [число строк на объект, по умолчанию 300]
    python -m ml.ocr_eval --bench [out/ocr_bench.json]   пересчёт сохранённых ответов EasyOCR / Tesseract / PaddleOCR
                                                          (замер в Colab, `colab/fetch_paddle.py`) с постобработкой
                                                          пайплайна (`pages.fix_homoglyphs`) и без неё
"""
import io
import json
import random
import sys
import time
import unicodedata
import zipfile

import numpy as np
from PIL import Image

from ml import paths
from ml.pages import _readtext, fix_homoglyphs

PILOT = paths.ROOT / "02_МЕТОДИКА" / "ocr_pilot_20260811" / "OCR-пилот 300 страниц v0.1.zip"
PREFIX = "ocr_pilot_300_v0.1/"
OBJECTS = {"Новослободская", "Пример нарушений на чертежах"}          # только обучающие
SCALE = 200 / 144
PAD = 12
_HOMO = str.maketrans("ABCEHKMOPTXYaceopxyё", "АВСЕНКМОРТХУасеорхуе")


def norm(text, homoglyph=False):
    t = unicodedata.normalize("NFC", text or "").replace("ё", "е").replace("Ё", "Е")
    t = " ".join(t.split())
    return t.translate(_HOMO) if homoglyph else t


def levenshtein(a, b):
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def recognize(img):
    """Текст строки: боксы EasyOCR слева направо через пробел."""
    w, h = img.size
    img = img.convert("L").resize((max(1, round(w * SCALE)), max(1, round(h * SCALE))))
    padded = Image.new("L", (img.width + 2 * PAD, img.height + 2 * PAD), 255)   # у края строки EasyOCR теряет буквы
    padded.paste(img, (PAD, PAD))
    img = padded
    boxes = _readtext(np.array(img))
    boxes.sort(key=lambda b: min(p[0] for p in b[0]))
    return " ".join(b[1] for b in boxes)


def main(argv):
    per_object = int(argv[0]) if argv else 300
    z = zipfile.ZipFile(PILOT)
    lines = [json.loads(l) for l in z.read(PREFIX + "data/recognition_lines.jsonl").decode("utf-8").splitlines()]
    # эталон испорчен (сами организаторы помечают): «(cid:588)» — шрифт без таблицы Unicode, флаги кодировки
    lines = [r for r in lines if r["corpus"] in OBJECTS and r["source_provenance"] == "PDF_TEXT_LAYER"
             and r["orientation"] == "HORIZONTAL" and norm(r["text_normalized"])
             and not r.get("encoding_flags") and "(cid:" not in r["text_normalized"]]
    rng = random.Random(20260924)
    sample = []
    for obj in sorted(OBJECTS):
        pool = [r for r in lines if r["corpus"] == obj]
        sample += rng.sample(pool, min(per_object, len(pool)))
    print(f"строк с эталоном из текстового слоя: {len(lines)}, в выборке {len(sample)}")

    rows, t0 = [], time.time()
    for i, r in enumerate(sample, 1):
        img = Image.open(io.BytesIO(z.read(PREFIX + f"images/lines/{r['line_id']}.png")))
        hyp = recognize(img)
        row = {"line_id": r["line_id"], "corpus": r["corpus"], "bucket": r["text_bucket"], "gold": r["text_normalized"],
               "ocr": hyp}
        for mode, h in (("strict", False), ("homoglyph", True)):
            g, o = norm(r["text_normalized"], h), norm(hyp, h)
            row[mode] = (levenshtein(g, o), len(g))
        rows.append(row)
        if i % 50 == 0:
            print(f"  {i}/{len(sample)} за {time.time() - t0:.0f} с", flush=True)
    paths.write_json(paths.OUT / "ocr_eval.json", rows)

    def acc(rs, mode):
        errs, chars = sum(r[mode][0] for r in rs), sum(r[mode][1] for r in rs)
        return 1 - errs / chars if chars else float("nan")

    print(f"\nCharacter Accuracy = 1 − CER (порог ТЗ ≥ 0,95), строк {len(rows)}, {time.time() - t0:.0f} с:")
    groups = [("все", rows)] + [(o, [r for r in rows if r["corpus"] == o]) for o in sorted(OBJECTS)] + \
             [(b, [r for r in rows if r["bucket"] == b]) for b in sorted({r["bucket"] for r in rows})]
    for name, rs in groups:
        print(f"  {name:32} строк {len(rs):4}  strict {acc(rs, 'strict'):.3f}  homoglyph {acc(rs, 'homoglyph'):.3f}")
    exact = sum(1 for r in rows if r["homoglyph"][0] == 0) / len(rows)
    print(f"  строк распознано без единой ошибки (homoglyph): {exact:.2f}")
    worst = sorted(rows, key=lambda r: -r["homoglyph"][0] / max(1, r["homoglyph"][1]))[:8]
    print("\nхудшие строки:")
    for r in worst:
        print(f"  {r['line_id']:22} эталон «{r['gold'][:60]}»\n  {'':22} OCR    «{r['ocr'][:60]}»")


def bench(path):
    """Строгая точность (1 − CER) по сохранённым ответам движков: как есть, после постобработки пайплайна и
    в мягком режиме (двойники не штрафуются). Эталон — тот же текстовый слой, что в main()."""
    rows = paths.read_json(path)
    print(f"строк {len(rows)} (эталон — текстовый слой обучающих объектов); порог ТЗ ≥ 0,95")
    print(f"  {'движок':10} {'строго':>8} {'строго + постобработка':>24} {'без штрафа за двойники':>24}"
          f" {'строк без ошибок':>18}")
    for eng in [k for k in rows[0] if k not in ("line_id", "gold")]:
        res = {}
        for mode, prep, h in (("strict", lambda t: t, False), ("fixed", fix_homoglyphs, False),
                              ("homoglyph", lambda t: t, True)):
            errs = chars = exact = 0
            for r in rows:
                g, o = norm(r["gold"], h), norm(prep(r[eng] or ""), h)
                e = levenshtein(g, o)
                errs += min(e, len(g))
                chars += len(g)
                exact += e == 0
            res[mode] = (1 - errs / chars, exact / len(rows))
        print(f"  {eng:10} {res['strict'][0]:8.4f} {res['fixed'][0]:24.4f} {res['homoglyph'][0]:24.4f}"
              f" {res['fixed'][1]:18.2f}")


if __name__ == "__main__":
    if sys.argv[1:2] == ["--bench"]:
        bench(sys.argv[2] if len(sys.argv) > 2 else paths.OUT / "ocr_bench.json")
    else:
        main(sys.argv[1:])
