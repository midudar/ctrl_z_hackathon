"""Пакет для OCR в Colab: PDF, переименованные в <file_id>.pdf, и jobs.json со списком страниц.

    python colab/make_bundle.py            # → colab/colab_bundle.zip

Результат Colab (cache.zip) распаковывается в out/cache/ как есть: пути там те же, что у ml/pages.py.
"""
import json
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ml import pages  # noqa: E402

# file_id → страницы для OCR. Чертёжные листы Тюменской (см. ml/rooms.py DRAWINGS).
JOBS = {
    "F0201": [17, 18, 19, 20, 22, 23, 24, 25],
    "F0202": [15, 16, 17, 18],
    "F0171": list(range(85, 104)),
}

OUT = Path(__file__).resolve().parent / "colab_bundle.zip"


def main():
    jobs = []
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_STORED) as z:
        for file_id, page_list in JOBS.items():
            z.write(pages.file_path(file_id), f"{file_id}.pdf")
            jobs.append({"file_id": file_id, "sha16": pages.file_sha(file_id)[:16], "pages": page_list})
        z.writestr("jobs.json", json.dumps(jobs, ensure_ascii=False, indent=1))
    print(f"{OUT}: {OUT.stat().st_size / 2**20:.1f} МБ, файлов {len(jobs)}, страниц {sum(len(j['pages']) for j in jobs)}")


if __name__ == "__main__":
    main()
