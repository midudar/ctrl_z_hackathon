# -*- coding: utf-8 -*-
"""Выбор актуальной редакции документа (этап 0).

В ПД Речникова рядом лежат и первая версия тома 2022 года, и её корректировки:
`01-0722-14-П-КР1.pdf`, `…-КР1-кор2.pdf`, `01-07_22-14-П-КР1-кор3.pdf`. Без отбора
значения из разных редакций смешиваются в одном сравнении. По ТЗ сверять можно
только с последней утверждённой редакцией, конфликт → CLARIFICATION_REQUIRED.

Документы группируются по шифру: из имени убираются маркер редакции и «(1)» —
копия при скачивании, — остальное приводится к буквам и цифрам, потому что один и
тот же шифр пишут по-разному: `01-07-22-14-П`, `01-0722-14-П`, `01-07_22-14-П`.

Редакция берётся с титульного листа («Корректировка №3», год), а имя файла —
запасной вариант: в `01-07_22-14-П-ИОС7.1-кор3.pdf` на титуле стоит
«Корректировка №4». Это порядок из ТЗ: источник истины — штамп, не имя файла.

    python -m ml.revisions OBJ-RECHNIKOV-7-7 PD
"""
import functools
import re
import sys

import pymupdf

from ml import paths

REV_NAME = re.compile(r"[\s._()\[-]*(?:изм|кор+|ред)[\s._-]*(\d+)", re.I)
COPY = re.compile(r"\s*\(\d+\)\s*$")            # «ПБ1 (5).pdf» — копия, а не редакция
TITLE_REV = re.compile(r"корректировк\w*\s*(?:№\s*)?(\d+)?", re.I)
YEAR = re.compile(r"\b(20\d{2})\b")

# У ИД редакций не бывает: акты не заменяют друг друга, нужны все.
NO_REVISIONS = {"ID"}


def base_code(file_name):
    """Шифр без редакции и без «(1)», только буквы и цифры: ключ группы."""
    stem = COPY.sub("", file_name.rsplit(".", 1)[0])
    return re.sub(r"[^0-9a-zа-яё]", "", COPY.sub("", REV_NAME.sub("", stem)).lower())


def name_revision(file_name):
    """Номер редакции из имени файла. «кор.3_Корр.2» → 3 (берём первый, он основной)."""
    nums = [int(n) for n in REV_NAME.findall(COPY.sub("", file_name.rsplit(".", 1)[0]))]
    return nums[0] if nums else None


@functools.lru_cache(maxsize=None)
def title_page(file_id):
    """Текст первой страницы. Титул почти всегда с текстовым слоем, OCR не нужен."""
    from ml.pages import file_path
    try:
        with pymupdf.open(file_path(file_id)) as doc:
            return doc[0].get_text() if doc.page_count else ""
    except Exception:
        return ""


def revision(file_id, file_name):
    """(номер редакции, год, откуда взято) — чем больше, тем новее."""
    text = title_page(file_id)
    m = TITLE_REV.search(text)
    years = [int(y) for y in YEAR.findall(text)]
    year = min(years) if years else 0      # на титуле год издания; берём наименьший из встреченных
    from_name = name_revision(file_name)
    if m and m.group(1):
        return int(m.group(1)), year, "титул"
    if from_name is not None:
        # на титуле «Корректировка» без номера либо титул не прочитался
        return from_name, year, "имя файла"
    if m:
        return 1, year, "титул (без номера)"
    return 0, year, "первая редакция"


def groups(rows):
    """Записи реестра → {шифр: [(запись, редакция), …]}, отсортировано от старой к новой."""
    out = {}
    for r in rows:
        name = r["relative_path"].split("/")[-1]
        out.setdefault((r["stage"], base_code(name)), []).append(r)
    ranked = {}
    for key, items in out.items():
        if len(items) == 1:            # титул не читаем зря: одиночному файлу редакция не нужна
            ranked[key] = [(items[0], (0, 0, "единственный"))]
            continue
        scored = [(r, revision(r["file_id"], r["relative_path"].split("/")[-1])) for r in items]
        scored.sort(key=lambda p: (p[1][0], p[1][1]))
        ranked[key] = scored
    return ranked


def latest(rows):
    """Оставить по одной — последней — редакции каждого шифра.

    Возвращает (записи, конфликты). Конфликт — когда у двух разных файлов группы
    одинаковый номер редакции: какой утверждён, из PDF не видно.
    """
    keep, conflicts = [], []
    for key, scored in groups(rows).items():
        if key[0] in NO_REVISIONS:
            keep.extend(r for r, _ in scored)
            continue
        top_rev = scored[-1][1][0]
        tied = [r for r, rv in scored if rv[0] == top_rev]
        if len(tied) > 1 and len({r["sha256"] for r in tied}) > 1:
            conflicts.append((key[1], [r["file_id"] for r in tied]))
        keep.append(scored[-1][0])
    return keep, conflicts


def main(argv):
    object_id = argv[0] if argv else paths.TEST_OBJECT
    stage = argv[1] if len(argv) > 1 else "PD"
    rows = [r for r in paths.read_jsonl(paths.MANIFEST)
            if r["object_id"] == object_id and r["stage"] == stage and r["extension"] == ".pdf"]
    ranked = groups(rows)
    n_drop = 0
    for key, scored in sorted(ranked.items()):
        if len(scored) == 1:
            continue
        print(f"\n{key[1]}")
        for r, rv in scored:
            mark = "берём " if r is scored[-1][0] else "старая"
            n_drop += r is not scored[-1][0]
            print(f"  {mark} {r['file_id']}  ред.{rv[0]} {rv[1]} ({rv[2]})  "
                  f"{r['relative_path'].split('/')[-1]}")
    keep, conflicts = latest(rows)
    print(f"\n{object_id} {stage}: {len(rows)} файлов → {len(keep)}, отброшено устаревших {n_drop}")
    for code, ids in conflicts:
        print(f"  КОНФЛИКТ РЕДАКЦИЙ {code}: {', '.join(ids)}")


if __name__ == "__main__":
    main(sys.argv[1:])
