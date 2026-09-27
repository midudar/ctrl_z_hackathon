"""Трек АР: экспликации помещений (номер, наименование, площадь) в ПД и РД и их сравнение.

Экспликация — таблица на листе плана АР: «Экспликация помещений 1-го этажа» (в Revit — «Спецификация
помещений»), колонки № | Наименование | Площадь, м² | Кат. Значения лежат в текстовом слое. Так в пилотной
разметке организаторов выглядят почти все их кандидаты: в РД добавлено помещение 1.109 и выросла площадь этажа
(Полярная 25, СОШ), изменены назначения и площади (Алтуфьевское 79Б), пищеблок (Полярная 25, ДОО), площади
(Полярная 16); Полярная 17 — их отрицательная контрольная пара. У обучающих объектов РД АР нет, поэтому трек
разрабатывался и проверялся на объектах пилота (`pilot_objects.py`, `python -m ml.explication --pilot`).

Разбор таблицы (по словам видимой страницы, после Rotate):
  заголовок «Экспликация / Спецификация / Ведомость помещений» → под ним шапка колонок «Номер/№», «Наименование/
  Имя», «Площадь» → колонки по положению шапки → строки таблицы по горизонтальным линиям сетки (get_drawings;
  так многострочные названия разбираются точно), без сетки — по номерам помещений. В ячейке бывает два наложенных
  значения (ДОО: «51,5» поверх «51,6») — хранятся оба, сравнение идёт по пересечению.

Сравнение по ключу помещения (номер; если номера короткие и повторяются по этажам — этаж из заголовка + номер):
  есть в ПД и нет в РД, есть в РД и нет в ПД (с поиском пары по площади: «23 Помещение 19,41» → «22 Комната
  отдыха 19,62» — перенумерация со сменой назначения), изменилось наименование, изменилась площадь.

Выход (параметр PZ-003 «Полезная / расчётная площадь», источник РД в матрице — сводная экспликация помещений):
  * строка на каждое помещение со сменой состава или назначения — кандидат, location — номер помещения;
  * строка на таблицу (этаж) с изменёнными площадями — кандидат, location — этаж, в `note` список помещений;
  * итоговая строка «Объект»: сколько помещений сверено, есть ли расхождения.
Это кандидаты для инспектора, а не вердикт: организаторы сами оставили такие находки кандидатами — изменение
может быть согласовано.

    python -m ml.explication <object_id>            таблицы и расхождения
    python -m ml.explication --pilot                по объектам пилота + сверка с кандидатами организаторов
"""
import json
import re
import sys
from collections import defaultdict
from difflib import SequenceMatcher

import pymupdf

from ml import paths, registry
from ml.pages import file_path

CODE = "PZ-003"

ANCHOR_RE = re.compile(r"(экспликаци\w*|спецификаци\w*|ведомост\w*)\s+помещен", re.I)
AREA_HDR_RE = re.compile(r"^площад", re.I)
NUM_HDR_RE = re.compile(r"^(номер|№|nn|поз)", re.I)
NAME_HDR_RE = re.compile(r"^(наименован|имя|назначен)", re.I)
ROOM_NO_RE = re.compile(r"^(?:\d{1,3}(?:[.\-]\d{1,3}){0,3}[а-яa-z]?|[А-ЯA-Z]\*?)$")
AREA_RE = re.compile(r"^\d{1,5}[.,]\d{1,2}$")
TOTAL_RE = re.compile(r"итог|всего", re.I)
FLOOR_RE = re.compile(r"(-?\s?\d{1,2}(?:\s*[-–]\s*\d{1,2})?)\s*-?\s*(?:го|й|ого|ый|х)?\s*(?:этаж\w*|эт\.|эт\b)|"
                      r"(антр?есол\w*|подвал\w*|цокол\w*|техническ\w*|типов\w*|мансард\w*|кровл\w*)|"
                      r"(\d+)\s*секци|секци\w*\s*(?:№\s*)?[сc]?(\d+)", re.I)
# заголовок следующей таблицы на том же листе: на нём текущая таблица заканчивается
# («Экспликация помещений МОП», под ней — «Спецификация квартир» у Полярной 16)
NEXT_TABLE_RE = re.compile(r"^(экспликаци|спецификаци|ведомост|таблиц)\w*", re.I)
FLOOR_WORDS = {"ант": "антресоль", "под": "подвал", "цок": "цоколь", "тех": "техэтаж", "тип": "типовой этаж",
               "ман": "мансарда", "кро": "кровля"}   # «антесольного» (опечатка в РД Алтуфьевского) — тоже антресоль
RENUMBER_TOL = 0.03      # пара «исчезло / появилось» с площадью в пределах 3% — перенумерация
# Пометки поверх таблицы, а не часть названия: облака корректировок («Корректировка №20»), выноски
# маркировки («Л/1», «К/1»), одиночные буквы
JUNK_RE = re.compile(r"^(?:корректировк\w*|изм\.?|[А-ЯA-Z]/\d+|№\d+|[А-ЯA-Z])$", re.I)


def clean_name(s):
    return " ".join(w for w in s.split() if not JUNK_RE.match(w))


def _tokens(s):
    s = s.lower().replace("ё", "е")
    s = re.sub(r"\bс/у\b|\bс\.у\.?", "санузел", s)
    s = re.sub(r"\bсан\.\s*узел|сан\.узел", "санузел", s)
    s = re.sub(r"\bтех\.\s*", "техническое ", s)
    s = re.sub(r"\(.*?(\)|$)", " ", s)                  # «(класс Ф4.3)», «(Тип Л1)» — уточнения, не назначение
    return [w[:6] for w in re.findall(r"[а-яa-z]{2,}", s)]


def same_name(a, b):
    """Одно ли назначение. Слова сравниваются по основам (6 букв); если слова одного названия целиком входят в
    другое («Учебный кабинет (1» — обрезанная строка), это то же помещение."""
    ta, tb = _tokens(clean_name(a)), _tokens(clean_name(b))
    if not ta or not tb:
        return True                                     # названия нет — назначение не с чем сравнивать
    sa, sb = set(ta), set(tb)
    if sa <= sb or sb <= sa or len(sa & sb) / len(sa | sb) >= 0.6:
        return True
    return SequenceMatcher(None, "".join(ta), "".join(tb)).ratio() >= 0.85


def _area(s):
    return float(s.replace(",", "."))


# ---------------------------------------------------------------- страница → таблицы

def _words(page):
    """Слова видимой страницы: (Rect, текст). Наложенные копии (жирный шрифт CAD, «БКТ БКТ») убираются."""
    m = page.rotation_matrix
    out = []
    for w in page.get_text("words"):
        r = pymupdf.Rect(w[:4]) * m
        t = w[4].strip()
        if not t:
            continue
        dup = False
        for r2, t2 in out[-40:]:
            if t2 == t and abs(r2.x0 - r.x0) < 3 and abs(r2.y0 - r.y0) < 3:
                dup = True
                break
        if not dup:
            out.append((r, t))
    return out


def _lines(page):
    """Строки видимой страницы: (Rect, текст) — для заголовков таблиц."""
    m = page.rotation_matrix
    out = []
    for b in page.get_text("dict")["blocks"]:
        for l in b.get("lines", []):
            t = " ".join(s["text"] for s in l["spans"]).strip()
            if t:
                out.append((pymupdf.Rect(l["bbox"]) * m, re.sub(r"\s+", " ", t)))
    return out


_hcache = {}


def _page_hlines(page):
    """Все горизонтальные отрезки страницы [(y, x0, x1)] в координатах видимой страницы (кэш на страницу)."""
    key = (page.parent.name, page.number)
    if key not in _hcache:
        m = page.rotation_matrix
        out = []
        for d in page.get_drawings():
            for it in d["items"]:
                if it[0] == "l":
                    segs = [(it[1] * m, it[2] * m)]
                elif it[0] == "re":
                    r = it[1] * m
                    segs = [(r.tl, r.tr), (r.bl, r.br)]
                else:
                    continue
                for p, q in segs:
                    if abs(p.y - q.y) < 0.6 and abs(p.x - q.x) > 8:
                        out.append((round(p.y, 1), min(p.x, q.x), max(p.x, q.x)))
        _hcache.clear()                    # держим одну страницу: листы A0 дают сотни тысяч отрезков
        _hcache[key] = out
    return _hcache[key]


def _hlines(page, area):
    """Горизонтальные отрезки сетки внутри area. Rect.intersects тут не годится: у горизонтальной линии
    рамка нулевой высоты, и PyMuPDF считает её пустой."""
    return [(y, x0, x1) for y, x0, x1 in _page_hlines(page)
            if area.y0 <= y <= area.y1 and x0 <= area.x1 and x1 >= area.x0]


def _undouble(title):
    """Заголовок, продублированный для жирного шрифта CAD: «2 2го го этажа этажа. . Секция Секция 1. 1.» →
    «2го этажа. Секция 1.» (слово, с которого начинается следующее, и хвост предыдущего слова убираются)."""
    toks, out = title.split(), []
    for i, t in enumerate(toks):
        nxt = toks[i + 1] if i + 1 < len(toks) else ""
        if nxt.startswith(t) or (out and out[-1].endswith(t) and len(t) < len(out[-1])):
            continue
        out.append(t)
    return " ".join(out)


def _floor_of(title):
    """«Экспликация помещений 1-го этажа» → «1 этаж»; «антресольного этажа» → «антресоль»; «(2 секция)» → «секция 2»."""
    title = _undouble(title)
    parts = []
    for m in FLOOR_RE.finditer(title):
        if m.group(1):
            parts.append(re.sub(r"\s", "", m.group(1)).replace("–", "-") + " этаж")
        elif m.group(2):
            parts.append(FLOOR_WORDS.get(m.group(2).lower()[:3], m.group(2).lower()))
        elif m.group(3) or m.group(4):
            parts.append("секция " + (m.group(3) or m.group(4)))
    return ", ".join(dict.fromkeys(parts))


def page_tables(page, _page_cache={}):
    """Таблицы экспликаций на странице: [{title, floor, bbox, rows: [{num, name, areas, bbox}], total}]."""
    lines = _lines(page)
    anchors = [(r, t) for r, t in lines if ANCHOR_RE.search(t) and not re.search(r"пол(ов|а)\b|отделк", t, re.I)]
    if not anchors:
        return []
    words = _words(page)
    tables = []
    used = set()
    for ar, atext in sorted(anchors, key=lambda a: (a[0].y0, a[0].x0)):
        if any(abs(ar.y0 - u[1]) < 4 and abs(ar.x0 - u[0]) < 40 for u in used):
            continue                                      # копия заголовка (жирный шрифт)
        used.add((ar.x0, ar.y0))
        t = _table(page, ar, atext, lines, words)
        if t and t["rows"]:
            tables.append(t)
    return tables


def _table(page, ar, atext, lines, words):
    # шапка колонок под заголовком
    zone = pymupdf.Rect(ar.x0 - 260, ar.y0 - 2, ar.x1 + 260, ar.y1 + 90)
    hdr = [(r, t) for r, t in lines if zone.intersects(r) and r.y0 >= ar.y0 - 2]
    area_c = [r for r, t in hdr if AREA_HDR_RE.search(t)]
    num_c = [r for r, t in hdr if NUM_HDR_RE.search(t)]
    name_c = [r for r, t in hdr if NAME_HDR_RE.search(t)]
    # таблицы стоят вплотную (СОШ: восемь экспликаций в ряд), поэтому шапка — своя: «Наименование» ближе всего
    # к середине заголовка, «Площадь» — ближайшая справа от неё, «№» — ближайший слева
    acx = (ar.x0 + ar.x1) / 2
    name_c = sorted(name_c, key=lambda r: abs((r.x0 + r.x1) / 2 - acx) + abs(r.y0 - ar.y1))
    if not name_c:
        return None
    name_h = name_c[0]
    area_c = [r for r in area_c if r.x0 > name_h.x1 - 5]
    if not area_c:
        return None
    area_h = min(area_c, key=lambda r: r.x0 - name_h.x1 + abs(r.y0 - ar.y1))
    num_c = [r for r in num_c if r.x1 <= name_h.x0 + 2]
    num_h = max(num_c, key=lambda r: r.x0) if num_c else None
    head_bottom = max(r.y1 for r in [area_h, name_h] + ([num_h] if num_h else []))
    # «Площадь, м2» и «Кат. поме щения» бывают в 2–3 строки: низ шапки — по словам шапки над первой строкой
    left = (num_h.x0 if num_h else name_h.x0 - 120) - 30
    nxt = [r.x0 for r, t in hdr if NUM_HDR_RE.search(t) and r.x0 > area_h.x1]
    right = min([area_h.x1 + 90] + [x - 5 for x in nxt])
    num_zone = (num_h.x0 - 15, num_h.x1 + 15) if num_h else (left, name_h.x0 - 5)
    area_zone = (area_h.x0 - 30, area_h.x1 + 30)

    def col(r, t=""):
        cx = (r.x0 + r.x1) / 2
        if cx > area_zone[1]:
            return "cat"
        if area_zone[0] <= cx <= area_zone[1]:
            # в зоне площади — только числа и единицы; хвост длинного названия («…гигиены для») — это название
            if AREA_RE.match(t) or r.x0 >= area_h.x0 - 8:
                return "area"
            return "name"
        if num_zone[0] <= cx <= num_zone[1] and r.x1 <= num_zone[1] + 25:
            return "num"
        return "name"

    below = [(r, t) for r, t in words if left <= (r.x0 + r.x1) / 2 <= right and r.y0 >= head_bottom - 1]
    below.sort(key=lambda w: (w[0].y0, w[0].x0))
    header_words = {"м2", "м²", "поме", "щения", "помещения", "кат.", "пом.", "нет", "м2 нет"}
    nums = [(r, t) for r, t in below if col(r, t) == "num" and ROOM_NO_RE.match(t)]
    # «1.100 Лифтовой холл»: номер склеен с названием в одно слово-строку не бывает (слова разделены), но
    # номер может выйти за колонку номера — тогда его берём по первому слову строки, начинающейся левее названий
    if not nums:
        return None
    # конец таблицы: разрыв по вертикали больше 6 строк или следующий заголовок таблицы ниже
    row_h = max(8.0, min(r.height for r, _ in nums) * 1.2)
    next_anchor = min([r.y0 for r, t in lines if (ANCHOR_RE.search(t) or NEXT_TABLE_RE.search(t))
                       and r.y0 > head_bottom + 5 and r.x0 < right and r.x1 > left] + [page.rect.height + 1])
    kept, last_y = [], head_bottom
    for r, t in nums:
        if r.y0 >= next_anchor or r.y0 - last_y > 6 * row_h + 40:
            break
        kept.append((r, t))
        last_y = r.y1
    nums = kept
    if not nums:
        return None
    bottom = min(next_anchor, nums[-1][0].y1 + 6 * row_h)
    body = [(r, t) for r, t in below if r.y0 < bottom and t.lower() not in header_words or
            (r.y0 < bottom and AREA_RE.match(t))]
    body = [(r, t) for r, t in body if r.y0 < bottom]

    # строки таблицы: сетка, иначе середины между номерами
    grid = _hlines(page, pymupdf.Rect(left, head_bottom - 3, right, bottom))
    # линия строки должна перекрывать саму таблицу: длинная линия соседней таблицы, задевающая край
    # (СОШ: x 3054–3335 рядом с таблицей до x 3059), иначе режет строку 1.87 пополам
    t0, t1 = num_zone[0], area_h.x1 + 10
    span_need = 0.5 * (t1 - t0)
    ys = sorted({y for y, x0, x1 in grid if min(x1, t1) - max(x0, t0) >= span_need or
                 (x0 <= num_zone[1] and x1 >= area_zone[0])})
    ys = [y for i, y in enumerate(ys) if i == 0 or y - ys[i - 1] > 2.5]
    bands = []
    if len(ys) >= 3:
        bands = [(ys[i], ys[i + 1]) for i in range(len(ys) - 1) if ys[i + 1] - ys[i] >= row_h * 0.6]
    if not bands or sum(1 for a, b in bands if any(a <= (r.y0 + r.y1) / 2 < b for r, _ in nums)) < 0.7 * len(nums):
        cy = [(r.y0 + r.y1) / 2 for r, _ in nums]
        edges = [head_bottom] + [(cy[i] + cy[i + 1]) / 2 for i in range(len(cy) - 1)] + [cy[-1] + row_h]
        bands = list(zip(edges[:-1], edges[1:]))
        # итоговая строка под последним помещением
        tail = [r for r, t in body if (r.y0 + r.y1) / 2 > edges[-1] and AREA_RE.match(t) and col(r, t) == "area"]
        if tail:
            bands.append((edges[-1], min(r.y1 for r in tail) + 1))

    rows, total, group = [], None, None
    for y0, y1 in bands:
        cell = [(r, t) for r, t in body if y0 <= (r.y0 + r.y1) / 2 < y1]
        if not cell:
            continue
        num = [t for r, t in cell if col(r, t) == "num" and ROOM_NO_RE.match(t)]
        area_t = [t for r, t in cell if col(r, t) == "area" and AREA_RE.match(t)]
        areas = sorted({_area(t) for t in area_t})
        prec = min((len(re.split(r"[.,]", t)[1]) for t in area_t), default=2)
        name_w = sorted([(r, t) for r, t in cell if col(r, t) in ("name", "num") and not
                         (col(r, t) == "num" and ROOM_NO_RE.match(t) and num and t == num[0])],
                        key=lambda w: (round(w[0].y0 / 3), w[0].x0))
        name = " ".join(t for _, t in name_w)
        bbox = pymupdf.Rect(left, y0, right, y1)
        if TOTAL_RE.search(name):
            total = areas[-1] if areas else total
            continue
        if not num or not areas:
            if areas and not name:
                total = areas[-1]                         # итог без подписи (Полярная 17: «367,30»)
            elif name and not areas:
                group = name                              # «Помещения БКТ №1», «1 Помещения жилой зоны МОП»
            continue
        rows.append({"num": num[0], "name": clean_name(name), "areas": areas, "prec": prec, "group": group,
                     "bbox": list(bbox)})
    title = " ".join(dict.fromkeys(t for r, t in lines if ar.y0 - 2 <= r.y0 < area_h.y0 - 1
                                   and r.x1 > left and r.x0 < right))
    return {"title": title or atext, "floor": _floor_of(title or atext), "rows": rows, "total": total,
            "bbox": [left, ar.y0, right, bottom]}


# ---------------------------------------------------------------- объект → помещения по стадиям

AR_NAME_RE = re.compile(r"[-_ (–](АР|AR)[\s._\d)–-]", re.I)
PREFILTER_RE = re.compile(r"(экспликаци|спецификаци|ведомост)\w*\s+помещен", re.I)


def ar_files(object_id, stage):
    ids = registry.files(object_id, stage, "AR")
    man = {r["file_id"]: r for r in paths.read_jsonl(paths.MANIFEST)}
    rows = [r for r in man.values() if r["object_id"] == object_id and r["extension"] == ".pdf"
            and r["stage"] in registry.STAGES[stage]]
    from ml import revisions
    keep = {r["file_id"] for r in revisions.latest(rows)[0]}
    extra = [r["file_id"] for r in rows if r["file_id"] in keep and r["file_id"] not in ids
             and AR_NAME_RE.search(" " + r["relative_path"].split("/")[-1])]
    return ids + extra


PARSER_VERSION = 5        # поднять при изменении разбора таблиц — кэш пересчитается


def file_tables(fid):
    """Таблицы экспликаций файла. Кэш по SHA-256 файла (`paths.CACHE/explication`): листы АР — это A0 с сотнями
    тысяч отрезков, разбор всех листов объекта занимает минуты."""
    from ml.pages import file_sha
    cache = paths.CACHE / "explication" / f"{file_sha(fid)[:16]}_v{PARSER_VERSION}.json"
    if cache.exists():
        return paths.read_json(cache)
    out = []
    try:
        doc = pymupdf.open(file_path(fid))
    except Exception:
        return out
    for pno, page in enumerate(doc, 1):
        if not PREFILTER_RE.search(page.get_text()):
            continue
        for t in page_tables(page):
            t.update(page=pno)
            out.append(t)
    paths.write_json(cache, out)
    return out


def extract(object_id, stage, verbose=False):
    """Все таблицы экспликаций стадии: [{file_id, page, title, floor, rows, total, bbox}]."""
    out = []
    for fid in ar_files(object_id, stage):
        # этаж пересчитывается из заголовка: правка FLOOR_RE не требует заново разбирать листы
        tabs = [dict(t, file_id=fid, floor=_floor_of(t["title"])) for t in file_tables(fid)]
        out += tabs
        if verbose:
            print(f"   {stage} {fid}: помещений {sum(len(t['rows']) for t in tabs)}", flush=True)
    return out


def _short(num):
    return not re.search(r"\.|\d{3}", num)          # «1», «23», «А» — повторяются по этажам; «1.109», «135» — нет


REV_RE = re.compile(r"(?:изм|корр?|кор|ред|rev|вер|v)\.?\s*_?(\d{1,2})(?!\d)", re.I)
DATE_RE = re.compile(r"(?<!\d)(\d{2})[._](\d{2})[._](\d{2}|\d{4})(?!\d)")
YEAR_RE = re.compile(r"(?<!\d)(20[12]\d)(?!\d)")


def file_rank(file_id, _man={}):
    """Насколько свежий документ по пути: (номер изменения / корректировки, дата, год).

    Нужен внутри одной группы похожих таблиц: в пилотных комплектах рядом лежат «ПД 2022», «Корр1», «Корр2»,
    РД «изм.3» и «изм.5», копии «(ВП)» / «(ОП)», а шифры записаны по-разному, поэтому отбор редакций по шифру
    (`ml.revisions`) их не разводит. Сравнивать, как и организаторы, — последнюю ПД с последней РД.
    """
    if not _man:
        _man.update({r["file_id"]: r for r in paths.read_jsonl(paths.MANIFEST)})
    path = _man[file_id]["relative_path"]
    rev = max([int(m.group(1)) for m in REV_RE.finditer(path)] + [0])
    dates = []
    for d, m, y in DATE_RE.findall(path):
        y = int(y) + (2000 if len(y) == 2 else 0)
        if 1 <= int(m) <= 12 and 1 <= int(d) <= 31 and 2010 <= y <= 2035:
            dates.append(y * 10000 + int(m) * 100 + int(d))
    years = [int(y) * 10000 for y in YEAR_RE.findall(path)]
    return rev, max(dates + years + [0])


def _groups(tables):
    """Таблицы одного листа и одного этажа — одна группа (СОШ: экспликация 1-го этажа разрезана на 3–8 колонок).

    В экспликациях квартир номера 1…7 повторяются в каждой квартире — повтор номера получает порядковый
    суффикс: «2#3» — помещение 2 третьей по счёту квартиры таблицы."""
    out = {}
    for t in tables:
        g = out.setdefault((t["file_id"], t["page"], t["floor"]),
                           {"file_id": t["file_id"], "page": t["page"], "floor": t["floor"], "rooms": {},
                            "title": t["title"], "bbox": t["bbox"]})
        for r in t["rows"]:
            key, k = r["num"], 1
            while key in g["rooms"]:
                k += 1
                key = f"{r['num']}#{k}"
            g["rooms"][key] = {"num": r["num"], "key": key, "floor": t["floor"], "names": [r["name"]] if r["name"]
                               else [], "areas": set(r["areas"]), "prec": r["prec"],
                               "where": [(t["file_id"], t["page"], r["bbox"])]}
    return list(out.values())


def _parts(floor):
    sec = re.search(r"секция (\S+)", floor)
    rest = re.sub(r"(,\s*)?секция \S+", "", floor).strip(", ")
    return (sec.group(1) if sec else ""), rest


def _floor_set(floor):
    """«3-14 этаж» → {3, …, 14}; «1 этаж» → {1}; без номера этажа — None."""
    m = re.search(r"(-?\d+)(?:-(\d+))? этаж", floor)
    if not m:
        return None
    a, b = int(m.group(1)), int(m.group(2) or m.group(1))
    return set(range(min(a, b), max(a, b) + 1))


def _names_match(p, r):
    return any(same_name(a, b) for a in p["names"] or [""] for b in r["names"] or [""])


def _sim(g1, g2):
    """Похожесть двух таблиц (одна и та же таблица в другой стадии или редакции): доля общих ключей помещений
    с тем же назначением. Разные секции не сравниваются; разные этажи — только при почти полном совпадении."""
    s1, f1 = _parts(g1["floor"])
    s2, f2 = _parts(g2["floor"])
    if s1 and s2 and s1 != s2:
        return 0.0
    # непересекающиеся этажи — разные таблицы, даже если планировка та же: у Полярной 17 квартиры 2-го этажа
    # (ПД) и 3–14-го (РД) совпадают по номерам и назначениям, но площади у них свои
    fs1, fs2 = _floor_set(f1), _floor_set(f2)
    if fs1 and fs2 and not fs1 & fs2:
        return 0.0
    a, b = g1["rooms"], g2["rooms"]
    if min(len(a), len(b)) < 2:
        return 0.0
    common = sum(1 for n in a if n in b and _names_match(a[n], b[n]))
    sim = common / min(len(a), len(b))
    need = 0.8 if (f1 and f2 and f1 != f2) else 0.5
    return sim if sim >= need else 0.0


def components(pd_t, rd_t):
    """Пары «лист ПД ↔ лист РД» для сравнения: [(группа, лист ПД, лист РД)].

    Похожие таблицы обеих стадий собираются в группы; в группе берутся документы последней ПД и последней РД
    (при равной свежести — все: у Алтуфьевского экспликация 1-го этажа есть и в АР1, и в АР2 РД с разными
    значениями, организаторы сами пишут, что эти файлы перепутаны). Каждому листу ПД — самый похожий лист РД.
    Таблицы без пары (этажи, на которые РД не выпущена; другой проект в том же комплекте) не сравниваются.
    """
    nodes = [("PD", g) for g in _groups(pd_t)] + [("RD", g) for g in _groups(rd_t)]
    parent = list(range(len(nodes)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(nodes)):
        for j in range(i + 1, len(nodes)):
            if find(i) != find(j) and _sim(nodes[i][1], nodes[j][1]):
                parent[find(i)] = find(j)
    comps = defaultdict(list)
    for i, n in enumerate(nodes):
        comps[find(i)].append(n)
    out = []
    for cid, members in enumerate(comps.values()):
        sides = {}
        for stage in ("PD", "RD"):
            gs = [g for s, g in members if s == stage]
            if gs:
                top = max(file_rank(g["file_id"]) for g in gs)
                sides[stage] = [g for g in gs if file_rank(g["file_id"]) == top]
        if len(sides) < 2:
            continue
        for pg in sides["PD"]:
            # лучший лист из каждого действующего документа РД: этаж бывает и в АР1, и в АР2 (Алтуфьевское)
            by_file = defaultdict(list)
            for rg in sides["RD"]:
                s = _sim(pg, rg)
                if s > 0:
                    by_file[rg["file_id"]].append((s, rg))
            for cands in by_file.values():
                best = max(s for s, _ in cands)
                out.extend((cid, pg, rg) for s, rg in cands if s == best)
    return out


def _area_close(p, r):
    """Площади совпадают с точностью записи: «8,1» и «8,10» — одно и то же (полшага последнего знака),
    «21,28» и «21,30» — нет (в РД Полярной 16 так и отличаются площади МОП)."""
    tol = 0.5 * 10 ** -min(p["prec"], r["prec"]) + 1e-6
    return any(abs(x - y) <= tol for x in p["areas"] for y in r["areas"])


def _fmt_area(a):
    return " / ".join(f"{x:.2f}".rstrip("0").rstrip(".").replace(".", ",") for x in sorted(a)) or "—"


def _rel(p, r):
    return min(abs(x - y) / max(x, y, 0.01) for x in p["areas"] for y in r["areas"])


def diff(pd_rooms, rd_rooms):
    """Расхождения ПД ↔ РД: [{kind, pd, rd}].

    1) Помещение с тем же номером, у которого совпадает назначение или площадь, — то же помещение: сверяются
       назначение («renamed») и площадь («area»).
    2) Остальные ищут пару под другим номером с площадью в пределах 3%: то же назначение — перенумерация
       («renumbered», справка, не кандидат: у Лосевской в РД вставили электрощитовую 0.214, и номера подвала
       сдвинулись на один), другое — «renumbered_renamed» (Алтуфьевское: «23 Помещение» → «22 Комната отдыха»).
    3) Номер есть в обеих стадиях, но ни назначение, ни площадь не совпали и пары нет — «renamed» + «area».
    4) Остальное — «removed» / «added».
    """
    if any("#" in k for k in list(pd_rooms) + list(rd_rooms)):
        return _diff_multiset(pd_rooms, rd_rooms)
    events = []
    same = [k for k in pd_rooms.keys() & rd_rooms.keys()
            if _names_match(pd_rooms[k], rd_rooms[k]) or _area_close(pd_rooms[k], rd_rooms[k])]
    free_pd = [k for k in pd_rooms if k not in same]
    free_rd = [k for k in rd_rooms if k not in same]
    for k in free_pd[:]:
        p = pd_rooms[k]
        cands = [(not _names_match(p, rd_rooms[k2]), _rel(p, rd_rooms[k2]), k2) for k2 in free_rd
                 if p["areas"] and rd_rooms[k2]["areas"] and _rel(p, rd_rooms[k2]) <= RENUMBER_TOL]
        if not cands:
            continue
        renamed, _, k2 = min(cands)
        free_pd.remove(k)
        free_rd.remove(k2)
        events.append({"kind": "renumbered_renamed" if renamed else "renumbered", "pd": p, "rd": rd_rooms[k2]})
    for k in [k for k in free_pd if k in free_rd]:
        same.append(k)
        free_pd.remove(k)
        free_rd.remove(k)
    for k in same:
        p, r = pd_rooms[k], rd_rooms[k]
        if p["names"] and r["names"] and not _names_match(p, r):
            events.append({"kind": "renamed", "pd": p, "rd": r})
        if p["areas"] and r["areas"] and not _area_close(p, r):
            events.append({"kind": "area", "pd": p, "rd": r})
    # «исчезло» / «появилось» — только если в таблице другой стадии вообще есть помещения этой группы номеров:
    # на листе ПД Полярной 16 рядом МОП секций 1 и 2, а лист РД — только секция 2, и 1.1–1.4 не «исчезли»
    rd_pref = {_group_prefix(k) for k in rd_rooms}
    pd_pref = {_group_prefix(k) for k in pd_rooms}
    events += [{"kind": "removed", "pd": pd_rooms[k], "rd": None} for k in free_pd if _group_prefix(k) in rd_pref]
    events += [{"kind": "added", "pd": None, "rd": rd_rooms[k]} for k in free_rd if _group_prefix(k) in pd_pref]
    return events


def _group_prefix(key):
    """«1.109» → «1», «2.4.1» → «2.4», «135» → «1», «23» → «»."""
    num = key.split("#")[0]
    if "." in num:
        return num.rsplit(".", 1)[0]
    return _prefix(num)


def _diff_multiset(pd_rooms, rd_rooms):
    """Таблицы, где номера повторяются (экспликации квартир: 1 Ванная, 2 Жилая комната… в каждой квартире).
    Порядок квартир на листах ПД и РД бывает разный, поэтому строки сравниваются как набор: сначала снимаются
    полностью совпавшие (номер, назначение, площадь), потом пары «тот же номер и назначение» — изменение площади.
    Состав («исчезло» / «появилось») по таким таблицам не выводится: какая квартира чья, по таблице не понять."""
    P, R = list(pd_rooms.values()), list(rd_rooms.values())
    used, rest_p = set(), []
    for p in P:
        j = next((i for i, r in enumerate(R) if i not in used and r["num"] == p["num"] and _names_match(p, r)
                  and _area_close(p, r)), None)
        if j is None:
            rest_p.append(p)
        else:
            used.add(j)
    rest_r = [r for i, r in enumerate(R) if i not in used]
    events = []
    if len(rest_p) + len(rest_r) > 0.3 * (len(P) + len(R)):
        return events                         # таблицы слишком разные: скорее другой типовой этаж, чем правка
    for p in rest_p:
        cands = [r for r in rest_r if r["num"] == p["num"] and _names_match(p, r) and p["areas"] and r["areas"]]
        if cands:
            r = min(cands, key=lambda r: _rel(p, r))
            rest_r.remove(r)
            events.append({"kind": "area", "pd": p, "rd": r})
    return events


def _label(room):
    """Номер помещения для текста: «2#3» → «2 (3-я квартира таблицы)»."""
    num, _, k = room["key"].partition("#")
    return f"{num} ({k}-я по счёту в таблице)" if k else num


def _desc(e):
    p, r = e["pd"], e["rd"]
    pn = lambda x: f"«{x['names'][0] if x['names'] else '?'}» {_fmt_area(x['areas'])} м²"
    return {
        "added": lambda: f"помещение {_label(r)} {pn(r)} есть в РД, в ПД нет",
        "removed": lambda: f"помещение {_label(p)} {pn(p)} есть в ПД, в РД нет",
        "renamed": lambda: f"помещение {_label(p)}: назначение «{p['names'][0]}» → «{r['names'][0]}»",
        "renumbered": lambda: f"{_label(p)} → {_label(r)} {pn(r)}",
        "renumbered_renamed": lambda: f"помещение {_label(p)} {pn(p)} → в РД {_label(r)} {pn(r)}: "
                                      f"другой номер и назначение",
        "area": lambda: f"{_label(p)} «{p['names'][0] if p['names'] else ''}»: {_fmt_area(p['areas'])} → "
                        f"{_fmt_area(r['areas'])} м²",
    }[e["kind"]]()


_sizes = {}


def _box(fid, page, bbox, fragment):
    """Рамка доказательства в долях видимой страницы (как в evidence_boxes): строка таблицы экспликации."""
    if (fid, page) not in _sizes:
        with pymupdf.open(file_path(fid)) as doc:
            r = doc[page - 1].rect
            _sizes[(fid, page)] = (r.width, r.height)
    W, H = _sizes[(fid, page)]
    x0, y0, x1, y1 = bbox
    clamp = lambda v: round(min(1.0, max(0.0, v)), 4)
    return {"bbox_norm": [clamp(x0 / W), clamp(y0 / H), clamp(x1 / W), clamp(y1 / H)],
            "bbox_source": "text_layer", "fragment": fragment}


def _ev(stage, room):
    fid, page, bbox = room["where"][0]
    ev = {"stage": stage, "file_id": fid, "pdf_page_number": page}
    try:
        ev.update(_box(fid, page, bbox, _val(room)))
    except Exception:
        pass
    return ev


def compare(object_id, verbose=False):
    pd_t, rd_t = extract(object_id, "PD", verbose), extract(object_id, "RD", verbose)
    # нет комплекта АР на стадии — «нет документа», как у заглушки скелета (у обучающих в РД АР нет вообще)
    for stage, have, other in (("RD", rd_t, pd_t), ("PD", pd_t, rd_t)):
        if not have and not ar_files(object_id, stage):
            if not other:
                return []
            t = other[0]
            return [{"parameter_code": CODE, "location": "Объект", "violation_label": "MISSING_DOCUMENT",
                     "pd_value": f"{sum(len(x['rows']) for x in other)} помещений" if stage == "RD" else None,
                     "rd_value": f"{sum(len(x['rows']) for x in other)} помещений" if stage == "PD" else None,
                     "evidence": [_page_ev("PD" if stage == "RD" else "RD", t)],
                     "note": f"в {'РД' if stage == 'RD' else 'ПД'} нет комплекта АР — экспликации не с чем сравнить"}]
    return checks_from(pd_t, rd_t)


def _page_ev(stage, g):
    """Лист другой стадии, где помещения нет: рамка — вся таблица."""
    ev = {"stage": stage, "file_id": g["file_id"], "pdf_page_number": g["page"]}
    try:
        ev.update(_box(g["file_id"], g["page"], g["bbox"], g["title"][:80]))
    except Exception:
        pass
    return ev


def checks_from(pd_t, rd_t):
    if not pd_t and not rd_t:
        return []
    if not pd_t or not rd_t:
        t = (pd_t or rd_t)[0]
        stage = "PD" if pd_t else "RD"
        n = sum(len(x["rows"]) for x in pd_t or rd_t)
        return [{"parameter_code": CODE, "location": "Объект", "violation_label": "COMPARISON_IMPOSSIBLE",
                 "pd_value": f"{n} помещений" if pd_t else None, "rd_value": f"{n} помещений" if rd_t else None,
                 "evidence": [_page_ev(stage, t)],
                 "note": f"экспликации помещений найдены только в {'ПД' if pd_t else 'РД'}"}]
    pairs = components(pd_t, rd_t)
    if not pairs:
        t_pd, t_rd = pd_t[0], rd_t[0]
        return [{"parameter_code": CODE, "location": "Объект", "violation_label": "COMPARISON_IMPOSSIBLE",
                 "pd_value": f"{sum(len(t['rows']) for t in pd_t)} помещений",
                 "rd_value": f"{sum(len(t['rows']) for t in rd_t)} помещений",
                 "evidence": [_page_ev("PD", t_pd), _page_ev("RD", t_rd)],
                 "note": "экспликации есть в ПД и в РД, но ни одна таблица РД не совпадает с таблицей ПД по номерам "
                         "и назначениям помещений — сопоставить нельзя"}]
    checks, seen, renum = [], {}, []
    stats = {}                      # (этаж, ключ) → помещение ПД и все варианты площади в РД (итог по объекту)
    area_ev = {}                    # (этаж, ключ) → изменение площади (варианты РД из разных документов — вместе)
    first_pair = None
    for cid, pg, rg in pairs:
        pd_r, rd_r = pg["rooms"], rg["rooms"]
        floor = pg["floor"] or rg["floor"] or _prefix_label(pd_r)
        both = [k for k in pd_r if k in rd_r and pd_r[k]["areas"] and rd_r[k]["areas"]]
        for k in both:              # каждое помещение один раз: экспликация бывает и на плане, и на фрагменте
            st = stats.setdefault((floor, k), {"pd": pd_r[k], "rd": set()})
            st["rd"] |= rd_r[k]["areas"]
        if both and not first_pair:
            first_pair = (pd_r[both[0]], rd_r[both[0]])
        for e in diff(pd_r, rd_r):
            room = e["rd"] or e["pd"]
            if e["kind"] == "renumbered":
                renum.append(_desc(e))
                continue
            if e["kind"] == "area":
                k = (floor or "без этажа", e["pd"]["key"])
                if k in area_ev:    # тот же этаж в другом документе РД (АР1 и АР2 у Алтуфьевского)
                    area_ev[k]["rd"]["areas"] |= e["rd"]["areas"]
                else:
                    area_ev[k] = {**e, "rd": {**e["rd"], "areas": set(e["rd"]["areas"])}, "n": len(pd_r)}
                continue
            loc = (floor + ", " if (_short(room["num"]) or "#" in room["key"]) and floor else "") + _label(room)
            ev = [_ev("PD", e["pd"]) if e["pd"] else _page_ev("PD", pg),
                  _ev("RD", e["rd"]) if e["rd"] else _page_ev("RD", rg)]
            if loc in seen:
                # та же находка на другом листе (экспликация повторена на плане и фрагменте) — ещё одна страница
                have = {(x["stage"], x["file_id"], x["pdf_page_number"]) for x in seen[loc]["evidence"]}
                for x in ev:
                    n_stage = sum(1 for y in seen[loc]["evidence"] if y["stage"] == x["stage"])
                    if (x["stage"], x["file_id"], x["pdf_page_number"]) not in have and n_stage < 3:
                        seen[loc]["evidence"].append(x)
                continue
            seen[loc] = {"parameter_code": CODE, "location": loc, "violation_label": "VIOLATION_PRESENT",
                         "pd_value": _val(e["pd"]), "rd_value": _val(e["rd"]), "evidence": ev,
                         "note": _desc(e) + " (экспликация помещений; кандидат — проверить согласованное "
                                            "изменение ПД)"}
            checks.append(seen[loc])
    n_struct = len(checks)
    # площади — одной строкой на этаж; сначала самые большие изменения: пересчёт отделки даёт десятки правок
    # по 0,1 м², а инспектору важнее «Насосная 40,3 → 38,1»
    by_floor = defaultdict(list)
    for (floor, _), e in area_ev.items():
        by_floor[floor].append(e)
    for floor, evs in sorted(by_floor.items()):
        evs.sort(key=lambda e: -_rel(e["pd"], e["rd"]))
        n_all = max(e["n"] for e in evs)
        pd_sum = sum(min(e["pd"]["areas"]) for e in evs)
        rd_sum = sum(min(e["rd"]["areas"]) for e in evs)
        lst = "; ".join(_desc(e) for e in evs[:12]) + (f"; и ещё {len(evs) - 12}" if len(evs) > 12 else "")
        checks.append({"parameter_code": CODE, "location": f"Площади помещений, {floor}",
                       "violation_label": "VIOLATION_PRESENT",
                       "pd_value": f"{pd_sum:.2f}".replace(".", ",") + f" м² ({len(evs)} пом.)",
                       "rd_value": f"{rd_sum:.2f}".replace(".", ",") + f" м² ({len(evs)} пом.)",
                       "evidence": [_ev("PD", evs[0]["pd"]), _ev("RD", evs[0]["rd"])],
                       "note": f"изменены площади {len(evs)} помещений (в таблице {n_all}), крупнейшие: {lst}"})
    return [_summary(stats, pairs, first_pair, n_struct, len(area_ev), renum, bool(checks))] + checks


# Триггер PZ-003 в матрице: «сокращение полезной площади в РД в пользу технических зон». Полезная площадь — все
# помещения, кроме лестничных клеток, лифтовых шахт и пандусов и кроме помещений инженерного оборудования и сетей
# (так её определяют своды правил); последние и есть «технические зоны».
TECH_RE = re.compile(r"техническ|\bтех\.|венткамер|вент\.\s*камер|\bитп\b|теплов\w* пункт|насосн|электрощит|щитов|"
                     r"трансформатор|кабельн|коммуникац|инженерн|машинн|водомерн|узел ввода|\bшахт|дымоудал|подпор|"
                     r"форкамер|мусорокамер|серверн", re.I)
NOT_USEFUL_RE = re.compile(r"лестн|^лк\b|^лифт\b|лифтов\w* шахт|шахт\w* лифт|пандус", re.I)


def _kind(room):
    name = room["names"][0] if room["names"] else ""
    if TECH_RE.search(name):
        return "tech"
    return "other" if NOT_USEFUL_RE.search(name) else "useful"


def _summary(stats, pairs, first_pair, n_struct, n_area, renum, any_checks):
    """Итог «Объект» по триггеру матрицы. Если документы РД расходятся между собой (у помещения несколько площадей),
    в сумму идёт ближайшая к ПД: итог по объекту должен ошибаться в сторону «нарушения нет»."""
    sums = {"useful": [0.0, 0.0], "tech": [0.0, 0.0], "other": [0.0, 0.0]}
    for st in stats.values():
        pd_a = min(st["pd"]["areas"])
        rd_a = min(st["rd"], key=lambda x: abs(x - pd_a))
        sums[_kind(st["pd"])][0] += pd_a
        sums[_kind(st["pd"])][1] += rd_a
    (u_pd, u_rd), (t_pd, t_rd) = sums["useful"], sums["tech"]
    violation = u_pd > 0 and u_rd < u_pd * 0.99 and t_rd > t_pd
    fmt = lambda x: f"{x:.2f}".replace(".", ",")
    note = (f"сверено {len(stats)} помещений по {len({id(p) for _, p, _ in pairs})} листам экспликаций (последняя ПД "
            f"с последней РД): полезная площадь {fmt(u_pd)} → {fmt(u_rd)} м², технические помещения {fmt(t_pd)} → "
            f"{fmt(t_rd)} м²" + ("; полезная сократилась больше чем на 1% в пользу технических — кандидат"
                                 if violation else "; сокращения полезной площади в пользу технических зон нет")
            + (f"; расхождений по помещениям: {n_struct} по составу и назначению, {n_area} по площади "
               f"(отдельные строки)" if any_checks else "; номера, назначения и площади совпадают")
            + (f"; перенумерованы без смены назначения и площади: {len(renum)} ({'; '.join(renum[:6])}"
               f"{' …' if len(renum) > 6 else ''})" if renum else ""))
    return {"parameter_code": CODE, "location": "Объект",
            "violation_label": "VIOLATION_PRESENT" if violation else "NO_VIOLATION",
            "pd_value": f"полезная {fmt(u_pd)} м², технические {fmt(t_pd)} м² ({len(stats)} пом.)",
            "rd_value": f"полезная {fmt(u_rd)} м², технические {fmt(t_rd)} м²",
            "evidence": [_ev("PD", first_pair[0]), _ev("RD", first_pair[1])] if first_pair else [],
            "note": note}


def _prefix_label(rooms_d):
    prefixes = sorted({_prefix(r["num"]) for r in rooms_d.values() if _prefix(r["num"])})
    return (prefixes[0] + " этаж") if len(prefixes) == 1 else ""


def _prefix(num):
    """Этаж в номере помещения: «1.109» → «1», «135» → «1», «009.1» → «0», «2.4.1» → «2»; короткие номера — «»."""
    head = num.split(".")[0] if "." in num else num
    if re.fullmatch(r"\d{3,4}[а-я]?", head):
        return str(int(head[:-2]))
    return head if "." in num else ""


def _val(room):
    if not room:
        return None
    return f"{_label(room)} «{room['names'][0] if room['names'] else '?'}» {_fmt_area(room['areas'])} м²"


# ---------------------------------------------------------------- CLI и сверка с пилотом

PILOT_CANDIDATES = paths.ROOT / "02_МЕТОДИКА" / "domain_violation_reannotation_20260817" / "annotations.jsonl"


def pilot_eval(object_id, checks):
    """Совпадение с кандидатами организаторов: их страницы ПД и РД есть среди наших доказательств-кандидатов."""
    code = object_id.replace("OBJ-PILOT-", "")
    marks = [json.loads(l) for l in open(PILOT_CANDIDATES, encoding="utf-8")]
    marks = [m for m in marks if m["source_file_id"].startswith(code + "-")]
    if not marks:
        return None
    ours = {(e["file_id"], e["pdf_page_number"]) for c in checks if c["violation_label"] == "VIOLATION_PRESENT"
            and c["location"] != "Объект" for e in c["evidence"]}
    status = marks[0]["status"]
    hit = [(m["stage"], m["source_file_id"], m["page"], (m["source_file_id"], m["page"]) in ours) for m in marks]
    return status, hit


def main(argv):
    if argv and argv[0] == "--pilot":
        man = paths.read_jsonl(paths.MANIFEST)
        objects = sorted({r["object_id"] for r in man if r["object_id"].startswith("OBJ-PILOT-")})
        objects = [o for o in objects if not argv[1:] or any(a in o for a in argv[1:])]
    else:
        objects = argv[:1] or ["OBJ-PILOT-ALT79B"]
    for obj in objects:
        print(f"\n===== {obj}", flush=True)
        pd_t, rd_t = extract(obj, "PD"), extract(obj, "RD")
        print(f"  таблиц: ПД {len(pd_t)} ({sum(len(t['rows']) for t in pd_t)} строк), "
              f"РД {len(rd_t)} ({sum(len(t['rows']) for t in rd_t)} строк)")
        checks = checks_from(pd_t, rd_t)
        for c in checks:
            ev = ", ".join(f"{e['stage']} {e['file_id']}:{e['pdf_page_number']}" for e in c["evidence"])
            print(f"  {c['violation_label'][:12]:12} {c['location'][:34]:34} [{ev}]")
            print(f"      {c.get('note', '')[:300]}")
        res = pilot_eval(obj, checks)
        if res:
            status, hit = res
            print(f"  разметка организаторов ({status}):")
            for st, fid, page, ok in hit:
                print(f"     {st} {fid}:{page}  {'есть среди наших кандидатов' if ok else 'НЕТ'}")


if __name__ == "__main__":
    main(sys.argv[1:])
