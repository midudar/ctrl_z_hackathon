"""Собирает результаты порций из вывода сохранённого colab_strips.ipynb → out/strips/results.jsonl.

Каждая порция печатает свой блок `===STRIPS_BEGIN part=N sha256=…===` … `===STRIPS_END===`. Скрипт берёт
все готовые блоки, проверяет контрольные суммы, дописывает новые полосы к уже забранным и говорит,
каких порций ещё нет. Запускать можно после каждой порции.

    python colab/fetch_strips.py
    python colab/fetch_strips.py путь/к/ноутбуку.ipynb
"""
import base64
import hashlib
import io
import json
import re
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from ml import paths          # noqa: E402

NOTEBOOK = HERE / "colab_strips.ipynb"
BUNDLE = HERE / "strips_bundle.zip"
DEST = paths.OUT / "strips" / "results.jsonl"
BLOCK = re.compile(r"===STRIPS_BEGIN part=(\d+) sha256=([0-9a-f]{64})===\s*(.*?)\s*===STRIPS_END===", re.S)


def notebook_text(path):
    nb = json.loads(path.read_text(encoding="utf-8"))
    text = ""
    for cell in nb["cells"]:
        for out in cell.get("outputs", []):
            t = out.get("text", "")
            text += "".join(t) if isinstance(t, list) else t
    return text


def main():
    notebook = Path(sys.argv[1]) if len(sys.argv) > 1 else NOTEBOOK
    text = notebook_text(notebook)
    have = {}
    if DEST.exists():
        for line in DEST.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
                have[(r["file_id"], r["page"])] = line
            except json.JSONDecodeError:
                pass
    before = len(have)
    for part, sha, payload in BLOCK.findall(text):
        data = base64.b64decode(re.sub(r"\s+", "", payload))
        if hashlib.sha256(data).hexdigest() != sha:
            print(f"порция part={part}: контрольная сумма не совпала (вывод обрезан) — пропускаю")
            continue
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            lines = z.read("results.jsonl").decode("utf-8").splitlines()
        for line in lines:
            if line.strip():
                r = json.loads(line)
                have[(r["file_id"], r["page"])] = line
        print(f"порция part={part}: {len(lines)} полос")
    DEST.parent.mkdir(parents=True, exist_ok=True)
    DEST.write_text("\n".join(have.values()) + "\n", encoding="utf-8")
    print(f"\nвсего полос: {len(have)} (новых {len(have) - before}) → {DEST}")

    # каких полос ещё нет — по объектам
    idx = [json.loads(l) for l in zipfile.ZipFile(BUNDLE).read("index.jsonl").decode("utf-8").splitlines()]
    for obj in sorted({r["object_id"] for r in idx}):
        rows = [r for r in idx if r["object_id"] == obj]
        done = sum((r["file_id"], r["page"]) in have for r in rows)
        print(f"  {obj}: {done} из {len(rows)}")


if __name__ == "__main__":
    main()
