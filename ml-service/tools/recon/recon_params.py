"""Разведка: у каких непокрытых параметров есть упоминания со значениями и в ПД, и в РД объекта (текстовый слой).

Ключи — основы значимых слов названия параметра (≥ 2 совпадения в строке, если слов ≥ 2). Правила сравнения тут
не подбираются: это только «есть ли вообще что сравнивать».
"""
import json
import re
import sys
from collections import defaultdict

sys.path.insert(0, r"D:\hakaton\ltc")
import pymupdf
from ml import paths, revisions
from ml.pages import file_path

OBJ = sys.argv[1] if len(sys.argv) > 1 else "OBJ-RECHNIKOV-7-7"
STOP = {"параметры", "параметр", "общая", "общий", "количество", "наличие", "характеристики", "спецификация",
        "класс", "марка", "тип", "типы", "схемы", "схема", "система", "системы", "материал", "материалы", "здания",
        "объекта", "конструкций", "конструкции", "элементов", "размещение", "расположения", "состав", "площадь",
        "ширина", "высота", "диаметр", "диаметры", "мероприятия", "требования", "защиты", "расчетная", "расчетный"}
KEEP_ALWAYS = {"площадь", "ширина", "высота", "диаметр", "класс", "марка"}   # важны, но только в паре с другим словом


def stems(name):
    words = [w.lower() for w in re.findall(r"[А-Яа-яЁё]{4,}", name)]
    core = [w[:6] for w in words if w not in STOP]
    extra = [w[:6] for w in words if w in KEEP_ALWAYS]
    return sorted(set(core)), sorted(set(extra))


def main():
    sub = paths.read_json(paths.OUT / f"submission_{OBJ}.json")
    covered = {c["parameter_code"] for c in sub["checks"] if c["evidence"]}
    missing = {c["parameter_code"] for c in sub["checks"] if c["violation_label"] == "MISSING_DOCUMENT"}
    catalog = paths.read_jsonl(paths.CATALOG)
    todo = [p for p in catalog if p["parameter_code"] not in covered | missing]
    print(f"параметров всего {len(catalog)}, покрыто {len(covered)}, нет комплекта РД {len(missing)}, "
          f"проверяем {len(todo)}", flush=True)
    keys = {p["parameter_code"]: stems(p["parameter_name"]) for p in todo}

    hits = {p["parameter_code"]: {"PD": [], "RD": []} for p in todo}
    man = [r for r in paths.read_jsonl(paths.MANIFEST) if r["object_id"] == OBJ and r["extension"] == ".pdf"]
    for stage, sts in (("PD", {"PD"}), ("RD", {"RD", "RD_ID_MIXED"})):
        rows, _ = revisions.latest([r for r in man if r["stage"] in sts])
        for r in rows:
            try:
                doc = pymupdf.open(file_path(r["file_id"]))
            except Exception:
                continue
            for pno, page in enumerate(doc, 1):
                text = page.get_text()
                if not text.strip():
                    continue
                low = text.lower()
                for code, (core, extra) in keys.items():
                    if not core or not any(k in low for k in core):
                        continue
                    for line in text.splitlines():
                        l = line.lower()
                        n = sum(k in l for k in core) + sum(k in l for k in extra)
                        need = 2 if len(core) + len(extra) >= 2 else 1
                        if n >= need and re.search(r"\d", line):
                            hits[code][stage].append((r["file_id"], pno, " ".join(line.split())[:140]))
        print(f"  {stage}: файлов {len(rows)}", flush=True)

    out = []
    for p in todo:
        h = hits[p["parameter_code"]]
        out.append({"code": p["parameter_code"], "name": p["parameter_name"], "keys": keys[p["parameter_code"]],
                    "pd": len(h["PD"]), "rd": len(h["RD"]),
                    "pd_files": sorted({f for f, _, _ in h["PD"]}), "rd_files": sorted({f for f, _, _ in h["RD"]}),
                    "pd_ex": h["PD"][:4], "rd_ex": h["RD"][:4]})
    out.sort(key=lambda x: (-(min(x["pd"], 1) + min(x["rd"], 1)), -x["rd"]))
    json.dump(out, open(r"C:\Users\Alex\AppData\Local\Temp\claude\d--hakaton-ltc\8ea918cf-11bd-465b-8f86-aaafbe3cc8ca"
                        rf"\scratchpad\recon_{OBJ}.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    both = [x for x in out if x["pd"] and x["rd"]]
    print(f"\nесть и в ПД, и в РД: {len(both)}; только ПД: {sum(1 for x in out if x['pd'] and not x['rd'])}; "
          f"только РД: {sum(1 for x in out if x['rd'] and not x['pd'])}; нигде: {sum(1 for x in out if not x['pd'] and not x['rd'])}")
    for x in both:
        print(f"  {x['code']:9} ПД {x['pd']:4} РД {x['rd']:4}  {x['name'][:70]}")


if __name__ == "__main__":
    main()
