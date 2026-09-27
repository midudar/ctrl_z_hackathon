# -*- coding: utf-8 -*-
"""Объекты пилотной разметки организаторов (архивы в `объекты/`) → папка `pilot/` в формате пакета участника.

    pilot/docs/<папка объекта>/...        распакованные ПД и РД (+ ИД, если её немного)
    pilot/data/document_manifest.jsonl    реестр в формате document_manifest.jsonl (file_id и SHA-256 — из реестров
                                          организаторов 02_МЕТОДИКА/multi_object_annotation_20260817/*/..._РЕЕСТР_*.csv)
    pilot/data/*                          копии каталога параметров, схемы ответа и split_policy

Пайплайн запускается на них без правок кода, как на стенде:
    INSPECTOR_DOCS=pilot/docs INSPECTOR_DATA=pilot/data INSPECTOR_OUT=pilot/out python -m ml.run OBJ-PILOT-ALT79B

УНДМС пропускается (сведения о бронировании). ИД распаковывается, только если в ней не больше MAX_ID файлов
(у СОШ 3 502, у ДОО 789 — для экспликаций, труб и стали она не нужна); такие строки в реестр не попадают.

    python pilot_objects.py            что будет распаковано и сколько места займёт
    python pilot_objects.py --extract  распаковать (повторный запуск докачивает недостающее)
"""
import csv
import glob
import io
import json
import os
import re
import shutil
import sys
import time
import unicodedata
import zipfile
from collections import Counter
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent
ZIPS = ROOT / "объекты"
ANNOT = ROOT / "02_МЕТОДИКА" / "multi_object_annotation_20260817"
DATA_SRC = ROOT / "01_ПАКЕТ" / "ХАКАТОН_УЧАСТНИКАМ_ГОТОВО_К_ПЕРЕДАЧЕ" / "02_ФОРМАТ_ДАННЫХ_И_ПРИМЕРЫ" / "data"
PILOT = ROOT / "pilot"
SKIP = ("УНДМС",)
MAX_ID = 300
SECTION = {"АР": "AR", "ВК": "VK", "КЖ": "KR", "КМ": "KR", "ОВ": "OV", "ЭОМ": "EOM", "СС": "SS", "ГП": "GP",
           "ПБ": "PB", "ПОС": "POS"}


def realname(info):
    if info.flag_bits & 0x800:
        return info.filename
    try:
        return info.filename.encode("cp437").decode("cp866")
    except Exception:
        return info.filename


def longpath(p):
    p = os.path.abspath(p)
    if os.name == "nt" and not p.startswith("\\\\?\\"):
        return "\\\\?\\" + p
    return p


def _loose(path):
    """Имя без знаков препинания: «–», «—», «…» не кодируются в cp866 и в архиве записаны иначе, чем в реестре."""
    return "~" + re.sub(r"[\W_]+", "", unicodedata.normalize("NFC", path).lower())


def registries():
    """object_code → строки реестра организаторов (без УНДМС)."""
    out = {}
    for fp in ANNOT.glob("*/*_РЕЕСТР_*.csv"):
        if any(s in fp.parent.name for s in SKIP):
            continue
        with open(fp, encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        out[rows[0]["file_id"].split("-")[0]] = rows
    return out


def plan():
    regs = registries()
    jobs = []
    for zp in sorted(ZIPS.glob("*.zip")):
        if any(s in zp.name for s in SKIP):
            continue
        zf = zipfile.ZipFile(zp)
        entries = {}
        for info in zf.infolist():
            if info.is_dir():
                continue
            name = realname(info).replace("\\", "/")
            rel = name.split("/", 1)[1] if "/" in name else name
            entries[rel] = entries[_loose(rel)] = (name, info)
        # реестр объекта — тот, чьи пути лучше всего совпадают с архивом
        code, rows = max(regs.items(), key=lambda kv: sum(r["relative_path"].replace("\\", "/") in entries
                                                           for r in kv[1]))
        n_id = sum(r["stage"] == "ID" for r in rows)
        take, missing = [], 0
        for r in rows:
            rel = r["relative_path"].replace("\\", "/")
            rel = rel if rel in entries else _loose(rel)
            if rel not in entries:
                missing += 1
                continue
            if r["stage"] == "ID" and n_id > MAX_ID:
                continue
            take.append((r, *entries[rel]))
        jobs.append((zp, zf, code, rows, take, missing))
    return jobs


def manifest_row(code, r, name):
    return {"schema_version": "0.1.0", "file_id": r["file_id"], "object_id": f"OBJ-PILOT-{code}",
            "corpus": r["case_name"], "dataset_role": "PILOT", "split": "PILOT", "relative_path": name,
            "extension": r["extension"].lower(), "size_bytes": int(r["bytes"]), "sha256": r["sha256"],
            "stage": r["stage"] if r["stage"] in ("PD", "RD", "ID") else "UNKNOWN",
            "section": SECTION.get(r["discipline"], "OTHER"), "pdf_pages": int(r["pdf_pages"] or 0),
            "annotation_status": "UNLABELED", "exclusion_reason": None, "duplicate_group": None,
            "distribution_status": "INCLUDE", "label_visibility": "PILOT"}


def main():
    extract = "--extract" in sys.argv
    jobs = plan()
    total = 0
    for zp, zf, code, rows, take, missing in jobs:
        size = sum(i.file_size for _, _, i in take)
        total += size
        st = Counter(r["stage"] for r, _, _ in take)
        skipped_id = sum(r["stage"] == "ID" for r in rows) - st.get("ID", 0)
        print(f"{code:7} {zp.name[:34]:34} файлов {len(take):4} ({dict(st)}), {size / 1e9:5.2f} ГБ"
              + (f", ИД пропущена ({skipped_id})" if skipped_id else "")
              + (f", нет в архиве {missing}" if missing else ""))
    print(f"всего {total / 1e9:.1f} ГБ; свободно на диске {shutil.disk_usage(ROOT).free / 1e9:.0f} ГБ")
    if not extract:
        return

    (PILOT / "data").mkdir(parents=True, exist_ok=True)
    for name in ("parameter_catalog_132.jsonl", "submission_schema.json", "split_policy.json"):
        shutil.copy2(DATA_SRC / name, PILOT / "data" / name)
    man = []
    t0, done = time.time(), 0
    for zp, zf, code, rows, take, missing in jobs:
        for n, (r, name, info) in enumerate(take, 1):
            out = longpath(PILOT / "docs" / name)
            if not (os.path.exists(out) and os.path.getsize(out) == info.file_size):
                os.makedirs(os.path.dirname(out), exist_ok=True)
                with zf.open(info) as s, open(out, "wb") as f:
                    while chunk := s.read(4 << 20):
                        f.write(chunk)
            done += info.file_size
            man.append(manifest_row(code, r, name))
            if n % 50 == 0 or n == len(take):
                print(f"  {code:7} {n:4}/{len(take)}  {done / 1e9:5.1f}/{total / 1e9:.1f} ГБ  "
                      f"{time.time() - t0:4.0f} с", flush=True)
    with open(PILOT / "data" / "document_manifest.jsonl", "w", encoding="utf-8") as f:
        for row in man:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"готово за {time.time() - t0:.0f} с; реестр: {len(man)} файлов → pilot/data/document_manifest.jsonl")


if __name__ == "__main__":
    main()
