"""Сборка и проверка файла ответа.

    python -m ml.submission skeleton OBJ-NOVOSLOBODSKAYA     # скелет на 132 параметра → out/
    python -m ml.submission validate out/submission_OBJ-NOVOSLOBODSKAYA.json

Скелет — это ответ «ничего не нашли, но и не молчим»: каждый параметр матрицы получает строку.
Дальше этапы пайплайна заменяют строки настоящими результатами через merge_checks().
"""
import re
import sys
from collections import defaultdict

import jsonschema

from ml import paths
from ml.location import OBJECT, OBJECT_LOCATION, location_type, normalize_location

# Комплектность по дисциплинам. «Источник в РД» матрицы называет марку комплекта («Спецификация (ОВ)»,
# «Раздел АР: …», «Поэтажные планы (СС/АПС)»). Если ни одного комплекта этих марок в РД объекта нет,
# параметр получает MISSING_DOCUMENT (ТЗ 9.2: MISSING_EVIDENCE — нет обязательного документа), а не
# «сравнить нельзя». У Речникова в РД нет ОВ, СС, ТХ, ГСВ и ППР.
RD_MARK_GROUP = {"ГП": "ГП", "ПП": "ГП", "АР": "АР", "КЖ": "КР", "КМ": "КР", "КР": "КР", "КК": "КР",
                 "ОВ": "ОВ", "ИТП": "ОВ", "НТС": "ОВ", "ВК": "ВК", "НВК": "ВК",
                 "ЭОМ": "ЭОМ", "ЭМ": "ЭОМ", "ЭО": "ЭОМ", "ЭГ": "ЭОМ", "ЭН": "ЭОМ",
                 "СС": "СС", "АПС": "СС", "СОУЭ": "СС", "ТХ": "ТХ", "ГСВ": "ГСВ", "ППР": "ППР"}
RD_MARK_RE = re.compile(r"\b(" + "|".join(sorted(RD_MARK_GROUP, key=len, reverse=True)) + r")\b")
# Группа есть в РД объекта: раздел реестра или марка в имени файла (как в registry.DISCIPLINES)
GROUP_SECTION = {"GP": "ГП", "AR": "АР", "KR": "КР", "OV": "ОВ", "VK": "ВК", "EOM": "ЭОМ", "SS": "СС"}
GROUP_NAME_RE = {
    "ГП": r"ГП|ПП", "АР": r"АР\d*|АИ", "КР": r"КЖ[\d.]*|КМ|КР|КК|СВГ|РС|ОК", "ОВ": r"ОВ\d*|ИТП|ТС",
    "ВК": r"ВК\d*|НВК|НС", "ЭОМ": r"ЭОМ|ЭМ|ЭО|ЭГ|ЭН|ЭС", "СС": r"СС|АПС|СОУЭ|СКС", "ТХ": r"ТХ",
    "ГСВ": r"ГСВ|ГСН", "ППР": r"ППР"}
RD_STAGES = ("RD", "RD_ID_MIXED")


def rd_groups(object_id):
    """Группы дисциплин, комплекты которых есть в РД объекта."""
    present = set()
    for r in paths.read_jsonl(paths.MANIFEST):
        if r["object_id"] != object_id or r["stage"] not in RD_STAGES:
            continue
        if r["section"] in GROUP_SECTION:
            present.add(GROUP_SECTION[r["section"]])
        name = " " + r["relative_path"].split("/")[-1]
        for group, rx in GROUP_NAME_RE.items():
            if re.search(rf"[-_ (.]({rx})[\s._\d)-]", name):
                present.add(group)
    return present


def missing_rd_groups(parameter, present):
    """Каких комплектов РД для параметра не хватает: [] — хватает или марка в матрице не названа."""
    need = {RD_MARK_GROUP[m] for m in RD_MARK_RE.findall(parameter["source_rd"])}
    return sorted(need) if need and not need & present else []

# Статус ТЗ (раздел 9.2) → violation_label ответа. Таблица продублирована в CLAUDE.md.
LABEL_BY_STATUS = {
    "CANDIDATE": "VIOLATION_PRESENT",
    "NEGATIVE_VERIFIED": "NO_VIOLATION",
    "MISSING_EVIDENCE": "MISSING_DOCUMENT",
    "NOT_COMPARABLE": "COMPARISON_IMPOSSIBLE",
    "CLARIFICATION_REQUIRED": "COMPARISON_IMPOSSIBLE",
}

PROTOCOL_STATUS_BY_LABEL = {
    "VIOLATION_PRESENT": None,          # CRITICAL / WARNING по критичности, см. protocol_status()
    "NO_VIOLATION": "OK",
    "COMPARISON_IMPOSSIBLE": "COMPARISON_IMPOSSIBLE",
}


def protocol_status(label, criticality, missing_stages=()):
    if label == "VIOLATION_PRESENT":
        return "CRITICAL" if criticality and criticality.startswith("Критич") else "WARNING"
    if label == "MISSING_DOCUMENT":
        for stage in ("PD", "RD", "ID"):
            if stage in missing_stages:
                return f"{stage}_MISSING"
        return "COMPARISON_IMPOSSIBLE"
    return PROTOCOL_STATUS_BY_LABEL[label]


# Коды вне матрицы 132 (свободный поиск в эталоне организаторов): критичность — как у них в public_gold_checks
# («Существенное (предписание) — требует утверждения»), без приписки, в формулировке матрицы
OUT_OF_MATRIX_CRITICALITY = {"FREE-HEATING-001": "Существенное (предписание)"}


def missing_stages_from_note(note):
    """Каких стадий не хватает, по пояснению трека: «в РД нет комплекта АР» → ["RD"]. Треки ставят MISSING_DOCUMENT
    сами и стадию отдельным полем не передают, а протоколу (Приложение 2) нужен RD_MISSING / PD_MISSING / ID_MISSING."""
    names = {"ПД": "PD", "РД": "RD", "ИД": "ID"}
    return [names[m] for m in re.findall(r"в (ПД|РД|ИД) нет", note or "")]


def stages_present(object_id):
    """Какие стадии вообще есть у объекта по реестру. RD_ID_MIXED считаем и РД, и ИД."""
    present = set()
    for row in paths.read_jsonl(paths.MANIFEST):
        if row["object_id"] != object_id:
            continue
        stage = row["stage"]
        if stage == "RD_ID_MIXED":
            present |= {"RD", "ID"}
        elif stage in ("PD", "RD", "ID"):
            present.add(stage)
    return present


def skeleton(object_id):
    catalog = paths.read_jsonl(paths.CATALOG)
    present = stages_present(object_id)
    missing = [s for s in ("PD", "RD") if s not in present]
    groups = rd_groups(object_id)
    checks = []
    for p in catalog:
        code = p["parameter_code"]
        # Без ПД или РД сравнивать не с чем → MISSING_DOCUMENT; нет нужного комплекта РД → тоже;
        # иначе пока «сравнить не смогли» (для параметра нет извлечения).
        lacking = missing_rd_groups(p, groups) if not missing else []
        label = "MISSING_DOCUMENT" if missing or lacking else "COMPARISON_IMPOSSIBLE"
        row = {
            "parameter_code": code,
            "location": OBJECT_LOCATION if location_type(code) == OBJECT else "",
            "violation_label": label,
            "protocol_status": protocol_status(label, p["criticality"], missing or (["RD"] if lacking else [])),
            "criticality": p["criticality"],
            "pd_value": None,
            "rd_value": None,
            "id_value": None,
            "evidence": [],
        }
        if lacking:
            row["note"] = "в РД нет комплекта " + ", ".join(lacking)
        checks.append(row)
    return {"object_id": object_id, "checks": checks}


def merge_checks(submission, new_checks):
    """Подставить найденные проверки вместо заглушек.

    Заглушка параметра (location = "" или «Объект» без доказательств) удаляется, если по этому
    параметру пришла хотя бы одна настоящая строка. Одинаковые ключи перезаписываются.
    """
    catalog = {p["parameter_code"]: p for p in paths.read_jsonl(paths.CATALOG)}
    by_code = defaultdict(list)
    for c in new_checks:
        c = dict(c)
        c["location"] = normalize_location(c["parameter_code"], c.get("location"))
        crit = catalog.get(c["parameter_code"], {}).get("criticality") or OUT_OF_MATRIX_CRITICALITY.get(c["parameter_code"])
        c.setdefault("criticality", crit)
        c.setdefault("protocol_status", protocol_status(c["violation_label"], crit, missing_stages_from_note(c.get("note"))))
        by_code[c["parameter_code"]].append(c)

    merged = []
    for c in submission["checks"]:
        code = c["parameter_code"]
        is_stub = not c["evidence"]
        if code in by_code and is_stub:
            continue
        if any(n["location"] == c["location"] for n in by_code.get(code, [])):
            continue
        merged.append(c)
    for rows in by_code.values():
        merged.extend(rows)
    return {"object_id": submission["object_id"], "checks": merged}


def validate(submission):
    """Ошибки схемы + наши собственные проверки. Пустой список = всё в порядке."""
    errors = [f"{'/'.join(map(str, e.path))}: {e.message}"
              for e in jsonschema.Draft202012Validator(paths.read_json(paths.SCHEMA)).iter_errors(submission)]
    codes = {p["parameter_code"] for p in paths.read_jsonl(paths.CATALOG)}
    seen = set()
    for i, c in enumerate(submission.get("checks", [])):
        key = (c.get("parameter_code"), c.get("location"))
        if key in seen:
            errors.append(f"checks/{i}: дубль ключа {key}")
        seen.add(key)
        if c.get("violation_label") == "VIOLATION_PRESENT" and not c.get("evidence"):
            errors.append(f"checks/{i}: VIOLATION_PRESENT без доказательств")
    missing = codes - {c.get("parameter_code") for c in submission.get("checks", [])}
    if missing:
        errors.append(f"нет строк для {len(missing)} параметров матрицы: {sorted(missing)[:5]}…")
    return errors


def main(argv):
    if len(argv) >= 2 and argv[0] == "skeleton":
        object_id = argv[1]
        sub = skeleton(object_id)
        out = paths.OUT / f"submission_{object_id}.json"
        paths.write_json(out, sub)
        print(f"{out}: {len(sub['checks'])} строк, ошибок проверки: {len(validate(sub))}")
    elif len(argv) >= 2 and argv[0] == "validate":
        errors = validate(paths.read_json(argv[1]))
        print("OK" if not errors else "\n".join(errors))
        sys.exit(1 if errors else 0)
    else:
        print(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
