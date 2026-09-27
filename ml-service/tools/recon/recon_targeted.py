"""Прицельная разведка: строка, где рядом предмет параметра и его значение с единицей (ПД и РД объекта)."""
import re
import sys
from collections import defaultdict

sys.path.insert(0, r"D:\hakaton\ltc")
import pymupdf
from ml import paths, revisions
from ml.pages import file_path

OBJ = sys.argv[1] if len(sys.argv) > 1 else "OBJ-RECHNIKOV-7-7"
I = re.I
M2 = r"\d[\d\s]*[,.]?\d*\s*(?:м2|м²|кв\.?\s*м)"
TARGETS = {
    "SPZU-025 асфальт, м²": [re.compile(r"асфальт", I), re.compile(M2, I)],
    "SPZU-026 плитка, м²": [re.compile(r"плит(?:очн|к)|брусчат", I), re.compile(M2, I)],
    "SPZU-027 озеленение, м²": [re.compile(r"озелен|газон", I), re.compile(M2, I)],
    "ZU-125 утеплитель стен, мм": [re.compile(r"утепл|теплоизол|минераловат|минплит", I),
                                   re.compile(r"стен|фасад", I), re.compile(r"\d{2,3}\s*мм", I)],
    "ZU-128 утеплитель кровли, мм": [re.compile(r"утепл|теплоизол|минераловат|экструз|XPS", I),
                                     re.compile(r"кровл|покрыти", I), re.compile(r"\d{2,3}\s*мм", I)],
    "PPM-103 EI дверей": [re.compile(r"двер|ворот|люк", I), re.compile(r"\bE[IШ]\s?[SW]?\s?\d{2,3}\b")],
    "PPM-113/114 расход на пожаротушение, л/с": [re.compile(r"пожар", I), re.compile(r"\d+[,.]?\d*\s*л/с", I)],
    "IOS3-075 трубы канализации": [re.compile(r"канализ|\bК1\b|\bК2\b|водосток", I),
                                   re.compile(r"\bПВХ\b|НПВХ|\bПП\b|полипропилен|чугун|полиэтилен|ПЭ\b", I)],
    "IOS2-072 трубы водопровода": [re.compile(r"водопровод|\bВ1\b|\bТ3\b|горяч", I),
                                   re.compile(r"оцинк|\bПП\b|полипропилен|PPR|металлопласт|нержаве|сшит", I)],
    "KR-056 сталь": [re.compile(r"(?<![\wА-Яа-я])[СC]\s?(?:235|245|255|345|355|390)\b")],
    "AR-041/ODI-117 ширина дверей": [re.compile(r"ширин", I), re.compile(r"двер|проём|проем", I),
                                     re.compile(r"\d[,.]\d{1,2}\s*м\b|\d{3,4}\s*мм", I)],
    "PZ-012/SPZU-037 машино-места, число": [re.compile(r"машино-?мест|парковочн\w*\s+мест|стоянк", I),
                                            re.compile(r"\b\d{1,4}\s*(?:шт|м/м|машино|мест)", I)],
}


def main():
    man = [r for r in paths.read_jsonl(paths.MANIFEST) if r["object_id"] == OBJ and r["extension"] == ".pdf"]
    hits = {k: {"PD": [], "RD": []} for k in TARGETS}
    for stage, sts in (("PD", {"PD"}), ("RD", {"RD", "RD_ID_MIXED"})):
        rows, _ = revisions.latest([r for r in man if r["stage"] in sts])
        for r in rows:
            try:
                doc = pymupdf.open(file_path(r["file_id"]))
            except Exception:
                continue
            name = r["relative_path"].split("/")[-1][:38]
            for pno, page in enumerate(doc, 1):
                for line in page.get_text().splitlines():
                    for k, rxs in TARGETS.items():
                        if all(rx.search(line) for rx in rxs):
                            hits[k][stage].append((r["file_id"], pno, name, " ".join(line.split())[:120]))
    for k, h in hits.items():
        pdf, rdf = sorted({x[0] for x in h["PD"]}), sorted({x[0] for x in h["RD"]})
        print(f"\n== {k}: ПД {len(h['PD'])} строк в {len(pdf)} файлах | РД {len(h['RD'])} строк в {len(rdf)} файлах")
        for st in ("PD", "RD"):
            seen = set()
            for f, p, n, l in h[st]:
                if l.lower() in seen:
                    continue
                seen.add(l.lower())
                print(f"   {st} {f}:{p} [{n}]  {l}")
                if len(seen) >= 3:
                    break


if __name__ == "__main__":
    main()
