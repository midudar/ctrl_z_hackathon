"""Маленький архив для проверки PaddleOCR в Colab → colab/paddle_bench.zip (~10 МБ, загрузить на Google Drive).

Внутри две проверки на обучающих данных (Речникова нет):
- lines/ + lines.jsonl — те же 600 строк пилота организаторов, что в ml.ocr_eval и сравнении движков: вырезка,
  эталон из текстового слоя, ответы EasyOCR и Tesseract (для сравнения в одной таблице);
- acts/ + acts.jsonl — страницы 20 актов Новослободской (10 с классом бетона, 10 с арматурой) с текстовым слоем,
  отрендеренные как сканы Речникова (200 dpi, ч/б): что разбор актов извлечёт по OCR против текстового слоя.

    python colab/make_paddle_bench.py
"""
import io
import json
import sys
import zipfile
from pathlib import Path

import numpy as np
import pymupdf
from PIL import Image

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from ml import acts, paths                                  # noqa: E402
from ml.ocr_eval import PILOT, PREFIX                       # noqa: E402
from ml.pages import binarize, file_path                    # noqa: E402

OUT = HERE / "paddle_bench.zip"
TESS = Path(r"D:\hakaton\ocr_bench\bench_tesseract_200.json")
DPI = 200
ACTS = 20


def main():
    rows = paths.read_json(paths.OUT / "ocr_eval.json")
    tess = {r["line_id"]: r["tesseract"] for r in json.load(open(TESS, encoding="utf-8"))} if TESS.exists() else {}
    pilot = zipfile.ZipFile(PILOT)
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
        lines = []
        for r in rows:
            z.writestr(f"lines/{r['line_id']}.png", pilot.read(PREFIX + f"images/lines/{r['line_id']}.png"))
            lines.append({"line_id": r["line_id"], "gold": r["gold"], "easyocr": r["ocr"],
                          "tesseract": tess.get(r["line_id"], "")})
        z.writestr("lines.jsonl", "\n".join(json.dumps(l, ensure_ascii=False) for l in lines) + "\n")
        print(f"строк пилота: {len(lines)}")

        # акты — как в проверке Tesseract (D:\hakaton\ocr_bench\acts_check.py): поровну бетон и арматура
        picked = []
        for f in acts.id_files("OBJ-NOVOSLOBODSKAYA"):
            pages = acts.file_pages(f)
            if acts.act_starts(pages):
                ref = acts.parse_segment(f, pages)
                if ref["concrete"] or ref["rebar"]:
                    picked.append((f, pages, ref))
        conc = [p for p in picked if p[2]["concrete"]][: ACTS // 2]
        reb = [p for p in picked if p[2]["rebar"] and not p[2]["concrete"]][: ACTS - len(conc)]
        index = []
        for f, pages, _ in conc + reb:
            doc = pymupdf.open(file_path(f))
            for pno, _, src in pages:
                if src != "text":
                    continue
                pix = doc[pno - 1].get_pixmap(dpi=DPI, colorspace=pymupdf.csGRAY)
                img = binarize(np.frombuffer(pix.samples, np.uint8).reshape(pix.h, pix.w))
                buf = io.BytesIO()
                Image.fromarray(img).save(buf, "PNG", optimize=True)
                name = f"acts/{f}_{pno:04d}.png"
                z.writestr(name, buf.getvalue())
                index.append({"file_id": f, "page": pno, "img": name, "dpi": DPI})
        z.writestr("acts.jsonl", "\n".join(json.dumps(i, ensure_ascii=False) for i in index) + "\n")
        print(f"актов: {len(conc) + len(reb)}, страниц: {len(index)}")
    print(OUT, f"{OUT.stat().st_size / 2**20:.1f} МБ")


if __name__ == "__main__":
    main()
