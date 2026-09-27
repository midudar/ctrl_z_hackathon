# -*- coding: utf-8 -*-
"""Распаковка больших ZIP64-архивов, которые не открывает Проводник Windows.
Заодно чинит русские имена файлов (cp437 -> cp866)."""
import zipfile, sys, io, os, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

src, dst = sys.argv[1], sys.argv[2]

def realname(info):
    if info.flag_bits & 0x800:
        return info.filename
    try:
        return info.filename.encode("cp437").decode("cp866")
    except Exception:
        return info.filename

def longpath(p):
    """Обходим лимит Windows в 260 символов: префикс \\\\?\\ снимает его.
    Требует абсолютный путь только с обратными слэшами."""
    p = os.path.abspath(p)
    if os.name == "nt" and not p.startswith("\\\\?\\"):
        return "\\\\?\\" + p
    return p

os.makedirs(dst, exist_ok=True)
z = zipfile.ZipFile(src)
items = [i for i in z.infolist() if not i.is_dir()]
total = sum(i.file_size for i in items)
print("архив : %s" % os.path.basename(src))
print("файлов: %d, объём %.2f ГБ" % (len(items), total / 1e9))
print("куда  : %s\n" % dst)

done = 0
skipped = 0
t0 = time.time()
for n, info in enumerate(items, 1):
    name = realname(info).replace("\\", "/")
    parts = [p for p in name.split("/") if p not in ("", ".", "..")]
    out = longpath(os.path.join(dst, *parts))
    # уже распакован целиком — пропускаем, чтобы можно было продолжить с места обрыва
    if os.path.exists(out) and os.path.getsize(out) == info.file_size:
        done += info.file_size
        skipped += 1
        continue
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with z.open(info) as s, open(out, "wb") as f:
        while (chunk := s.read(4 << 20)):
            f.write(chunk)
    done += info.file_size
    if n % 25 == 0 or n == len(items):
        el = time.time() - t0
        print("  %3d/%d  %5.2f/%.2f ГБ  %4.0f с" % (n, len(items), done / 1e9, total / 1e9, el), flush=True)
print("\nГОТОВО за %.0f с | распаковано %d, пропущено уже готовых %d"
      % (time.time() - t0, len(items) - skipped, skipped))
