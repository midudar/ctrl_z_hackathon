"""Разведка EI дверей: ПД (ПБ / ППМ / АР / ПЗ) и РД (АР) — значения, контекст, примеры строк.

    INSPECTOR_DOCS=pilot/docs INSPECTOR_DATA=pilot/data python recon_doors.py OBJ… [OBJ…]
"""
import re
import sys
from collections import Counter

sys.path.insert(0, r"D:\hakaton\ltc")
import pymupdf
from ml import paths, revisions
from ml.pages import file_path

EI_RE = re.compile(r"(?<![A-Za-zА-Яа-я])(?P<cls>R?E\s?I\s?(?P<sw>[SW]{0,2}))\s?[-–]?\s?(?P<v>15|30|45|60|90|120|150|180)(?!\d)")
DOOR_RE = re.compile(r"двер\w*|ворот\w*|люк\w*|полотн\w*|заполнени\w*\s+проем\w*|(?<![А-Яа-я])Д[ПМ][МСО]?(?![а-я])|"
                     r"(?<![А-Яа-я])ДП(?![а-я])", re.I)
BAD_RE = re.compile(r"клапан\w*|воздуховод\w*|стен\w*|перегород\w*|перекрыт\w*|проходк\w*|кабел\w*|шахт\w*|"
                    r"огнезащит\w*|штор\w*|занавес\w*|экран\w*|конструкц\w*|колонн\w*|балк\w*|покрыти\w*|"
                    r"REI|\bR\s?\d", re.I)
PD_FILE_RE = re.compile(r"[-_ (.](ПБ|ППМ|МПБ|АР|ПЗ|ОПЗ)[\s._\d)-]|пожарн", re.I)
RD_FILE_RE = re.compile(r"[-_ (.](АР|АС|АИ)[\s._\d)-]", re.I)


def files(obj, stage):
    rows = [r for r in paths.read_jsonl(paths.MANIFEST) if r["object_id"] == obj and r["extension"] == ".pdf"
            and r["stage"] in ({"PD"} if stage == "PD" else {"RD", "RD_ID_MIXED"})]
    keep = {r["file_id"] for r in revisions.latest(rows)[0]}
    rx = PD_FILE_RE if stage == "PD" else RD_FILE_RE
    secs = {"PB", "AR"} if stage == "PD" else {"AR"}
    return [r["file_id"] for r in rows if r["file_id"] in keep and (r["section"] in secs or rx.search(" " + r["relative_path"]))]


def main(objs):
    for obj in objs:
        print(f"\n######## {obj}")
        for stage in ("PD", "RD"):
            fids = files(obj, stage)
            door, alone, bad = Counter(), Counter(), Counter()
            ex_door, ex_alone = [], []
            for fid in fids:
                try:
                    doc = pymupdf.open(file_path(fid))
                except Exception:
                    continue
                for pno, page in enumerate(doc, 1):
                    L = [" ".join(l.split()) for l in page.get_text().splitlines() if l.strip()]
                    for i, l in enumerate(L):
                        for m in EI_RE.finditer(l):
                            key = f"{m.group('cls').replace(' ', '')}{m.group('v')}"
                            if key.startswith("R"):
                                bad[key] += 1
                                continue
                            if DOOR_RE.search(l) and not re.search(r"клапан|воздуховод|проходк|кабел", l, re.I):
                                door[key] += 1
                                if len(ex_door) < 8:
                                    ex_door.append((fid, pno, l[:120]))
                            elif re.fullmatch(r"[\s()A-Za-z0-9–-]{3,30}", l) and not BAD_RE.search(" ".join(L[max(0, i - 1): i + 2])):
                                alone[key] += 1
                                if len(ex_alone) < 5:
                                    ex_alone.append((fid, pno, " | ".join(L[max(0, i - 2): i + 3])[:120]))
                            else:
                                bad[key] += 1
            print(f"  {stage}: файлов {len(fids)} | с дверью {dict(door.most_common(8))} | отдельные метки {dict(alone.most_common(6))}"
                  f" | прочее {dict(bad.most_common(5))}")
            for x in ex_door[:6]:
                print("     дверь:", x)
            for x in ex_alone[:3]:
                print("     метка:", x)


if __name__ == "__main__":
    main(sys.argv[1:])
