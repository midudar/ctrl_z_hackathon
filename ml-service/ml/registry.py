"""Этап 0 (упрощённый): какие файлы объекта относятся к дисциплине.

Раздел из `document_manifest.jsonl` (`section`) плюс шифр в имени файла: в реестре, например,
«…_СВГ (1).pdf» (стена в грунте) помечен как OTHER, а по смыслу это конструкции.

    python -m ml.registry OBJ-NOVOSLOBODSKAYA KR
"""
import re
import sys

from ml import paths, revisions

# дисциплина → (разделы реестра, регулярка по имени файла)
DISCIPLINES = {
    # конструкции: КР (ПД), КЖ/КМ (РД), СВГ — стена в грунте, РС — распорная система
    "KR": ({"KR"}, re.compile(r"[-_ (](КР|КЖ|КМ|КК|СВГ|РС)[\s._\d)-]", re.I)),
    # отопление и вентиляция: ИОС4 / ОВ
    "OV": ({"OV"}, re.compile(r"[-_ (](ОВ|ИОС\s?4)[\s._\d)-]", re.I)),
    "AR": ({"AR"}, re.compile(r"[-_ (](АР)[\s._\d)-]", re.I)),
    # пояснительная записка: там абсолютная отметка 0.000 и ТЭП
    "PZ": (set(), re.compile(r"[-_ (](ПЗ|ОПЗ)[\s._\d)-]", re.I)),
    # электроснабжение и электроосвещение: ИОС1 (ПД), ЭОМ / ЭМ / ЭО / ЭН / ЭС (РД)
    "EOM": ({"EOM"}, re.compile(r"[-_ (](ЭОМ|ЭМ|ЭО|ЭН|ЭС|ИОС\s?1)[\s._\d)-]", re.I)),
}

STAGES = {"PD": {"PD"}, "RD": {"RD", "RD_ID_MIXED"}, "ID": {"ID", "RD_ID_MIXED"}}


def files(object_id, stage, discipline, latest=True):
    """file_id файлов объекта по стадии и дисциплине, в порядке реестра.

    По умолчанию оставляются только актуальные редакции (`ml.revisions`): в ПД
    Речникова иначе в одно сравнение попадают и том 2022 года, и его корректировка
    2025-го. Отбор редакций считается по всем файлам стадии, а не внутри дисциплины,
    иначе актуальную редакцию может отсечь фильтр по имени.
    """
    sections, name_re = DISCIPLINES[discipline]
    rows = [r for r in paths.read_jsonl(paths.MANIFEST)
            if r["object_id"] == object_id and r["extension"] == ".pdf"
            and r["stage"] in STAGES[stage]]
    if latest:
        keep, _ = revisions.latest(rows)
        keep_ids = {r["file_id"] for r in keep}
        rows = [r for r in rows if r["file_id"] in keep_ids]
    out = []
    for r in rows:
        name = r["relative_path"].split("/")[-1]
        if r["section"] in sections or name_re.search(" " + name):
            out.append(r["file_id"])
    return out


def revision_conflicts(object_id, stage):
    """Шифры, у которых две разные действующие редакции: по ТЗ это CLARIFICATION_REQUIRED.

    На трёх объектах хакатона таких нет, поэтому сравнения этим пока не пользуются.
    """
    rows = [r for r in paths.read_jsonl(paths.MANIFEST)
            if r["object_id"] == object_id and r["extension"] == ".pdf"
            and r["stage"] in STAGES[stage]]
    return revisions.latest(rows)[1]


def main(argv):
    object_id = argv[0] if argv else "OBJ-NOVOSLOBODSKAYA"
    discipline = argv[1] if len(argv) > 1 else "KR"
    man = {r["file_id"]: r for r in paths.read_jsonl(paths.MANIFEST)}
    for stage in ("PD", "RD", "ID"):
        ids = files(object_id, stage, discipline)
        print(f"{stage}: {len(ids)} файлов (актуальные редакции)")
        for code, conflicting in revision_conflicts(object_id, stage):
            print(f"   КОНФЛИКТ РЕДАКЦИЙ {code}: {', '.join(conflicting)}")
        for f in ids:
            print(f"   {f}  стр.{man[f]['pdf_pages']:>4}  {man[f]['relative_path'].split('/')[-1][:70]}")


if __name__ == "__main__":
    main(sys.argv[1:])
