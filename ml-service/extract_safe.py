"""Выборочная распаковка архивов без закрытых организаторских файлов и разметки тестового объекта.

Запуск: python extract_safe.py
"""
import os
import zipfile

ROOT = r"D:\hakaton\ltc"

# Всё, что содержит ответы на тест или помечено do_not_release. Сравнение идёт по пути в нижнем регистре.
FORBIDDEN = (
    "организатор",          # ОРГАНИЗАТОР_ЗАКРЫТЫЙ, ПАКЕТ_ОРГАНИЗАТОРА_ЗАКРЫТЫЙ, TEST_HIDDEN_ОРГАНИЗАТОР
    "rechnikov",            # rechnikov_7_7_comparison_* — разметка тестового объекта
    "речников",
    "hidden_gold",
    "all_gold_checks",
    "review_queue",
    "evidence_index",
)

JOBS = [
    # (архив, префикс внутри архива, куда распаковать)
    ("02_ЭТАЛОННАЯ_РАЗМЕТКА_И_МЕТОДИКА.zip", "", r"D:\hakaton\ltc\02_МЕТОДИКА"),
    ("РАЗМЕЧЕННЫЙ_TRAIN_PUBLIC_203.zip", "РАЗМЕЧЕННЫЙ_TRAIN_PUBLIC_203/data/", r"D:\hakaton\ltc\TRAIN_data"),
]


def decode(info):
    if info.flag_bits & 0x800:
        return info.filename
    return info.filename.encode("cp437").decode("cp866")


def long_path(p):
    p = os.path.abspath(p)
    return p if p.startswith("\\\\?\\") else "\\\\?\\" + p


def main():
    for archive, prefix, dest in JOBS:
        z = zipfile.ZipFile(os.path.join(ROOT, archive))
        done = skipped = 0
        for info in z.infolist():
            name = decode(info)
            if name.endswith("/") or not name.startswith(prefix):
                continue
            if any(f in name.lower() for f in FORBIDDEN):
                skipped += 1
                continue
            rel = name[len(prefix):]
            target = long_path(os.path.join(dest, *rel.split("/")))
            if os.path.exists(target) and os.path.getsize(target) == info.file_size:
                done += 1
                continue
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with z.open(info) as src, open(target, "wb") as dst:
                while chunk := src.read(1 << 20):
                    dst.write(chunk)
            done += 1
        print(f"{archive}: распаковано {done}, пропущено закрытых {skipped} -> {dest}")


if __name__ == "__main__":
    main()
