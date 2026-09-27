"""PPM-103 «Пределы огнестойкости противопожарных дверей и ворот (EI)»: ПД (ПБ / ППМ, АР) ↔ РД (АР).

Триггер матрицы — «снижение предела огнестойкости (например, EI-60 на EI-30) в РД». Значения — EI / EIS / EIW с числом
минут (EI 30, EIS60, «EI-30»). Значение относится к двери, если ближайшее к нему в строке слово — дверь, ворота, люк,
полотно, заполнение проёма или марка двери (ДПС, ДПМ, «Д-1»), а не перегородка, стена, перекрытие, клапан, воздуховод,
окно или проходка: в ПД одна фраза часто говорит о перегородке и о двери сразу («перегородками 1-го типа (EI 45).
Заполнение проемов … дверями 2-го типа (EI 30)»). REI — несущие конструкции, не берутся.

В РД значения бывают только метками на планах АР («EI60», «EIS60 (EIWS60)») — у стен и окон такие метки тоже есть, поэтому
метки подтверждают только «нет нарушения» и никогда не дают кандидата.

Правило («сначала точность», как у стали KR-056): «нарушение» — в РД не меньше MIN_RD_DOORS явных строк о дверях, и все
их пределы ниже самого высокого предела дверей в ПД; самый высокий предел ПД в РД есть (в строках о дверях или метках) —
«нет нарушения»; иначе «сравнить нельзя». Разрабатывалось на объектах пилота (у обучающих РД АР нет).

    python -m ml.doors <object_id>
"""
import re
import sys

import pymupdf

from ml import paths, revisions
from ml.concrete import page_lines
from ml.pages import file_path

CODE = "PPM-103"
LOCATION = "Противопожарные двери и ворота"
MIN_RD_DOORS = 3

EI_RE = re.compile(r"(?<![A-Za-zА-Яа-я])(?P<r>R)?E\s?I\s?(?P<sw>[SW]{0,2})\s?[-–]?\s?(?P<v>15|30|45|60|90|120|150|180)(?!\d)")
DOOR_WORD_RE = re.compile(r"двер\w*|ворот\w*|люк\w*|полотн\w*|калитк\w*|заполнени\w*\s+(?:дверн\w*\s+)?проем\w*|"
                          r"(?<![А-Яа-я])(?:ДПС|ДПМ|ДМП|ДП|ДМ)(?![а-я])|(?<![А-Яа-я])Д\s?-\s?\d", re.I)
OTHER_WORD_RE = re.compile(r"перегород\w*|стен\w*|перекрыт\w*|покрыти\w*|конструкц\w*|клапан\w*|воздуховод\w*|"
                           r"окн\w*|оконн\w*|витраж\w*|проходк\w*|кабел\w*|шахт\w*|штор\w*|занавес\w*|экран\w*|"
                           r"колонн\w*|балк\w*|лестничн\w*\s+марш\w*|отсек\w*|преград\w*", re.I)
LABEL_RE = re.compile(r"[\s()\[\]EISW0-9–-]{3,40}")          # строка — только метки «EI60», «EIS60 (EIWS60)»
PD_FILE_RE = re.compile(r"[-_ (.](ПБ|ППМ|МПБ|АР|ПЗ|ОПЗ)[\s._\d)-]|пожарн", re.I)
RD_FILE_RE = re.compile(r"[-_ (.](АР|АС|АИ)[\s._\d)-]", re.I)
STAGES = {"PD": {"PD"}, "RD": {"RD", "RD_ID_MIXED"}}


def door_files(object_id, stage):
    rows = [r for r in paths.read_jsonl(paths.MANIFEST) if r["object_id"] == object_id and r["extension"] == ".pdf"
            and r["stage"] in STAGES[stage]]
    keep = {r["file_id"] for r in revisions.latest(rows)[0]}
    rx, secs = (PD_FILE_RE, {"PB", "AR"}) if stage == "PD" else (RD_FILE_RE, {"AR"})
    return [r["file_id"] for r in rows if r["file_id"] in keep and (r["section"] in secs or rx.search(" " + r["relative_path"]))]


# конец фразы: точка перед русской заглавной или в конце строки, «;» — ключевое слово за ним к значению не относится.
# Сокращения внутри фразы («ДПС 01 2100-1100 пр. EI30», «л. EI30») концом фразы не считаются
BREAK_RE = re.compile(r";|[.!?](?=\s+[А-ЯЁ]|\s*$)")


def _keywords(text):
    return sorted([(m.start(), m.end(), kind) for kind, rx in (("door", DOOR_WORD_RE), ("other", OTHER_WORD_RE))
                   for m in rx.finditer(text)])


def _split(text, a, b):
    """Между позициями a и b строки text есть конец фразы."""
    return any(a <= m.start() < b for m in BREAK_RE.finditer(text))


def _is_door(prev, line, start, end):
    """Значение относится к двери: ключевое слово той же фразы — дверное (а не перегородка, стена, клапан…).

    Сначала — ближайшее слово перед значением («дверями 2-го типа (EI 30)»), затем хвост предыдущей строки, если фраза
    переносится («…перегородки 1-го типа с пределом / огнестойкости не ниже EI 45, с заполнением дверями»), и только
    потом — слово после значения. Через конец фразы не смотрим: «перегородками 1-го типа (EI 45). Заполнение проемов
    …» — это перегородка, «не менее EI 45. Двери таких помещений…» — ни то ни другое."""
    before = [k for k in _keywords(line) if k[1] <= start and not _split(line, k[1], start)]
    if before:
        return before[-1][2] == "door"
    prev = (prev or "").rstrip()
    if prev and not _split(line, 0, start):
        tail = [k for k in _keywords(prev) if not _split(prev, k[1], len(prev))]
        if tail:
            return tail[-1][2] == "door"
    after = [k for k in _keywords(line) if k[0] >= end and not _split(line, end, k[0])]
    return bool(after) and after[0][2] == "door"


def extract(file_id):
    """[{value, cls, kind: door|label, …}] по текстовому слою."""
    out = []
    try:
        doc = pymupdf.open(file_path(file_id))
    except Exception:
        return out
    for pno, page in enumerate(doc, 1):
        if "EI" not in page.get_text().replace(" ", "").upper():
            continue
        lines = page_lines(page)
        for i, (text, bbox) in enumerate(lines):
            label = bool(LABEL_RE.fullmatch(text.strip()))
            for m in EI_RE.finditer(text):
                if m.group("r"):
                    continue                            # REI — несущие конструкции
                if label:
                    kind = "label"
                elif _is_door(lines[i - 1][0] if i else "", text, m.start(), m.end()):
                    kind = "door"
                else:
                    continue
                out.append({"value": int(m.group("v")), "cls": "EI" + m.group("sw").upper(), "kind": kind,
                            "file_id": file_id, "page": pno, "line": text.strip()[:160], "bbox": list(bbox)})
    return out


def stage_records(object_id, stage):
    return [r for fid in door_files(object_id, stage) for r in extract(fid)]


def _fmt(recs):
    return ", ".join(f"{c}{v}" for v, c in sorted({(r["value"], r["cls"]) for r in recs}))


def _ev(stage, recs, key):
    r = min(recs, key=key)
    ev = {"stage": stage, "file_id": r["file_id"], "pdf_page_number": r["page"]}
    try:
        from ml.evidence_boxes import text_box
        ev.update(text_box(r["file_id"], r["page"], r["bbox"], r["line"]))
    except Exception:
        pass
    return ev


def compare(object_id):
    pd = [r for r in stage_records(object_id, "PD") if r["kind"] == "door"]   # требования ПД — только строки о дверях
    rd_all = stage_records(object_id, "RD")
    rd_doors = [r for r in rd_all if r["kind"] == "door"]
    if not pd:
        if not rd_all:
            return []
        return [{"parameter_code": CODE, "location": LOCATION, "violation_label": "COMPARISON_IMPOSSIBLE",
                 "pd_value": None, "rd_value": _fmt(rd_all), "evidence": [_ev("RD", rd_all, lambda r: -r["value"])],
                 "note": "в тексте ПД пределы огнестойкости дверей не найдены"}]
    top = max(r["value"] for r in pd)
    strongest = lambda r: (-r["value"], r["kind"] != "door", r["page"])
    if not rd_all:
        files = bool(door_files(object_id, "RD"))
        return [{"parameter_code": CODE, "location": LOCATION,
                 "violation_label": "COMPARISON_IMPOSSIBLE" if files else "MISSING_DOCUMENT",
                 "pd_value": _fmt(pd), "rd_value": None, "evidence": [_ev("PD", pd, strongest)],
                 "note": ("в РД комплект АР есть, но пределы огнестойкости дверей в тексте не найдены (чертежи без "
                          "текстового слоя)") if files else "в РД нет комплекта АР"}]
    rd_top = max(r["value"] for r in rd_all)
    if rd_top >= top:
        label = "NO_VIOLATION"
        src = "в строках о дверях" if any(r["value"] >= top for r in rd_doors) else "по меткам на планах АР"
        note = f"наибольший предел огнестойкости дверей ПД ({top} мин) в РД есть ({src}); ПД: {_fmt(pd)}; РД: {_fmt(rd_all)}"
        ev = [_ev("PD", pd, strongest), _ev("RD", rd_all, strongest)]
    elif len(rd_doors) >= MIN_RD_DOORS:
        label = "VIOLATION_PRESENT"
        note = (f"в ПД двери с пределом огнестойкости до {top} мин ({_fmt(pd)}), в РД у всех дверей ниже — до {rd_top} мин "
                f"({_fmt(rd_all)}): снижение предела огнестойкости (триггер матрицы); кандидат")
        ev = [_ev("PD", pd, strongest), _ev("RD", rd_doors, strongest)]
    else:
        label = "COMPARISON_IMPOSSIBLE"
        note = (f"в ПД двери до {top} мин ({_fmt(pd)}), в РД найдено только до {rd_top} мин ({_fmt(rd_all)}) — строк о "
                f"дверях в РД мало, сверить по спецификации дверей")
        ev = [_ev("PD", pd, strongest), _ev("RD", rd_all, strongest)]
    return [{"parameter_code": CODE, "location": LOCATION, "violation_label": label,
             "pd_value": _fmt(pd), "rd_value": _fmt(rd_all), "evidence": ev, "note": note}]


def main(argv):
    obj = argv[0] if argv else "OBJ-PILOT-LOS3A"
    for stage in ("PD", "RD"):
        recs = stage_records(obj, stage)
        doors = [r for r in recs if r["kind"] == "door"]
        print(f"{stage}: файлов {len(door_files(obj, stage))} | о дверях: {_fmt(doors)} ({len(doors)}) | "
              f"метки: {_fmt([r for r in recs if r['kind'] == 'label'])}")
        for r in list({r["line"]: r for r in doors}.values())[:8]:
            print(f"      {r['file_id']}:{r['page']} [{r['cls']}{r['value']}] {r['line'][:110]}")
    for c in compare(obj):
        ev = ", ".join(f"{e['stage']} {e['file_id']}:{e['pdf_page_number']}" for e in c["evidence"])
        print(f"{c['violation_label']:22} ПД {c['pd_value']} | РД {c['rd_value']} [{ev}]\n      {c['note']}")


if __name__ == "__main__":
    main(sys.argv[1:])
