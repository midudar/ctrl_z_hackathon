"""IOS3-075 «Материал и тип канализационных труб и фасонных частей»: ПД (ИОС3 — водоотведение) ↔ РД (ВК / НВК).

Триггер матрицы — «самовольная замена малошумных или чугунных труб на тонкостенный ПВХ». Материалы сводятся к
классам (чугун: ВЧШГ, SML; малошумные: «с пониженным уровнем шума», Raupiano, Skolan…; ПВХ; ПП; ПЭ; сталь), система —
по словам строки и соседних строк: К2 (водосток, кровельные воронки), К3, иначе К1 (бытовая канализация).
Берутся только внутренние сети: строки про наружные и внутриплощадочные сети, водопровод, изоляцию, футляры, гильзы,
«технические» трубы и дренаж отбрасываются.

Правило: «нарушение» — в ПД у системы есть чугун или малошумные трубы, а в РД их не осталось и есть ПВХ (ровно
триггер); «нет нарушения» — чугун / малошумные из ПД в РД сохранились и ПВХ не появился; иначе «сравнить нельзя»
с перечнем материалов обеих стадий. Разрабатывалось на объектах пилота (у обучающих РД ВК — только сканы).

    python -m ml.pipes <object_id>
"""
import re
import sys
from collections import defaultdict

import pymupdf

from ml import paths, revisions
from ml.concrete import context_of, page_lines
from ml.pages import file_path

CODE = "IOS3-075"

MATERIALS = [
    ("чугун", re.compile(r"чугун\w*|\bSML\b|ВЧШГ", re.I)),
    ("малошумные", re.compile(r"малошумн\w*|пониженн\w*\s+уровн\w*\s+шум\w*|шумопоглощ\w*|Raupiano|Skolan|"
                              r"Sinikon\s+Comfort|Comfort\s+Plus|Политрон|Polytron|Stilte|Silent", re.I)),
    ("ПВХ", re.compile(r"(?<![А-Яа-я])Н?ПВХ(?![А-Яа-я])|поливинилхлорид\w*|\bPVC", re.I)),
    ("ПП", re.compile(r"(?<![А-Яа-я])ПП(?![А-Яа-я])|полипропилен\w*|\bPP\b", re.I)),
    ("ПЭ", re.compile(r"(?<![А-Яа-я])(?:ПЭ|ПНД)(?![А-Яа-я])|полиэтилен\w*|\bPE\b", re.I)),
    ("сталь", re.compile(r"стальн\w*|оцинкован\w*", re.I)),
]
PREMIUM = {"чугун", "малошумные"}
SEWER_RE = re.compile(r"канализ\w*|водоотвод\w*|водосток\w*|(?<![А-Яа-я])К\s?[123](?:\.\d+)?(?![\d])|стояк\w*|"
                      r"выпуск\w*|раструбн\w*|безраструбн\w*|SML|ВЧШГ|трап\w*|ревизи\w*|отступ\w*", re.I)
SKIP_RE = re.compile(r"водоснабж\w*|водопровод\w*|(?<![А-Яа-я])(?:В|Т)\s?[1-4](?![\d])|ХВС|ГВС|горяч\w*|отоплен\w*|"
                     r"изоляц\w*|теплоизол\w*|сшит\w*|PP-?R|стекловолокн\w*|гильз\w*|техническ\w*|кабел\w*|дренаж\w*|"
                     r"дрен\b|Перфокор|Корсис|наружн\w*|внутриплощадочн\w*|площадочн\w*|колодц\w*|самотечн\w*\s+сет\w*|"
                     r"напорн\w*\s+сет\w*|демонтаж\w*|пожар\w*|спринклер\w*", re.I)
CASE_RE = re.compile(r"в\s+стальн\w*\s+(?:футляр|гильз)\w*", re.I)     # «ВЧШГ … в стальном футляре»: футляр — не труба
SYSTEM_K2_RE = re.compile(r"водосток\w*|ливнев\w*|кровельн\w*\s+ворон\w*|(?<![А-Яа-я])К\s?2(?![\d])", re.I)
SYSTEM_K3_RE = re.compile(r"(?<![А-Яа-я])К\s?3(?![\d])|производствен\w*", re.I)
FILE_RE = re.compile(r"[-_ (–.](ВК|НВК|ИОС\s?3|ИОС\s?2|ИОС\s?5\.[23])[\s._\d)–-]|водоотвед|канализац|водоснабж", re.I)
STAGES = {"PD": {"PD"}, "RD": {"RD", "RD_ID_MIXED"}}


def vk_files(object_id, stage):
    rows = [r for r in paths.read_jsonl(paths.MANIFEST) if r["object_id"] == object_id and r["extension"] == ".pdf"
            and r["stage"] in STAGES[stage]]
    keep = {r["file_id"] for r in revisions.latest(rows)[0]}
    return [r["file_id"] for r in rows if r["file_id"] in keep and (r["section"] == "VK" or
                                                                   FILE_RE.search(" " + r["relative_path"]))]


SYSTEM_MARK_RE = re.compile(r"(?<![А-Яа-я])К\s?(\d{1,2})(?:[.\-]\d+)?(?![\d])")


def system_of(text):
    """Марка системы: явная в тексте («К2», «Выпуск К4-1» → К4), иначе по словам (водосток → К2), иначе К1."""
    m = SYSTEM_MARK_RE.search(text)
    if m:
        return "К" + m.group(1)
    if SYSTEM_K2_RE.search(text):
        return "К2"
    if SYSTEM_K3_RE.search(text):
        return "К3"
    return "К1"


def extract(file_id):
    """[{system, material, page, line}] по текстовому слою."""
    out = []
    try:
        doc = pymupdf.open(file_path(file_id))
    except Exception:
        return out
    for pno, page in enumerate(doc, 1):
        txt = page.get_text()
        if not SEWER_RE.search(txt) or not any(rx.search(txt) for _, rx in MATERIALS):
            continue
        lines = page_lines(page)
        for i, (text, bbox) in enumerate(lines):
            clean = CASE_RE.sub(" ", text)
            mats = [name for name, rx in MATERIALS if rx.search(clean)]
            if not mats or SKIP_RE.search(clean):
                continue
            window = context_of(lines, i, radius=2)
            if not (SEWER_RE.search(clean) or (re.search(r"труб\w*|трубопровод\w*", clean, re.I)
                                               and SEWER_RE.search(window))):
                continue
            if SKIP_RE.search(window) and not SEWER_RE.search(clean):
                continue
            explicit = SYSTEM_MARK_RE.search(clean) or SYSTEM_K2_RE.search(clean) or SYSTEM_K3_RE.search(clean)
            system = system_of(clean) if explicit else system_of(window)
            for m in mats:
                out.append({"system": system, "material": m, "file_id": file_id, "page": pno,
                            "line": text.strip()[:160], "bbox": list(bbox)})
    return out


def stage_records(object_id, stage):
    return [r for fid in vk_files(object_id, stage) for r in extract(fid)]


def _fmt(recs):
    order = [m for m, _ in MATERIALS]
    return ", ".join(sorted({r["material"] for r in recs}, key=order.index))


def _ev(stage, recs, prefer):
    r = next((x for x in recs if x["material"] in prefer), recs[0])
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
        if not have:
            files = vk_files(object_id, stage)
            if not other:
                return []
            sys_ = sorted({r["system"] for r in other})[0]
            return [{"parameter_code": CODE, "location": sys_,
                     "violation_label": "COMPARISON_IMPOSSIBLE" if files else "MISSING_DOCUMENT",
                     "pd_value": _fmt(other) if stage == "RD" else None, "rd_value": _fmt(other) if stage == "PD" else None,
                     "evidence": [_ev("PD" if stage == "RD" else "RD", other, PREMIUM)],
                     "note": (f"в {'РД' if stage == 'RD' else 'ПД'} комплект ВК есть, но материал канализационных труб "
                              f"в тексте не найден (сканы или только чертежи)") if files else
                             f"в {'РД' if stage == 'RD' else 'ПД'} нет комплекта ВК / ИОС3"}]
    by = {s: defaultdict(list) for s in ("PD", "RD")}
    for s, recs in (("PD", pd), ("RD", rd)):
        for r in recs:
            by[s][r["system"]].append(r)
    checks = []
    rd_premium = {x["material"] for x in rd} & PREMIUM
    for system in sorted(set(by["PD"]) & set(by["RD"])):
        p, r = by["PD"][system], by["RD"][system]
        P, R = {x["material"] for x in p}, {x["material"] for x in r}
        pvc = next((x for x in r if x["material"] == "ПВХ"), None)
        if P & PREMIUM and pvc and not R & PREMIUM and not rd_premium:
            label = "VIOLATION_PRESENT"
            note = (f"{system}: в ПД {', '.join(sorted(P & PREMIUM))}, в РД чугунных и малошумных труб нет вовсе, "
                    f"есть ПВХ («{pvc['line'][:90]}») — замена на тонкостенный ПВХ (триггер матрицы); кандидат")
            ev = [_ev("PD", p, PREMIUM), _ev("RD", r, {"ПВХ"})]
        elif P & PREMIUM and pvc and not R & PREMIUM:
            # система определяется по словам строки; чугун в РД есть, но отнесён к другой системе или участку
            # (Полярная 16: чугунные выпуски в ПД — у водостока, в РД — без марки системы)
            label = "COMPARISON_IMPOSSIBLE"
            note = (f"{system}: в ПД {', '.join(sorted(P & PREMIUM))}, в РД у этой системы — {_fmt(r)} («{pvc['line'][:90]}»); "
                    f"{', '.join(sorted(rd_premium))} в РД есть у других участков — сверить по спецификации ВК")
            ev = [_ev("PD", p, PREMIUM), _ev("RD", r, {"ПВХ"})]
        elif (P & PREMIUM) <= R and not ("ПВХ" in R and "ПВХ" not in P):
            label = "NO_VIOLATION"
            note = (f"{system}: " + (f"{', '.join(sorted(P & PREMIUM))} из ПД в РД сохранены" if P & PREMIUM
                                     else "чугунных и малошумных труб в ПД нет") + ", ПВХ не добавлен")
            ev = [_ev("PD", p, PREMIUM), _ev("RD", r, PREMIUM)]
        else:
            label = "COMPARISON_IMPOSSIBLE"
            note = f"{system}: материалы различаются (ПД: {_fmt(p)}; РД: {_fmt(r)}) — сверить по спецификации ВК"
            ev = [_ev("PD", p, PREMIUM), _ev("RD", r, {"ПВХ"} | PREMIUM)]
        checks.append({"parameter_code": CODE, "location": system, "violation_label": label,
                       "pd_value": _fmt(p), "rd_value": _fmt(r), "evidence": ev, "note": note})
    return checks


def main(argv):
    obj = argv[0] if argv else "OBJ-PILOT-POL17"
    for stage in ("PD", "RD"):
        recs = stage_records(obj, stage)
        by = defaultdict(list)
        for r in recs:
            by[r["system"]].append(r)
        print(f"{stage}: файлов {len(vk_files(obj, stage))}, записей {len(recs)}")
        for s, rs in sorted(by.items()):
            print(f"   {s}: {_fmt(rs)}")
            for x in {x['line']: x for x in rs}.values():
                print(f"        {x['file_id']}:{x['page']} [{x['material']}] {x['line'][:100]}")
    for c in compare(obj):
        ev = ", ".join(f"{e['stage']} {e['file_id']}:{e['pdf_page_number']}" for e in c["evidence"])
        print(f"{c['violation_label']:22} {c['location']:4} ПД {c['pd_value']} | РД {c['rd_value']} [{ev}]")
        print(f"      {c['note']}")


if __name__ == "__main__":
    main(sys.argv[1:])
