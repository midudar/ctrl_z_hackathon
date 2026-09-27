"""Разведка ЭОМ на объектах пилота (и обучающих): кабели с индексом исполнения, контекст СПЗ, источники света.

    INSPECTOR_DOCS=pilot/docs INSPECTOR_DATA=pilot/data python recon_eom.py [объект ...]
"""
import re
import sys
from collections import Counter, defaultdict

sys.path.insert(0, r"D:\hakaton\ltc")
import pymupdf
from ml import paths, registry
from ml.pages import file_path

CABLE_RE = re.compile(r"(?<![А-ЯA-Zа-я])((?:А?ВВГ|А?ППГ|А?ПвВГ|А?ПвПГ|АПвП[уг]?|КПС[эЭ]?|КПКЭ|КВВГ|КГ|ПуГВ|ПВ[13]|NYM|ВБШв|АВБШв|"
                      r"ПвБШв|АПвБШв|ВВГЭ|FRLS|КСРЭ|КСБ|UTP|FTP|МКЭШ|КуПЭВ|РК\s?50)[А-Яа-яA-Za-z]*)"
                      r"(?:\s?-?\s?нг\s?\(?\s?[АA]\s?\)?)?(?:\s?-\s?[A-Za-z]{2,8})?", re.I)
INDEX_RE = re.compile(r"нг\s?\(?\s?[АA](?:\s?\)|\b)?(?:\s?-\s?(?P<idx>FRLSLTx|FRHFLTx|FRLS|FRHF|LSLTx|HFLTx|LS|HF))?", re.I)
FIRE_RE = re.compile(r"противопожарн|пожарн|СПЗ|АУПС|АУПТ|СОУЭ|дымоудал|подпор|аварийн\w* освещ|эвакуацион\w* освещ|"
                     r"огнестойк|ППУ|ПЗУ|лифт\w* для (?:перевозки )?пожарн|ППЗ|\bПС\b|систем\w+ противопожарной", re.I)
LIGHT_RE = re.compile(r"светодиод|LED\b|люминесцент|накаливан|галоген|ДРЛ|ДНаТ|МГЛ|компактн\w+ люминесц|\bЛЛ\b|\bКЛЛ\b", re.I)
PHOTOLUM_RE = re.compile(r"фотолюминесц", re.I)


def lines_of(fid):
    try:
        doc = pymupdf.open(file_path(fid))
    except Exception as e:
        return
    for pno, page in enumerate(doc, 1):
        try:
            t = page.get_text()
        except Exception:
            continue
        for l in t.splitlines():
            l = " ".join(l.split())
            if l:
                yield pno, l


def main(objs):
    man = paths.read_jsonl(paths.MANIFEST)
    objs = objs or sorted({r["object_id"] for r in man})
    for obj in objs:
        print(f"\n######## {obj}")
        for stage in ("PD", "RD"):
            fids = registry.files(obj, stage, "EOM")
            idx = Counter()
            fire_idx = Counter()
            light = Counter()
            ex_fire, ex_light = [], []
            for fid in fids:
                for pno, l in lines_of(fid):
                    for m in INDEX_RE.finditer(l):
                        k = (m.group("idx") or "нг(А)").upper().replace("LSLTX", "LSLTx").replace("FRLSLTX", "FRLSLTx")
                        idx[k] += 1
                        if FIRE_RE.search(l):
                            fire_idx[k] += 1
                            if len(ex_fire) < 6:
                                ex_fire.append((fid, pno, l[:120]))
                    if LIGHT_RE.search(l) and not PHOTOLUM_RE.search(l):
                        for w in re.findall(LIGHT_RE, l):
                            light[w.lower()[:10]] += 1
                        if len(ex_light) < 6 and not re.search(r"светодиод", l, re.I):
                            ex_light.append((fid, pno, l[:120]))
            print(f"  {stage}: файлов ЭОМ {len(fids)} | индексы {dict(idx.most_common(6))} | в строках про СПЗ {dict(fire_idx)}"
                  f" | свет {dict(light.most_common(6))}")
            for x in ex_fire[:4]:
                print("     СПЗ:", x)
            for x in ex_light[:4]:
                print("     свет не LED:", x)


if __name__ == "__main__":
    main(sys.argv[1:])
