"""Разведка PZ-014: расчётная / разрешённая мощность объекта в ПД (ИОС1, ПЗ) и РД (ЭОМ).

    INSPECTOR_DOCS=pilot/docs INSPECTOR_DATA=pilot/data python recon_power.py OBJ…
"""
import re
import sys

sys.path.insert(0, r"D:\hakaton\ltc")
import pymupdf
from ml import registry
from ml.pages import file_path

KW = re.compile(r"расч[её]тн\w*\s+(?:электрическ\w*\s+)?(?:нагрузк\w*|мощност\w*)|(?<![А-Яа-яA-Za-z])[РP]\s?р(?:асч)?\.?(?![а-яa-z])|"
                r"разреш[её]нн\w*\s+(?:к\s+использованию\s+)?мощност\w*|максимальн\w*\s+мощност\w*|"
                r"установленн\w*\s+мощност\w*|присоединяем\w*\s+мощност\w*", re.I)
KW_VAL = re.compile(r"\d[\d\s]*[.,]?\d*\s*кВт", re.I)
TOTAL = re.compile(r"итого|всего|по\s+здани|по\s+объекту|на\s+объект|здани\w+\s+в\s+цел|ВРУ|ГРЩ|ТУ\b|техническ\w+\s+услови|"
                   r"вводно\w*|жил\w+\s+дом|школ\w*|корпус", re.I)


def main(objs):
    for obj in objs:
        print(f"\n######## {obj}")
        for stage in ("PD", "RD"):
            files = set(registry.files(obj, stage, "EOM")) | (set(registry.files(obj, stage, "PZ")) if stage == "PD" else set())
            hits = []
            for fid in sorted(files):
                try:
                    doc = pymupdf.open(file_path(fid))
                except Exception:
                    continue
                for pno, page in enumerate(doc, 1):
                    L = [" ".join(l.split()) for l in page.get_text().splitlines() if l.strip()]
                    for i, l in enumerate(L):
                        if KW.search(l) and KW_VAL.search(" ".join(L[i:i + 2])):
                            ctx = " ".join(L[max(0, i - 1): i + 2])
                            if TOTAL.search(ctx):
                                hits.append((fid, pno, ctx[:150]))
            print(f"  {stage}: файлов {len(files)}, строк с итогом/ТУ {len(hits)}")
            seen = set()
            for h in hits:
                if h[2] in seen:
                    continue
                seen.add(h[2])
                if len(seen) > 7:
                    break
                print("     ", h)


if __name__ == "__main__":
    main(sys.argv[1:])
