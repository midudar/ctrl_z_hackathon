"""Забирает результаты colab_paddle.ipynb (PaddleOCR на GPU): замер, проверку на актах, страницы ИД Речникова.

- BENCH: точность на 600 строках пилота против эталона (текстовый слой) рядом с EasyOCR и Tesseract → out/ocr_bench.json;
- ACTS: 20 актов Новослободской, отрендеренных как сканы: что разбор (ml.acts) извлёк по OCR против текстового слоя;
- PAGES: страницы ИД Речникова → кэш OCR (out/cache, ocr_source = paddle). Прежний кэш перезаписывается только с --write
  (сначала посмотреть на BENCH и ACTS, потом решать; EasyOCR-кэш сохранён в out/cache_easyocr_backup).

    python colab/fetch_paddle.py colab/colab_paddle_done.ipynb            # замер и акты
    python colab/fetch_paddle.py colab/colab_paddle_done.ipynb --write    # + записать страницы в кэш
"""
import argparse
import base64
import hashlib
import io
import json
import re
import sys
import zipfile
from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path

import pymupdf

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from ml import acts, paths                                # noqa: E402
from ml.ocr_eval import levenshtein, norm                 # noqa: E402
from ml.pages import cache_path, file_path                # noqa: E402
from fetch_pages import cache_record, notebook_text       # noqa: E402

BENCH_ZIP = HERE / "paddle_bench.zip"


def blocks(text, tag):
    """Записи из блоков ===TAG_BEGIN … ===TAG_END=== (проверка контрольной суммы)."""
    rx = re.compile(rf"==={tag}_BEGIN part=(\d+) sha256=([0-9a-f]{{64}})===\s*(.*?)\s*==={tag}_END===", re.S)
    out = []
    for part, sha, payload in rx.findall(text):
        data = base64.b64decode(re.sub(r"\s+", "", payload))
        if hashlib.sha256(data).hexdigest() != sha:
            print(f"  {tag} part={part}: контрольная сумма не совпала (вывод обрезан) — пропускаю")
            continue
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            out += [json.loads(l) for l in z.read("results.jsonl").decode("utf-8").splitlines() if l.strip()]
    return out


def bench(records):
    lines = {json.loads(l)["line_id"]: json.loads(l) for l in
             zipfile.ZipFile(BENCH_ZIP).read("lines.jsonl").decode("utf-8").splitlines() if l.strip()}
    for r in records:
        lines[r["line_id"]]["paddle"] = r["paddle"]
    rows = [r for r in lines.values() if "paddle" in r]
    print(f"\nЗамер на строках пилота ({len(rows)}), 1 − CER, эталон — текстовый слой:")
    for h in (False, True):
        C = sum(len(norm(r["gold"], h)) for r in rows)
        acc = {e: 1 - sum(levenshtein(norm(r["gold"], h), norm(r[e], h)) for r in rows) / C
               for e in ("easyocr", "tesseract", "paddle")}
        print(f"  {'без двойников' if h else 'строго       '}: " + "   ".join(f"{e} {v:.3f}" for e, v in acc.items()))
    for e in ("easyocr", "tesseract", "paddle"):
        exact = sum(levenshtein(norm(r["gold"], True), norm(r[e], True)) == 0 for r in rows) / len(rows)
        print(f"  {e:9} строк без ошибок {exact:.2f}")
    paths.write_json(paths.OUT / "ocr_bench.json", rows)


def ocr_text(tokens):
    """Как acts._ocr_text для кэша без ocr_text: строки по высоте (шаг 8 pt), слева направо."""
    rows = defaultdict(list)
    for t in tokens:
        rows[round(t[2] / 8)].append(t)
    return "\n".join(" ".join(t[0] for t in sorted(r, key=lambda t: t[1])) for _, r in sorted(rows.items()))


def acts_check(records):
    by_file = defaultdict(dict)
    for r in records:
        by_file[r["file_id"]][r["page"]] = ocr_text(r["tokens"])
    stats, n = defaultdict(float), 0
    cls = lambda a, k: sorted({v for v, _ in a[k]}) if k == "concrete" else sorted({x[1] for x in a[k]})
    print(f"\nАкты Новослободской по «сканам» (PaddleOCR) против текстового слоя:")
    for f, ocr_pages in sorted(by_file.items()):
        pages = acts.file_pages(f)
        ref = acts.parse_segment(f, pages)
        got = acts.parse_segment(f, [(p, ocr_pages[p], "ocr") if p in ocr_pages else (p, t, s) for p, t, s in pages])
        n += 1
        ok = {k: ref[k] == got[k] for k in ("element", "date")}
        ok.update({k: cls(ref, k) == cls(got, k) for k in ("concrete", "rebar")})
        sim = SequenceMatcher(None, (ref["work"] or "").lower(), (got["work"] or "").lower()).ratio()
        for k, v in ok.items():
            stats[k] += v
        stats["work"] += sim
        bad = [k for k, v in ok.items() if not v]
        print(f"  {f}: {'всё совпало' if not bad else 'не совпало: ' + ', '.join(bad)}; вид работ {sim:.2f}"
              + (f"  (бетон {cls(ref, 'concrete')} → {cls(got, 'concrete')}, арматура {cls(ref, 'rebar')} → "
                 f"{cls(got, 'rebar')})" if bad else ""))
    if n:
        print(f"  итого из {n}: конструкция {stats['element']:.0f}, бетон {stats['concrete']:.0f}, "
              f"арматура {stats['rebar']:.0f}, дата {stats['date']:.0f}; вид работ в среднем {stats['work'] / n:.2f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("notebook")
    ap.add_argument("--write", action="store_true", help="записать страницы ИД Речникова в кэш OCR")
    args = ap.parse_args()
    text = notebook_text(args.notebook)

    b = blocks(text, "BENCH")
    if b:
        bench(b)
    a = blocks(text, "ACTS")
    if a:
        acts_check(a)
    pages = blocks(text, "PAGES")
    print(f"\nстраниц ИД в ноутбуке: {len(pages)}")
    if pages and args.write:
        docs = {}
        for rec in pages:
            f, pno = rec["file_id"], rec["page"]
            if f not in docs:
                docs.clear()
                docs[f] = pymupdf.open(file_path(f))
            path = cache_path(f, pno, True)
            path.parent.mkdir(parents=True, exist_ok=True)
            paths.write_json(path, cache_record(rec, docs[f][pno - 1]))
        print(f"записано в кэш OCR: {len(pages)} страниц (ocr_source = paddle)")
    elif pages:
        print("в кэш не записано — добавь --write, когда посмотришь замер и акты")


if __name__ == "__main__":
    main()
