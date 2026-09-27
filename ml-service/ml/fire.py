"""Пожарные характеристики здания: степень огнестойкости (PZ-022) и класс конструктивной пожарной опасности (PZ-023).

Значения пишут текстом и почти одинаково на обеих стадиях:
- ПД (ПЗ, АР, КР, МОПБ): «Комплекс запроектирован II степени огнестойкости С0 класса конструктивной пожарной
  опасности», «Степень огнестойкости здания – I;», «-класс конструктивной пожарной опасности - С 0»;
- РД (общие данные КЖ, АР): «Жилой дом запроектирован в строительных конструкциях, соответствующих l степени
  огнестойкости и СО конструктивной пожарной опасности» — «l» (латинская L) вместо I и «СО» (буква О) вместо С0.

Главная ловушка — те же слова про чужие здания и нормы: противопожарные расстояния «до зданий I, II, III степени
огнестойкости», соседние корпуса, «не ниже I степени», «для зданий 1-й степени», пределы огнестойкости
конструкций. Поэтому берутся только объявления о самом объекте:
- именительный падеж с тире или «предусмотрен»: «Степень огнестойкости здания – I», «Класс … Объекта предусмотрен С0»;
- родительный («II степени огнестойкости», «класса … С0») — только если в том же предложении раньше стоит
  «запроектирован / предусмотрен / принят / соответствующих», и нет слов про соседей, нормы и расстояния.
Перечни («I, III степени», «С0, С1») не берутся.

Правило (триггер матрицы — «снижение»): I лучше V, С0 лучше С3. Нарушение — только если РД хуже всех значений
ПД; не хуже ни одного — «нет нарушения»; иначе (значения внутри стадии расходятся) — «сравнить нельзя» с пояснением.

    python -m ml.fire OBJ-NOVOSLOBODSKAYA
"""
import re
import sys

import pymupdf

from ml import paths, revisions, submission
from ml.pages import file_path

LOCATION = "Объект"

_ROMAN = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5}
_DEGREE = r"(I{1,3}|IV|V|[lІ]{1,3}|[1-5])"               # «l» и украинская «І» в текстовом слое вместо I
_CLASS = r"[СC]\s?([0-3OО])"                              # «СО» с буквой О вместо нуля
_BEFORE_LIST = re.compile(r"(?:I|V|[1-5]|[СC]\s?[0-3])\s*(?:[,–—-]|и)\s*$")   # «I, III степени», «С0, С1»

# (параметр, форма, регулярка): nom — именительный с тире («Степень огнестойкости здания – I»), gen — родительный
PATTERNS = [
    ("PZ-022", "nom", re.compile(r"степень\s+огнестойкост\w*(?:\s+(?:здания|объекта(?:\s+защиты)?|комплекса|сооружения))?"
                                 r"(?:\s*[–—:-]\s*|\s+(?:принят|предусмотрен|установлен)\w*\s*[–—:-]?\s*)" + _DEGREE
                                 + r"(?![\w,])", re.I)),
    # «II степени огнестойкости», «II-ой степени», «имеет II степень огнестойкости»
    ("PZ-022", "gen", re.compile(r"(?<![\w,.])" + _DEGREE + r"(?:-?(?:ой|й))?\s+степен\w*\s+огнестойкост", re.I)),
    ("PZ-023", "nom", re.compile(r"класс\s+конструктивн\w*\s+пожарн\w*\s+опасност\w*(?:\s+(?:здания|объекта(?:\s+защиты)?|комплекса))?"
                                 r"(?:\s+(?:предусмотрен|принят)\w*)?\s*[–—:-]?\s*" + _CLASS + r"(?![\w,]|\s*,\s*[СC]\s?\d)", re.I)),
    ("PZ-023", "gen", re.compile(r"(?<![\w,])" + _CLASS + r"\s*(?:[–—-]\s*)?(?:класса\s+)?конструктивн\w*\s+пожарн\w*\s+опасност", re.I)),
    ("PZ-023", "gen", re.compile(r"класса\s+конструктивн\w*\s+пожарн\w*\s+опасност\w*\s*[–—-]?\s*" + _CLASS
                                 + r"(?![\w,]|\s*,\s*[СC]\s?\d)", re.I)),
]
DECLARE_RE = re.compile(r"запроектирова|предусмотре|приня[тя]|соответствующ|относит\w*\s+к|является|име(?:ет|ют)\b", re.I)
# Чужие здания, нормы и расстояния
FOREIGN_RE = re.compile(r"не\s+ниже|не\s+менее|до\s+здани|соседн|существующ|расстояни|корпус|автостоянк|для\s+здани"
                        r"|предел\w*\s+огнестойк|требуем|зависимост|таблиц", re.I)
SENTENCE_START_RE = re.compile(r"(?:[.;!?]\s+|\s[–—-]\s+|^)(?=[^.;!?]*$)")

# Откуда брать доказательство: ПД — ПЗ, затем МОПБ, АР, КР; РД — общие данные АР, затем КР
SOURCE_RANK = {"PD": {"PZ": 0, "PB": 1, "AR": 2, "KR": 3}, "RD": {"AR": 0, "KR": 1}}
PZ_NAME_RE = re.compile(r"[-_ (](ПЗ|ОПЗ)[\s._\d)-]", re.I)
NAMES = {"PZ-022": "степень огнестойкости", "PZ-023": "класс конструктивной пожарной опасности"}


def _value(code, raw):
    raw = raw.upper().replace("L", "I").replace("І", "I")
    if code == "PZ-022":
        return int(raw) if raw.isdigit() else _ROMAN.get(raw)
    return 0 if raw in ("O", "О") else int(raw)


def _label(code, v):
    if code == "PZ-022":
        return {1: "I", 2: "II", 3: "III", 4: "IV", 5: "V"}[v]
    return f"С{v}"


def _text(page):
    t = " ".join(page.get_text().split())
    return re.sub(r"(?<=[а-я])- (?=[а-я])", "", t)          # переносы: «огнестой- кости»


def extract(text):
    """[(код, значение, форма, фрагмент)] — объявления о самом объекте на странице."""
    out = []
    for code, form, rx in PATTERNS:
        for m in rx.finditer(text):
            start = m.start()
            head = text[max(0, start - 250):start]
            sentence = head[SENTENCE_START_RE.search(head).end():] if SENTENCE_START_RE.search(head) else head
            if _BEFORE_LIST.search(text[max(0, start - 8):start]):
                continue
            if FOREIGN_RE.search(sentence + text[start:m.end()]):
                continue
            if form == "gen" and not DECLARE_RE.search(sentence):
                continue
            v = _value(code, m.group(1))
            if v is None:
                continue
            out.append((code, v, form, (sentence[-80:] + text[start:m.end() + 30]).strip()))
    return out


def _source(row):
    name = row["relative_path"].split("/")[-1]
    return "PZ" if PZ_NAME_RE.search(" " + name) else row["section"]


def stage_records(object_id, stage):
    rows = [r for r in paths.read_jsonl(paths.MANIFEST)
            if r["object_id"] == object_id and r["extension"] == ".pdf"
            and r["stage"] in ({"PD"} if stage == "PD" else {"RD", "RD_ID_MIXED"})]
    rows, _ = revisions.latest(rows)
    records = []
    for r in rows:
        try:
            doc = pymupdf.open(file_path(r["file_id"]))
        except Exception:                          # битый файл — это дело integrity.py, здесь просто пропускаем
            continue
        for pno, page in enumerate(doc, start=1):
            for code, v, form, snippet in extract(_text(page)):
                records.append({"code": code, "value": v, "form": form, "file_id": r["file_id"], "page": pno,
                                "source": _source(r), "snippet": snippet})
    return records


def _evidence(rows, stage):
    rank = SOURCE_RANK[stage]
    best = min(rows, key=lambda r: (rank.get(r["source"], 9), r["form"] != "nom", r["file_id"], r["page"]))
    return {"stage": stage, "file_id": best["file_id"], "pdf_page_number": best["page"]}


def compare(object_id, records=None):
    records = records or {s: stage_records(object_id, s) for s in ("PD", "RD")}
    catalog = {p["parameter_code"]: p for p in paths.read_jsonl(paths.CATALOG)}
    groups = submission.rd_groups(object_id)
    checks = []
    for code in ("PZ-022", "PZ-023"):
        pd = [r for r in records["PD"] if r["code"] == code]
        rd = [r for r in records["RD"] if r["code"] == code]
        fmt = lambda rows: "/".join(_label(code, v) for v in sorted({r["value"] for r in rows})) if rows else None
        row = {"parameter_code": code, "location": LOCATION, "pd_value": fmt(pd), "rd_value": fmt(rd),
               "id_value": None, "evidence": []}
        lacking = submission.missing_rd_groups(catalog[code], groups) if not rd else []
        if lacking:                                # в РД нет комплекта АР/КР — это «нет документа», как в скелете
            row["violation_label"] = "MISSING_DOCUMENT"
            row["note"] = "в РД нет комплекта " + ", ".join(lacking)
        elif not pd or not rd:
            row["violation_label"] = "COMPARISON_IMPOSSIBLE"
            row["note"] = f"{NAMES[code]}: в {'ПД' if not pd else 'РД'} значение не найдено"
        else:
            p, r = {x["value"] for x in pd}, {x["value"] for x in rd}
            if min(r) > max(p):                    # РД хуже всего, что заявлено в ПД
                row["violation_label"] = "VIOLATION_PRESENT"
            elif max(r) <= min(p):                 # РД не хуже ни одного значения ПД
                row["violation_label"] = "NO_VIOLATION"
            else:
                row["violation_label"] = "COMPARISON_IMPOSSIBLE"
                row["note"] = f"{NAMES[code]}: значения внутри стадии расходятся (ПД {fmt(pd)}, РД {fmt(rd)})"
        row["evidence"] = [_evidence(rows, stage) for stage, rows in (("PD", pd), ("RD", rd)) if rows]
        checks.append(row)
    return checks


def main(argv):
    object_id = argv[0] if argv else "OBJ-NOVOSLOBODSKAYA"
    records = {s: stage_records(object_id, s) for s in ("PD", "RD")}
    for stage in ("PD", "RD"):
        print(f"\n{stage}:")
        for r in records[stage]:
            print(f"  {r['code']} {_label(r['code'], r['value']):4} {r['form']} {r['file_id']}:{r['page']} [{r['source']}]"
                  f"  …{r['snippet'][-150:]}")
    print()
    for c in compare(object_id, records):
        print(f"{c['parameter_code']}: ПД {c['pd_value']} | РД {c['rd_value']} → {c['violation_label']}  "
              f"{[(e['stage'], e['file_id'], e['pdf_page_number']) for e in c['evidence']]}  {c.get('note', '')}")


if __name__ == "__main__":
    main(sys.argv[1:])
