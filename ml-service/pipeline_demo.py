# -*- coding: utf-8 -*-
"""
Демонстрация полного пайплайна «Инспектор ИИ» на реальной эталонной паре.
Цель: нарушение №1 из перечня организаторов —
«конфигурация приточных установок в венткамере 012 не соответствует ПД».
"""
import io, sys, re, json, math, hashlib, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
import pymupdf

_DOCS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "01_ПАКЕТ",
                     "ХАКАТОН_УЧАСТНИКАМ_ГОТОВО_К_ПЕРЕДАЧЕ", "01_ДОКУМЕНТАЦИЯ",
                     "Пример нарушений на чертежах")
PD = os.path.join(_DOCS, "ПД", "5 Сведения об инженерном оборудовании",
                  "5.4 Отопление, вентиляц", "V2_01-05-04-02-07_Том 5.4.2 ОВ (1).pdf")
RD = os.path.join(_DOCS, "Рабочая и исполнительная документация", "Полные разделы",
                  "АНО-150321-1-РД-ОВ1 изм. 4_в1 (1).pdf")

def head(n, t):
    print("\n" + "=" * 78)
    print("ЭТАП %s. %s" % (n, t))
    print("=" * 78)

# ---------------------------------------------------------------- ЭТАП 0
RE_REV = re.compile(r"изм\.?\s*(\d+)", re.I)
RE_CODE = re.compile(r"(АНО[/\-\s]?\d+[/\-\s]?\d+[/\-\s]?[А-Я0-9.\-]*)", re.I)

def sha256(path, limit=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while (b := f.read(limit)):
            h.update(b)
    return h.hexdigest()

def from_stamp(doc, probe_pages=(1, 2, 3, 10)):
    """Шифр и изменение берём из штампа/титула документа, а не из имени файла.
    Имя файла врёт: его переименовывают, теряют суффиксы, дублируют '(1)'."""
    code = rev = None
    for n in probe_pages:
        if n > doc.page_count:
            break
        txt = doc[n - 1].get_text()
        if code is None:
            m = RE_CODE.search(txt)
            if m:
                code = m.group(1).strip()
        if rev is None:
            m = RE_REV.search(txt)
            if m:
                rev = int(m.group(1))
        if code and rev is not None:
            break
    return code, rev

def register(path, file_id, stage):
    doc = pymupdf.open(path)
    name = os.path.basename(path)
    code, rev = from_stamp(doc)
    if rev is None:
        m = RE_REV.search(name.replace("_", " "))
        rev = int(m.group(1)) if m else None
    return {
        "file_id": file_id,
        "stage": stage,
        "discipline": "ОВ",
        "document_code": code or "?",
        "revision": rev,
        "approval_status": "APPROVED",
        "page_count": doc.page_count,
        "sha256": sha256(path)[:16] + "...",
        "_doc": doc,
    }

head(0, "Реестр файлов и редакций")
files = [register(PD, "F-PD-OV", "PD"), register(RD, "F-RD-OV1", "RD")]
for f in files:
    print("  %-10s stage=%-3s шифр=%-22s изм.=%-4s страниц=%-4d sha=%s"
          % (f["file_id"], f["stage"], f["document_code"], f["revision"], f["page_count"], f["sha256"]))
print("\n  Эталон сравнения — последняя утверждённая редакция каждой стадии.")
print("  Если бы нашлось два файла с одним шифром и разными изм. — старший в predecessor,")
print("  сравнение шло бы только по старшему. Конфликт → CLARIFICATION_REQUIRED.")

# ---------------------------------------------------------------- ЭТАП 1
RE_ROOM = re.compile(r"^\d{3}$")
RE_MARK = re.compile(r"^(?:[ПВ]\d+(?:\.\d+)?|ПЕ\d*|ВЕ\d*)$")

def read_page(doc, pno):
    """Единое представление страницы — источник (вектор/скан) дальше не важен."""
    pg = doc[pno - 1]
    words = pg.get_text("words")
    paths = pg.get_drawings()
    is_vector = len(words) > 20 and len(paths) > 200
    tokens = [{"text": w[4], "bbox": (w[0], w[1], w[2], w[3]),
               "cx": (w[0] + w[2]) / 2, "cy": (w[1] + w[3]) / 2, "conf": 1.0}
              for w in words]
    return {"page": pno, "w": pg.rect.width, "h": pg.rect.height,
            "tokens": tokens, "paths": paths,
            "source": "PDF_VECTOR" if is_vector else "NEEDS_OCR"}

head(1, "Страница → единое представление")
p_pd = read_page(files[0]["_doc"], 104)
p_rd = read_page(files[1]["_doc"], 17)
for lbl, p in (("ПД лист 26", p_pd), ("РД ОВ1 лист 3", p_rd)):
    print("  %-15s стр.%-4d %5.0fx%-5.0f pt  источник=%-11s токенов=%-5d путей=%d"
          % (lbl, p["page"], p["w"], p["h"], p["source"], len(p["tokens"]), len(p["paths"])))
print("\n  Если бы source был NEEDS_OCR — та же структура заполнялась бы из OCR,")
print("  а в conf лежала бы уверенность распознавания. Дальнейший код не меняется.")

# ---------------------------------------------------------------- ЭТАП 2
def long_segments(paths, min_len=60):
    """Длинные отрезки — кандидаты в стены и оси (основа для полигонов помещений)."""
    out = []
    for p in paths:
        for it in p["items"]:
            if it[0] != "l":
                continue
            a, b = it[1], it[2]
            if math.hypot(b.x - a.x, b.y - a.y) >= min_len:
                out.append(((a.x, a.y), (b.x, b.y), p.get("width") or 0))
    return out

def extract(p):
    rooms = [t for t in p["tokens"] if RE_ROOM.match(t["text"])]
    marks = [t for t in p["tokens"] if RE_MARK.match(t["text"])]
    return {"rooms": rooms, "marks": marks, "walls": long_segments(p["paths"])}

head(2, "Извлечение сущностей")
e_pd, e_rd = extract(p_pd), extract(p_rd)
for lbl, e in (("ПД лист 26", e_pd), ("РД ОВ1 лист 3", e_rd)):
    print("  %-15s помещений=%-3d марок систем=%-3d длинных отрезков(стены/оси)=%d"
          % (lbl, len(e["rooms"]), len(e["marks"]), len(e["walls"])))
print("\n  помещения ПД: %s" % sorted({t["text"] for t in e_pd["rooms"]}))
print("  помещения РД: %s" % sorted({t["text"] for t in e_rd["rooms"]})[:20])

# ---------------------------------------------------------------- ЭТАП 3
def room_set(doc, pno):
    try:
        w = doc[pno - 1].get_text("words")
    except Exception:
        return set()
    return {x[4] for x in w if RE_ROOM.match(x[4])}

MIN_COMMON = 3  # без этого порога одна общая комната даёт overlap 1.00 — ложная связка

def link(pd_rooms, rd_doc, scan=80):
    """Ищем страницу РД, лучше всего совпадающую с листом ПД по составу помещений.
    Ранжируем по РАЗМЕРУ пересечения, а не по доле: доля вырождается на малых наборах."""
    best = []
    for n in range(1, min(scan, rd_doc.page_count) + 1):
        rs = room_set(rd_doc, n)
        if not rs:
            continue
        inter = pd_rooms & rs
        if len(inter) < MIN_COMMON:
            continue
        ov = len(inter) / min(len(pd_rooms), len(rs))
        best.append((len(inter), ov, n, len(rs), sorted(inter)))
    best.sort(reverse=True)
    return best

head(3, "Сопоставление страниц ПД ↔ РД по составу помещений")
pd_rooms = {t["text"] for t in e_pd["rooms"]}
cand = link(pd_rooms, files[1]["_doc"])
print("  ищем среди первых 80 страниц РД лист, где встречаются те же помещения")
print("  порог: минимум %d общих помещения, иначе связка считается случайной\n" % MIN_COMMON)
print("  %-7s %-8s %-8s %-10s %s" % ("общих", "overlap", "стр.РД", "помещений", "общие помещения"))
for k, ov, n, cnt, inter in cand[:6]:
    mark = "  <-- выбрано" if n == cand[0][2] else ""
    print("  %-7d %-8.2f %-8d %-10d %s%s" % (k, ov, n, cnt, inter, mark))
if not cand:
    print("  подходящей страницы РД не найдено → COMPARISON_IMPOSSIBLE")
linked_page = cand[0][2] if cand else None

# ---------------------------------------------------------------- ЭТАП 4
def nearest_room(mark, rooms):
    """Марку относим к ближайшему номеру помещения — масштаб листа не важен."""
    if not rooms:
        return None, None
    best = min(rooms, key=lambda r: math.hypot(r["cx"] - mark["cx"], r["cy"] - mark["cy"]))
    return best["text"], math.hypot(best["cx"] - mark["cx"], best["cy"] - mark["cy"])

def by_room(e):
    idx = {}
    for m in e["marks"]:
        rm, dist = nearest_room(m, e["rooms"])
        if rm is None:
            continue
        idx.setdefault(rm, set()).add(m["text"])
    return idx

head(4, "Сравнение: набор элементов по помещению")
# сравниваем ИМЕННО ту страницу РД, которую выбрал этап 3, а не заданную вручную
if linked_page and linked_page != p_rd["page"]:
    print("  этап 3 выбрал стр.%d — перечитываем её вместо стр.%d\n" % (linked_page, p_rd["page"]))
    p_rd = read_page(files[1]["_doc"], linked_page)
    e_rd = extract(p_rd)
idx_pd, idx_rd = by_room(e_pd), by_room(e_rd)
room = "012"
set_pd, set_rd = idx_pd.get(room, set()), idx_rd.get(room, set())
print("  помещение %s (венткамера)" % room)
print("    ПД: %s" % (sorted(set_pd) or "—"))
print("    РД: %s" % (sorted(set_rd) or "—"))
print("    пропало в РД : %s" % (sorted(set_pd - set_rd) or "—"))
print("    добавлено в РД: %s" % (sorted(set_rd - set_pd) or "—"))

# ---------------------------------------------------------------- ЭТАП 5
head(5, "Вердикт и карточка доказательства")
missing = sorted(set_pd - set_rd)

# --- страховка от ложного срабатывания -------------------------------------
# Если на одной стадии извлечено в разы меньше элементов, чем на другой,
# вероятнее не «всё снесли», а наш экстрактор недобрал. Обвинять нельзя.
n_pd, n_rd = len(e_pd["marks"]), len(e_rd["marks"])
ratio = min(n_pd, n_rd) / max(n_pd, n_rd, 1)
low_recall = ratio < 0.25
print("  контроль извлечения: марок на ПД=%d, на РД=%d, отношение %.2f -> %s"
      % (n_pd, n_rd, ratio, "ПОДОЗРИТЕЛЬНО МАЛО" if low_recall else "сопоставимо"))

if low_recall:
    label, status = "COMPARISON_IMPOSSIBLE", "COMPARISON_IMPOSSIBLE"
    reason = ("наборы элементов несопоставимы по полноте извлечения "
              "(%d против %d) — расхождение может быть артефактом парсинга" % (n_pd, n_rd))
elif not set_pd or not set_rd:
    label, status = "COMPARISON_IMPOSSIBLE", "COMPARISON_IMPOSSIBLE"
    reason = "в одной из стадий не извлечён набор элементов для помещения"
elif missing:
    label, status = "VIOLATION_PRESENT", "CRITICAL"
    reason = "элементы, предусмотренные ПД, не найдены в РД"
else:
    label, status = "NO_VIOLATION", "OK"
    reason = "наборы элементов совпадают"

sub = {
    "object_id": "OBJ-TYUMENSKAYA-5",
    "checks": [{
        "parameter_code": "IOS4-079",
        "location": room,
        "pd_value": "приточные установки: " + ", ".join(sorted(set_pd)) if set_pd else None,
        "rd_value": "приточные установки: " + ", ".join(sorted(set_rd)) if set_rd else None,
        "violation_label": label,
        "protocol_status": status,
        "criticality": "Критическое (приостановка работ)" if label == "VIOLATION_PRESENT" else None,
        "evidence": [
            {"stage": "PD", "file_id": files[0]["file_id"], "pdf_page_number": p_pd["page"]},
            {"stage": "RD", "file_id": files[1]["file_id"], "pdf_page_number": linked_page},
        ],
    }],
}
print("  обоснование: %s\n" % reason)
print(json.dumps(sub, ensure_ascii=False, indent=2))
