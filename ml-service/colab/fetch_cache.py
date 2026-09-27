"""Достаёт cache.zip из вывода ячейки 5 сохранённого colab_ocr.ipynb и распаковывает в out/cache/.

    python colab/fetch_cache.py
"""
import base64
import hashlib
import io
import json
import re
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
NOTEBOOK = HERE / "colab_ocr.ipynb"
DEST = HERE.parent / "out" / "cache"


def main():
    nb = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
    text = ""
    for cell in nb["cells"]:
        for out in cell.get("outputs", []):
            t = out.get("text", "")
            text += "".join(t) if isinstance(t, list) else t
    m = re.search(r"===CACHE_ZIP_BEGIN===\s*(.*?)\s*===CACHE_ZIP_END===", text, re.S)
    if not m:
        raise SystemExit("В ноутбуке нет вывода с архивом: запусти ячейку 5 и сохрани ноутбук (Ctrl+S).")
    data = base64.b64decode(re.sub(r"\s+", "", m.group(1)))
    sha = re.search(r"sha256 ([0-9a-f]{64})", text)
    if sha and hashlib.sha256(data).hexdigest() != sha.group(1):
        raise SystemExit("Контрольная сумма не совпала: вывод обрезан. Нужна выгрузка по частям.")
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = [n for n in z.namelist() if n.endswith(".json")]
        z.extractall(DEST)
    print(f"распаковано {len(names)} страниц в {DEST}")


if __name__ == "__main__":
    main()
