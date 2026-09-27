# -*- coding: utf-8 -*-
"""Сверка распакованной папки с оглавлением архива: имя + размер каждого файла."""
import zipfile, sys, io, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

src, dst = sys.argv[1], sys.argv[2]
PREFIX = "\\\\?\\"          # в строке это \\?\ — снимает лимит 260 символов

def realname(i):
    if i.flag_bits & 0x800:
        return i.filename
    try:
        return i.filename.encode("cp437").decode("cp866")
    except Exception:
        return i.filename

def longpath(p):
    p = os.path.abspath(p)
    if os.name == "nt" and not p.startswith(PREFIX):
        return PREFIX + p
    return p

z = zipfile.ZipFile(src)
items = [i for i in z.infolist() if not i.is_dir()]
missing, wrong, ok, okb = [], [], 0, 0

for i in items:
    parts = [p for p in realname(i).replace("\\", "/").split("/") if p not in ("", ".", "..")]
    out = longpath(os.path.join(dst, *parts))
    if not os.path.exists(out):
        missing.append(realname(i))
    elif os.path.getsize(out) != i.file_size:
        wrong.append((realname(i), os.path.getsize(out), i.file_size))
    else:
        ok += 1
        okb += i.file_size

print("в архиве файлов   : %d  (%.2f ГБ)" % (len(items), sum(i.file_size for i in items) / 1e9))
print("распаковано верно : %d  (%.2f ГБ)" % (ok, okb / 1e9))
print("отсутствует       : %d" % len(missing))
print("неверный размер   : %d" % len(wrong))
for m in missing[:10]:
    print("   нет :", m)
for w in wrong[:10]:
    print("   бит :", w[0], w[1], "вместо", w[2])
print()
print("ИТОГ:", "ПОЛНОЕ СОВПАДЕНИЕ" if not missing and not wrong else "ЕСТЬ РАСХОЖДЕНИЯ")
