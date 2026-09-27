"""Целостность комплекта документов объекта: что не так с самими файлами, а не с проектными решениями.

Типы дефектов — из пилотной разметки организаторов (02_МЕТОДИКА/multi_object_annotation_20260817/
00_СВОДНЫЕ_ДАННЫЕ/ДЕФЕКТЫ_И_КАНДИДАТЫ.jsonl): там 37 из 48 записей — дефекты комплекта. Похоже, за это
и дают 10 баллов «document_integrity_and_split_handling» (открытый вопрос 5). Нарушениями это не считается:
результат — отдельный список, он же идёт в ответ ключом `document_integrity` и в протокол
(раздел «Статус загрузки документов»).

    python -m ml.integrity OBJ-RECHNIKOV-7-7
"""
import json
import re
import shutil
import subprocess
import sys
import zipfile
from collections import Counter, defaultdict

import pymupdf

from ml import paths, registry, revisions, submission
from ml.pages import file_path

TEMP_RE = re.compile(r"(^~\$|\.(bak|dwl2?|tmp|log|db)$|^thumbs\.db$|^desktop\.ini$)", re.I)
MIN_TEXT = 80                       # символов на первой странице: меньше — текстового слоя нет
PD_SECTION_GROUP = {"GP": "ГП", "AR": "АР", "KR": "КР", "OV": "ОВ", "VK": "ВК", "EOM": "ЭОМ", "SS": "СС"}
# Марка с номером в шифре: «КЖ4.1», «АР2», «ИОС5.4.2», «ОВ1»
MARK_NUM_RE = re.compile(r"(?<![А-ЯЁA-Z])(КЖ|КМ|КР|АР|ОВ|ВК|ЭОМ|ИОС|ПБ|ПОС|ООС|ГП|СС|ТХ)\s?(\d+(?:\.\d+)*)")
# Марки проекта в поле 2 акта: «17_ПД/25-СВГ», «НВС-2025/03-КР1»
ACT_MARK_RE = re.compile(r"(?<![А-ЯЁA-Z])(СВГ|РС|КЖ[\d.]*\d|КМ\d*|КР\d+|АР\d*|ОВ\d*|ВК\d*|ЭОМ|ЭМ|ЭО|ОК)(?![А-ЯЁA-Z])")


def _finding(kind, severity, title, file_ids=(), **extra):
    return {"type": kind, "severity": severity, "title": title, "count": len(file_ids),
            "file_ids": sorted(file_ids), **extra}


def archive_members(path):
    """Имена файлов внутри архива или None, если архив не читается. 7z и rar — через bsdtar, если он есть."""
    suffix = path.suffix.lower()
    if suffix in (".zip", ".docx"):
        def name(i):
            if i.flag_bits & 0x800:
                return i.filename
            try:
                return i.filename.encode("cp437").decode("cp866")
            except UnicodeError:                 # имя не в cp866 — для поиска по расширению годится и так
                return i.filename
        try:
            with zipfile.ZipFile(path) as z:
                return [name(i) for i in z.infolist()]
        except (zipfile.BadZipFile, OSError):
            return None
    tar = shutil.which("bsdtar") or (r"C:\Windows\System32\tar.exe" if sys.platform == "win32" else None)
    if suffix in (".7z", ".rar") and tar:
        r = subprocess.run([tar, "-tf", str(path)], capture_output=True)
        return None if r.returncode not in (0, 1) else r.stdout.decode("utf-8", "replace").splitlines()
    return []                                   # не умеем читать — не считаем битым


def check(object_id):
    rows = [r for r in paths.read_jsonl(paths.MANIFEST) if r["object_id"] == object_id]
    everything = paths.read_jsonl(paths.MANIFEST)
    findings = []

    # 1. Пустые и нечитаемые файлы
    broken, no_text, first_text = [], defaultdict(list), {}
    temp_inside = []
    for r in rows:
        path = file_path(r["file_id"])
        if not path.exists() or path.stat().st_size == 0:
            broken.append(r["file_id"])
            continue
        if r["extension"] == ".pdf":
            try:
                with pymupdf.open(path) as doc:
                    if doc.page_count == 0:
                        broken.append(r["file_id"])
                        continue
                    first_text[r["file_id"]] = doc[0].get_text()
            except Exception:
                broken.append(r["file_id"])
                continue
            if len(first_text[r["file_id"]].strip()) < MIN_TEXT:
                no_text[r["stage"]].append(r["file_id"])
        elif r["extension"] in (".zip", ".docx", ".7z", ".rar"):
            members = archive_members(path)
            if members is None:
                broken.append(r["file_id"])
            else:
                temp_inside += [f"{r['file_id']}:{m.split('/')[-1]}" for m in members if TEMP_RE.search(m.split("/")[-1])]
    if broken:
        findings.append(_finding("UNREADABLE_OR_EMPTY_SOURCE_FILE", "CRITICAL",
                                 "Файл пуст или не читается — для сравнения не используется", broken))

    # 2. Временные файлы редакторов — в комплекте и внутри архивов
    temp = [r["file_id"] for r in rows if TEMP_RE.search(r["relative_path"].split("/")[-1])]
    if temp or temp_inside:
        findings.append(_finding("TEMPORARY_EDITOR_ARTIFACTS", "MEDIUM",
                                 "Временные и резервные файлы редакторов (~$, BAK, DWL, TMP, LOG)", temp,
                                 inside_archives=temp_inside[:50], inside_archives_count=len(temp_inside)))

    # 3. PDF без текстового слоя — нужен OCR (характеристика источника, не нарушение)
    for stage, ids in sorted(no_text.items()):
        total = sum(1 for r in rows if r["stage"] == stage and r["extension"] == ".pdf")
        findings.append(_finding("PDF_WITHOUT_MACHINE_READABLE_TEXT", "MEDIUM",
                                 f"{stage}: у {len(ids)} из {total} PDF на первой странице нет текстового слоя — "
                                 f"нужен OCR", ids, stage=stage))

    # 4. Точные дубли по SHA-256
    by_sha = defaultdict(list)
    for r in everything:
        by_sha[r["sha256"]].append(r)
    within, across_stages, across_objects = [], [], []
    for group in by_sha.values():
        mine = [r for r in group if r["object_id"] == object_id]
        if not mine or len(group) < 2:
            continue
        if len({r["object_id"] for r in group}) > 1:
            across_objects += [r["file_id"] for r in mine]
        stages = Counter(r["stage"] for r in mine)
        if len(stages) > 1:
            across_stages += [r["file_id"] for r in mine]
        elif len(mine) > 1:
            within += [r["file_id"] for r in mine]
    for kind, sev, title, ids in (
            ("EXACT_DUPLICATES_WITHIN_STAGE", "MEDIUM", "Внутри одной стадии есть точные дубли", within),
            ("EXACT_DUPLICATES_ACROSS_STAGES", "HIGH", "Одинаковые файлы лежат в разных стадиях", across_stages),
            ("EXACT_DUPLICATES_ACROSS_OBJECTS", "HIGH", "Точные дубли в разных объектах", across_objects)):
        if ids:
            findings.append(_finding(kind, sev, title, ids))

    # 5. Нет стадии или нет в РД дисциплины, которая есть в ПД
    present = submission.stages_present(object_id)
    lost = [s for s in ("PD", "RD", "ID") if s not in present]
    if lost:
        findings.append(_finding("MISSING_COMPARISON_STAGE", "HIGH",
                                 f"Нет стадий: {', '.join(lost)} — полное сравнение ПД–РД–ИД невозможно", stages=lost))
    pd_groups = {PD_SECTION_GROUP[r["section"]] for r in rows if r["stage"] == "PD" and r["section"] in PD_SECTION_GROUP}
    rd_groups = submission.rd_groups(object_id)
    if "RD" in present and pd_groups - rd_groups:
        findings.append(_finding("DISCIPLINE_MISSING_IN_RD", "HIGH",
                                 f"В ПД есть разделы {', '.join(sorted(pd_groups - rd_groups))}, "
                                 f"а комплектов РД этих марок в пакете нет", disciplines=sorted(pd_groups - rd_groups)))

    # 6. Редакции: устаревшие исключены из сравнения; конфликт — две действующие редакции одного шифра
    for stage in ("PD", "RD"):
        pdfs = [r for r in rows if r["extension"] == ".pdf" and r["stage"] in registry.STAGES[stage]]
        keep, conflicts = revisions.latest(pdfs)
        kept = {r["file_id"] for r in keep}
        old = [r["file_id"] for r in pdfs if r["file_id"] not in kept]
        if old:
            findings.append(_finding("SUPERSEDED_REVISIONS_EXCLUDED", "INFO",
                                     f"{stage}: устаревшие редакции ({len(old)}) исключены из сравнения", old, stage=stage))
        for code, ids in conflicts:
            findings.append(_finding("REVISION_CONFLICT", "HIGH", f"{stage}: две действующие редакции шифра {code} — "
                                     "CLARIFICATION_REQUIRED, сравнение по ним не делается", ids, stage=stage))

    # 7. Шифр в имени файла ≠ шифр на титуле (в пилоте: «файлы АР1 и АР2 перепутаны», «ИОС5.6 вместо ИОС5.5.6»)
    mismatch = []
    for r in rows:
        if r["stage"] == "ID" or r["file_id"] not in first_text:
            continue
        name = r["relative_path"].split("/")[-1]
        title = re.sub(r"\s", "", first_text[r["file_id"]].upper())
        for letters, num in MARK_NUM_RE.findall(name.upper()):
            if len(title) < MIN_TEXT or f"{letters}{num}" in title:
                continue
            others = {f"{l}{n}" for l, n in MARK_NUM_RE.findall(title) if l == letters}
            if others:
                mismatch.append((r["file_id"], f"{letters}{num}", sorted(others)[:3]))
            break
    if mismatch:
        findings.append(_finding("FILENAME_TITLE_CODE_MISMATCH", "HIGH",
                                 "Марка в имени файла не совпадает с маркой на титуле", [m[0] for m in mismatch],
                                 details=[{"file_id": f, "in_name": a, "on_title": b} for f, a, b in mismatch]))

    # 8. Акт ссылается на проект, которого нет в пакете (поле 2 бланка; только текстовый слой — OCR шумный)
    try:
        from ml import acts
        names = re.sub(r"\s", "", " ".join(r["relative_path"].split("/")[-1] for r in rows
                                            if r["stage"] in ("PD", "RD", "RD_ID_MIXED")).upper())
        orphan = []
        for a in acts.object_acts(object_id)[0]:
            if not a.get("design") or a["pages_text"] == 0:
                continue
            marks = set(ACT_MARK_RE.findall(a["design"].upper()))
            if marks and not any(m in names for m in marks):
                orphan.append((a["file_id"], a["design"][:80]))
        if orphan:
            findings.append(_finding("ID_REFERENCED_DESIGN_NOT_IN_PACKAGE", "HIGH",
                                     "Акт ссылается на проект, которого нет в пакете", sorted({o[0] for o in orphan}),
                                     examples=[{"file_id": f, "design": d} for f, d in orphan[:10]]))
    except Exception as e:                      # трек ИД не должен ломать проверку целостности
        print(f"  целостность: акты не разобраны ({e})", file=sys.stderr)

    summary = {"files": len(rows), "pdf": sum(r["extension"] == ".pdf" for r in rows),
               "stages": sorted(present), "findings": len(findings)}
    return {"object_id": object_id, "summary": summary, "findings": findings}


def compact(report):
    """Для файла ответа: без длинных списков."""
    return {"summary": report["summary"],
            "findings": [{k: (v[:20] if isinstance(v, list) else v) for k, v in f.items()} for f in report["findings"]]}


PILOT_DEFECTS = (paths.ROOT / "02_МЕТОДИКА" / "multi_object_annotation_20260817" / "00_СВОДНЫЕ_ДАННЫЕ"
                 / "ДЕФЕКТЫ_И_КАНДИДАТЫ.jsonl")
# у нас и у организаторов тип назван по-разному
OUR_TO_PILOT = {"ID_REFERENCED_DESIGN_NOT_IN_PACKAGE": "ID_REFERENCED_RD_NOT_IN_PACKAGE",
                "REVISION_CONFLICT": "SAME_LOGICAL_NAME_DIFFERENT_CONTENT"}


def pilot_eval(objects):
    """Сверка с подтверждёнными дефектами комплекта из пилотной разметки организаторов (объекты `pilot_objects.py`,
    запускать с INSPECTOR_DOCS / INSPECTOR_DATA на pilot/). По каждому их дефекту: нашли ли мы такой тип и те же
    файлы. Для СОШ и ДОО ИД не распакована (3 502 и 789 файлов) — дефекты ИД там не проверяются."""
    marks = [json.loads(l) for l in open(PILOT_DEFECTS, encoding="utf-8")]
    man = {r["file_id"] for r in paths.read_jsonl(paths.MANIFEST)}
    tot = hit_t = hit_f = 0
    for obj in objects:
        code = obj.replace("OBJ-PILOT-", "")
        theirs = [m for m in marks if m["object_code"] == code]
        rep = check(obj)
        ours = defaultdict(set)
        for f in rep["findings"]:
            ours[OUR_TO_PILOT.get(f["type"], f["type"])].update(f.get("file_ids") or [])
        print(f"\n== {obj}: у организаторов {len(theirs)}, у нас {len(rep['findings'])} "
              f"({', '.join(sorted(ours))})")
        for m in theirs:
            files = [f for f in m["file_ids"] if f in man]
            if m["file_ids"] and not files:
                print(f"   —  {m['finding_type']:38} файлов нет в распакованном (ИД не распакована)")
                continue
            tot += 1
            t_ok = m["finding_type"] in ours
            f_ok = t_ok and (not files or bool(set(files) & ours[m["finding_type"]]))
            hit_t += t_ok
            hit_f += f_ok
            print(f"   {'ДА ' if f_ok else ('тип' if t_ok else 'НЕТ')} {m['finding_type']:38} {m['status']:26} "
                  f"файлов {len(files)}")
    print(f"\nИтого: дефектов организаторов {tot}; тот же тип нашли {hit_t}, тот же тип и файл — {hit_f}")


def main(argv):
    if argv and argv[0] == "--pilot":
        objs = sorted({r["object_id"] for r in paths.read_jsonl(paths.MANIFEST)
                       if r["object_id"].startswith("OBJ-PILOT-")})
        return pilot_eval([o for o in objs if not argv[1:] or any(a in o for a in argv[1:])])
    object_id = argv[0] if argv else "OBJ-NOVOSLOBODSKAYA"
    report = check(object_id)
    out = paths.OUT / f"integrity_{object_id}.json"
    paths.write_json(out, report)
    print(f"{object_id}: файлов {report['summary']['files']}, дефектов {len(report['findings'])} → {out}")
    for f in report["findings"]:
        extra = f" (внутри архивов: {f['inside_archives_count']})" if f.get("inside_archives_count") else ""
        print(f"  [{f['severity']:8}] {f['type']}: {f['title']} — файлов {f['count']}{extra}")
        for d in (f.get("details") or f.get("examples") or [])[:3]:
            print(f"        {d}")


if __name__ == "__main__":
    main(sys.argv[1:])
