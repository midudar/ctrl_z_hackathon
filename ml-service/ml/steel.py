"""KR-056 «Марка стали и класс прочности металлопроката»: сталь по элементам в ПД (текст КР, спецификации
металлопроката) и РД (КМ, КЖ, распорная система) и сравнение по пределу текучести.

Триггер матрицы — «подмена марки стали на менее прочную (например, С345 на С245)». Марки сводятся к пределу
текучести (С245 → 245 МПа, Ст3сп → 245, сталь 20 → 245, 09Г2С → 345), элемент — по словам в той же строке, в соседних
строках, иначе по названию комплекта РД («Распорная система», «Лестницы»); не нашёлся — общие «Металлоконструкции».

Правило (как у класса бетона, но осторожнее): «нарушение» — только у названного элемента и только если вся сталь
элемента в РД слабее всей стали в ПД; самая слабая сталь РД не слабее самой слабой в ПД — «нет нарушения»; иначе
«сравнить нельзя» с пояснением (в спецификациях у одного элемента бывают разные марки для разных деталей).
Разрабатывалось на Новослободской (кандидат организаторов по распорной системе: ПД Ст2сп / С245 → РД Ст20 / С245 —
по прочности не слабее, у организаторов он «до экспертизы») и объектах пилота; Речников правила не подбирал.

    python -m ml.steel OBJ-NOVOSLOBODSKAYA
"""
import re
import sys
from collections import defaultdict

import pymupdf

from ml import paths, registry
from ml.concrete import context_of, page_lines, set_name
from ml.pages import file_path

CODE = "KR-056"
GENERIC = "Металлоконструкции"

# Марки стали: С245 / С345-3 / С345К (ГОСТ 27772), Ст3сп5 / ВСт3сп / Ст2сп (ГОСТ 380), сталь 20 / Ст20 (ГОСТ 1050),
# низколегированные, европейские S235…S355. Кириллица и латиница вперемешку («C245»).
# «Ст. 6», «ст.40» в тексте — это статьи и пункты, а не сталь: марку СтN берём только с раскислением («Ст3сп»,
# «ВСт3пс5») или после слова «сталь»; углеродистую — слитно («Ст20») или после «сталь» («сталь 20»).
STEEL_RE = re.compile(
    r"(?<![А-ЯA-Zа-яa-z\d])(?:"
    r"(?P<c>[СC])\s?(?P<cn>235|245|255|275|285|345|355|375|390|440|590)(?:-\d|К)?"
    r"|(?:В-?)?[СC]т\s?(?P<st>[0-6])\s?(?P<deox>[сС][пП]|[пП][сС]|[кК][пП])\d?"
    r"|(?:[СCсc]таль|[сc]тали)\s+(?:марки\s+)?(?:В-?)?[СC]т\.?\s?(?P<st3>[0-6])"
    r"(?:\s?(?P<deox3>[сС][пП]|[пП][сС]|[кК][пП])\d?)?(?![\d.,])"          # «стали марки Ст20» — не Ст2
    r"|[СC]т(?P<st2>10|15|20|25|35|45)(?![\d.,])"
    r"|(?:[СCсc]таль|[сc]тали)\s+(?:марки\s+)?(?P<carbon>10|15|20|25|35|45)(?![\d.,])"
    r"|(?P<alloy>09Г2С|10Г2С1|10ХСНД|15ХСНД|14Г2АФ)"
    r"|S\s?(?P<eu>235|275|355|420|460))(?![\d])")
ST_YIELD = {0: 185, 1: 195, 2: 225, 3: 245, 4: 265, 5: 285, 6: 315}                  # ГОСТ 380, до 20 мм
CARBON_YIELD = {10: 205, 15: 225, 20: 245, 25: 275, 30: 295, 35: 315, 40: 335, 45: 355}  # ГОСТ 1050
ALLOY_YIELD = {"09Г2С": 345, "10Г2С1": 345, "10ХСНД": 390, "15ХСНД": 345, "14Г2АФ": 390}

ELEMENTS = [
    ("Форшахта", re.compile(r"форшахт\w*", re.I)),
    ("Стена в грунте", re.compile(r"стен\w*\s+в\s+грунт\w*|(?<![А-Яа-яA-Za-z])СВГ(?![А-Яа-яA-Za-z])|буросекущ\w*",
                                  re.I)),
    # «ограждение котлована» — не перила: распорки, пояса и трубы крепления котлована (Новослободская)
    ("Распорная система", re.compile(r"распор\w*|обвязочн\w*\s+пояс|подкос\w*|котлован\w*", re.I)),
    ("Фермы", re.compile(r"ферм\w*", re.I)),
    ("Прогоны", re.compile(r"прогон\w*", re.I)),
    ("Колонны", re.compile(r"колонн\w*", re.I)),
    ("Балки", re.compile(r"\bбалк\w*|ригел\w*", re.I)),
    ("Лестницы", re.compile(r"лестниц\w*|стремянк\w*", re.I)),
    ("Ограждения", re.compile(r"перил\w*|огражден\w*\s+(?:лестниц|кровл|балкон|площад|марш|пандус)", re.I)),
    ("Связи", re.compile(r"\bсвяз(?:и|ей|ям)\b", re.I)),
    ("Закладные детали", re.compile(r"закладн\w*", re.I)),
]
# сталь арматуры (А240 из Ст3сп) и крепежа — не металлопрокат
SKIP_RE = re.compile(r"арматур|\b[АA]\s?(?:240|400|500)|болт|гайк|шайб|анкер", re.I)


def grade(m):
    """(марка, предел текучести, МПа) по совпадению STEEL_RE."""
    if m.group("cn"):
        return "С" + m.group("cn"), int(m.group("cn"))
    if m.group("st") or m.group("st3"):
        n = int(m.group("st") or m.group("st3"))
        return f"Ст{n}{(m.group('deox') or m.group('deox3') or '').lower()}", ST_YIELD[n]
    if m.group("carbon") or m.group("st2"):
        n = int(m.group("carbon") or m.group("st2"))
        return f"сталь {n}", CARBON_YIELD[n]
    if m.group("alloy"):
        return m.group("alloy"), ALLOY_YIELD[m.group("alloy")]
    return "S" + m.group("eu"), int(m.group("eu"))


def element_of(line, window, file_id):
    """Строка → название комплекта → соседние строки: в спецификации КМ марка стоит одна в ячейке, и сосед по окну
    бывает из другой части листа, а комплект РД посвящён одной конструкции («Распорная система … РС»)."""
    for source in (line, set_name(file_id) if file_id else "", window):
        for name, rx in ELEMENTS:
            if rx.search(source):
                return name
    return GENERIC


def extract(file_id):
    """[{element, grade, yield, page, line, bbox}] по текстовому слою файла."""
    out = []
    try:
        doc = pymupdf.open(file_path(file_id))
    except Exception:
        return out
    for pno, page in enumerate(doc, 1):
        if not STEEL_RE.search(page.get_text()):
            continue
        lines = page_lines(page)
        for i, (text, bbox) in enumerate(lines):
            found = list(STEEL_RE.finditer(text))
            if not found:
                continue
            window = context_of(lines, i, radius=2)
            if SKIP_RE.search(text) or (SKIP_RE.search(window) and not any(rx.search(text) for _, rx in ELEMENTS)):
                continue
            element = element_of(text, window, file_id)
            for m in found:
                g, y = grade(m)
                out.append({"element": element, "grade": g, "yield": y, "file_id": file_id, "page": pno,
                            "line": text.strip()[:160], "bbox": list(bbox), "direct": element != GENERIC and
                            any(rx.search(text) for _, rx in ELEMENTS)})
    return out


def stage_records(object_id, stage):
    return [r for fid in registry.files(object_id, stage, "KR") for r in extract(fid)]


def _fmt(recs):
    return ", ".join(g for g, _ in sorted({(r["grade"], r["yield"]) for r in recs}, key=lambda x: (x[1], x[0])))


def _ev(stage, recs, weakest=False):
    """Страница-доказательство: прямое упоминание элемента в строке, при нарушении — самая слабая сталь."""
    key = (lambda r: (r["yield"], not r["direct"])) if weakest else (lambda r: (not r["direct"], r["yield"]))
    r = min(recs, key=key)
    ev = {"stage": stage, "file_id": r["file_id"], "pdf_page_number": r["page"]}
    try:
        from ml.evidence_boxes import text_box
        ev.update(text_box(r["file_id"], r["page"], r["bbox"], r["line"]))
    except Exception:
        pass
    return ev


def compare(object_id):
    pd, rd = stage_records(object_id, "PD"), stage_records(object_id, "RD")
    if not pd and not rd:
        return []
    for stage, have, other in (("RD", rd, pd), ("PD", pd, rd)):
        if not have and not registry.files(object_id, stage, "KR"):
            # нет комплекта КР / КМ на стадии — «нет документа», как у заглушки скелета (Тюменская: в РД нет КР)
            return [{"parameter_code": CODE, "location": GENERIC, "violation_label": "MISSING_DOCUMENT",
                     "pd_value": _fmt(other) if stage == "RD" else None, "rd_value": _fmt(other) if stage == "PD" else None,
                     "evidence": [_ev("PD" if stage == "RD" else "RD", other)],
                     "note": f"в {'РД' if stage == 'RD' else 'ПД'} нет комплекта КР / КМ — марки стали не с чем сравнить"}]
    by = {s: defaultdict(list) for s in ("PD", "RD")}
    for s, recs in (("PD", pd), ("RD", rd)):
        for r in recs:
            by[s][r["element"]].append(r)
            if r["element"] != GENERIC:
                by[s][GENERIC].append(r)          # «Металлоконструкции» — вся сталь стадии
    checks = []
    for element in sorted(set(by["PD"]) | set(by["RD"]), key=lambda e: (e == GENERIC, e)):
        p, r = by["PD"].get(element, []), by["RD"].get(element, [])
        if not p or not r:
            if element == GENERIC:
                have, stage = (p, "PD") if p else (r, "RD")
                checks.append({"parameter_code": CODE, "location": GENERIC, "violation_label": "COMPARISON_IMPOSSIBLE",
                               "pd_value": _fmt(p) or None, "rd_value": _fmt(r) or None,
                               "evidence": [_ev(stage, have)],
                               "note": f"марки стали найдены только в {'ПД' if p else 'РД'}"})
            continue
        pmin, pmax = min(x["yield"] for x in p), max(x["yield"] for x in p)
        rmin, rmax = min(x["yield"] for x in r), max(x["yield"] for x in r)
        if element != GENERIC and rmax < pmin:
            label, note = "VIOLATION_PRESENT", (f"{element}: вся сталь в РД ({_fmt(r)}) слабее стали в ПД ({_fmt(p)}) — "
                                                f"подмена на менее прочную (триггер матрицы); кандидат")
            ev = [_ev("PD", p), _ev("RD", r, weakest=True)]
        elif rmin >= pmin:
            label, note = "NO_VIOLATION", f"самая слабая сталь в РД ({_fmt([min(r, key=lambda x: x['yield'])])}) не " \
                                          f"слабее самой слабой в ПД ({_fmt([min(p, key=lambda x: x['yield'])])})"
            ev = [_ev("PD", p), _ev("RD", r)]
        else:
            label, note = "COMPARISON_IMPOSSIBLE", (f"в РД есть сталь слабее, чем в ПД ({_fmt(r)} против {_fmt(p)}), "
                                                    f"но не у всех деталей — сверить по спецификации элемента")
            ev = [_ev("PD", p), _ev("RD", r, weakest=True)]
        checks.append({"parameter_code": CODE, "location": element, "violation_label": label,
                       "pd_value": _fmt(p), "rd_value": _fmt(r), "evidence": ev, "note": note})
    return checks


def main(argv):
    obj = argv[0] if argv else "OBJ-NOVOSLOBODSKAYA"
    for stage in ("PD", "RD"):
        recs = stage_records(obj, stage)
        by = defaultdict(list)
        for r in recs:
            by[r["element"]].append(r)
        print(f"{stage}: записей {len(recs)}")
        for e, rs in sorted(by.items()):
            print(f"   {e:20} {_fmt(rs):40} напр. {rs[0]['file_id']}:{rs[0]['page']} «{rs[0]['line'][:70]}»")
    for c in compare(obj):
        ev = ", ".join(f"{e['stage']} {e['file_id']}:{e['pdf_page_number']}" for e in c["evidence"])
        print(f"{c['violation_label']:22} {c['location']:20} ПД {c['pd_value']} | РД {c['rd_value']} [{ev}]")


if __name__ == "__main__":
    main(sys.argv[1:])
