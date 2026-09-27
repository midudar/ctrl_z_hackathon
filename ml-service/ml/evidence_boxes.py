"""Рамки доказательств: к каждой ссылке «файл + страница» в ответе — прямоугольник найденного фрагмента.

ТЗ 9.1, п. 4: для извлечённого значения сохраняется «нормализованный bbox/polygon. Координаты приводятся к диапазону
[0;1] относительно видимой области страницы после учёта CropBox, MediaBox и Rotate». Интерфейс по ним подсвечивает
фрагмент на картинке страницы.

Как ищется фрагмент: значение из ответа (`pd_value` для страницы ПД, `rd_value` — РД, `id_value` — акта) превращается
в регулярки («B25/B30» → «В25», «В30» в кириллице и латинице; «1200/1500 мм» → числа; «А500С»; «550×1200»; «159.95» →
«159,95»), и на странице выбирается строка, где их больше всего, с приоритетом строки, где рядом названа конструкция
(«фундаментн…», «толщин…»). Для помещений (трек ОВ) обводится номер помещения на плане. Строки — текстовый слой, а на
сканах — токены OCR из кэша (`out/cache`), собранные в строки.

Координаты: слова текстового слоя PyMuPDF отдаёт в системе неповёрнутой страницы (относительно CropBox), поэтому они
умножаются на `page.rotation_matrix`; токены OCR получены с картинки видимой страницы и уже в её системе. Проверено
глазами на листе с Rotate 270, на листе с CropBox, смещённым на 3604 pt, и на листе со смещённым MediaBox.

Поля, которые добавляются в элемент evidence (схема ответа лишние поля допускает):
    bbox_norm    [x0, y0, x1, y1] в долях видимой страницы, начало — левый верхний угол (как на картинке страницы);
                 null — фрагмент не найден, показывать страницу целиком;
    bbox_source  "text_layer" | "ocr" — откуда взят текст фрагмента;
    fragment     текст найденной строки (до 200 символов) — то, что подсвечено.

    python -m ml.evidence_boxes out/submission_OBJ-NOVOSLOBODSKAYA.json [--png папка]
"""
import re
import sys
from pathlib import Path

import pymupdf

from ml import paths
from ml.concrete import page_lines as text_lines
from ml.location import ROOM, location_type
from ml.pages import file_path, has_ocr, read_page

STAGE_VALUE = {"PD": "pd_value", "RD": "rd_value", "ID": "id_value"}
NEAR_LINES = 3            # на сколько высот строки вокруг значения ищется название конструкции
SAME_ROW_GAP = 350        # pt: название и значение в колонках одной строки таблицы (в записках ПД ~220 pt)
ROOM_PAD = 30             # pt вокруг номера помещения: сам номер слишком мелкий для подсветки

# Название места → слова, которые рядом со значением подтверждают, что это нужная строка
CONTEXT = [
    (r"стена\s+в\s+грунте", r"стен\w*\s+в\s+грунте|свг|буросек|захватк"),
    (r"фундаментн", r"фундаментн"),
    (r"плит|перекрыт", r"перекрыт|покрыт|горизонтальн"),     # без голого «плит»: «фундаментная плита» — не то
    (r"вертикальн", r"вертикальн|стен|пилон|колонн"),
    (r"стен", r"стен"),
    (r"пилон|колонн", r"пилон|колонн"),
    (r"форшахт", r"форшахт"),
    (r"арматур", r"арматур|рабоч"),
    (r"0\.000", r"0[,.]000|абсолютн"),
]
CODE_CONTEXT = {"KR-057": r"арматур|стерж", "KR-058": r"толщин|\bмм\b", "KR-059": r"толщин|\bмм\b",
                "KR-061": r"толщин|\bмм\b", "KR-060": r"сечени|пилон|колонн", "KR-055": r"бетон|класс"}

_docs = {}


def _page(file_id, pno):
    if file_id not in _docs:
        _docs[file_id] = pymupdf.open(file_path(file_id))
    return _docs[file_id][pno - 1]


def _visible(page, bbox, src):
    """Рамка в точках видимой (повёрнутой) страницы."""
    r = pymupdf.Rect(bbox)
    return r * page.rotation_matrix if src == "text" else r


def _norm(page, r):
    W, H = page.rect.width, page.rect.height
    clamp = lambda v: round(min(1.0, max(0.0, v)), 4)
    return [clamp(r.x0 / W), clamp(r.y0 / H), clamp(r.x1 / W), clamp(r.y1 / H)]


def text_box(file_id, pno, bbox, fragment):
    """Поля рамки для строки текстового слоя, которую трек уже нашёл сам (bbox — как отдаёт get_text, до поворота)."""
    page = _page(file_id, pno)
    return {"bbox_norm": _norm(page, _visible(page, bbox, "text")), "bbox_source": "text_layer",
            "fragment": " ".join(fragment.split())[:160]}


def _ocr_lines(tokens):
    """Токены OCR → строки: близкий центр по вертикали, без больших разрывов по горизонтали."""
    tokens = sorted(tokens, key=lambda t: ((t["bbox"][1] + t["bbox"][3]) / 2, t["bbox"][0]))
    rows = []
    for t in tokens:
        h = t["bbox"][3] - t["bbox"][1]
        yc = (t["bbox"][1] + t["bbox"][3]) / 2
        for row in rows:
            if abs(row["yc"] - yc) < 0.6 * max(h, row["h"]):
                row["items"].append(t)
                break
        else:
            rows.append({"yc": yc, "h": h, "items": [t]})
    out = []
    for row in rows:
        items = sorted(row["items"], key=lambda t: t["bbox"][0])
        chunk = [items[0]]
        for t in items[1:]:
            if t["bbox"][0] - chunk[-1]["bbox"][2] > 3 * row["h"]:     # другая колонка
                out.append(chunk)
                chunk = [t]
            else:
                chunk.append(t)
        out.append(chunk)
    return [(" ".join(t["text"] for t in c),
             [min(t["bbox"][0] for t in c), min(t["bbox"][1] for t in c),
              max(t["bbox"][2] for t in c), max(t["bbox"][3] for t in c)]) for c in out]


def page_lines(file_id, pno):
    """[(текст, рамка в точках видимой страницы, источник)] — текстовый слой плюс строки OCR из кэша."""
    page = _page(file_id, pno)
    lines = [(t, _visible(page, b, "text"), "text_layer") for t, b in text_lines(page)]
    if has_ocr(file_id, pno):
        ocr = [t for t in read_page(file_id, pno, ocr=True)["tokens"] if t.get("src") != "text"]
        lines += [(t, pymupdf.Rect(b), "ocr") for t, b in _ocr_lines(ocr)]
    return page, lines


def _num(v):
    """«7.5» → «7[,.]5», «30» → «30(?![,.]\\d)»."""
    whole, _, frac = v.partition(".")
    return rf"{whole}[,.]{frac}" if frac else rf"{whole}(?![,.]\d)"


def value_patterns(code, value):
    """Регулярки значения из поля ответа."""
    if not value:
        return []
    value = str(value)
    if code == "KR-055":
        return [rf"(?<![\wА-Яа-я])[ВB]\s?{_num(v)}(?!\d)" for v in re.findall(r"B(\d+(?:\.\d)?)", value)]
    if code == "KR-057":
        return [rf"(?<![\wА-Яа-я])[АA]\s?{n}(?!\d)" for n in re.findall(r"[АA](\d{3})", value)]
    if code in ("KR-058", "KR-059", "KR-061"):
        return [rf"(?<![\d.,]){n}(?![\d])" for n in re.findall(r"\d+", value)]
    if code == "KR-060":
        pats = [rf"(?<!\d){a}\s*[хxХX×]\s*{b}(?!\d)" for a, b in re.findall(r"(\d+)×(\d+)", value)]
        return pats + [rf"[DdДд]\s?{n}(?!\d)" for n in re.findall(r"D(\d+)", value)]
    if code == "PZ-009":
        return [rf"(?<![\d]){w}[,.]{f}" for w, f in re.findall(r"(\d+)\.(\d+)", value)] or \
               [rf"(?<![\d.,]){v}(?![\d])" for v in re.findall(r"\d+", value)]
    if code == "PZ-022":
        return [r"огнестойк", r"степен\w*\s+огнестойк"]
    if code == "PZ-023":
        return [r"конструктивн", r"пожарн\w*\s+опасност"]
    return []


_OCR_DIGITS = str.maketrans("ЗзОоOo", "330000")


def _ocr_fix(text):
    """Цифры в марках, которые OCR сканов читает буквами: «В3О» → «В30», «ВЗ5» → «В35» (только для поиска)."""
    return re.sub(r"(?<![\wА-Яа-я])([ВBАA]\s?)([\dЗзОоOo]{2,4})",
                  lambda m: m.group(1) + m.group(2).translate(_OCR_DIGITS) if re.search(r"\d", m.group(2)) else m.group(0),
                  text)


def _compile(parts):
    return re.compile("|".join(parts), re.I) if parts else None


def locate_value(file_id, pno, code, location, value):
    """(рамка в долях страницы, источник, фрагмент) или None.

    Порядок выбора строки: название конструкции в той же строке → в соседних → общие слова параметра («толщина»,
    «арматура») → больше совпавших значений → выше на странице. Иначе «Лестницы …, толщина 200мм» обгоняла строку
    про стены."""
    pats = [re.compile(p, re.I) for p in value_patterns(code, value)]
    if not pats:
        return None
    page, lines = page_lines(file_id, pno)
    # только первое подходящее правило: «Фундаментная плита» — это «фундаментн», а не «плит» (плиты перекрытий)
    element = next((re.compile(ctx, re.I) for key, ctx in CONTEXT if re.search(key, location or "", re.I)), None)
    general = _compile([CODE_CONTEXT[code]] if code in CODE_CONTEXT else [])
    el_lines = [r for t, r, _ in lines if element and element.search(t)]
    hit_lines = [(t, r, s, sum(1 for p in pats if p.search(_ocr_fix(t) if s == "ocr" else t))) for t, r, s in lines]
    hit_lines = [x for x in hit_lines if x[3]]
    best = None
    for text, rect, src, hits in hit_lines:
        near = [r for r in el_lines if r != rect and _near(rect, r)]
        key = (bool(element and element.search(text)), bool(near), bool(general and general.search(text)),
               hits, -rect.y0)
        if best is None or key > best[0]:
            best = (key, text, rect, src, near)
    if best is None:
        return None
    key, text, rect, src, near = best
    box = pymupdf.Rect(rect)
    # фраза, разорванная переносом («СО конструктивной» / «пожарной опасности»): соседние строки со значением
    for t, r, _, _ in hit_lines:
        if r != rect and _gap(rect, r) < 1.5 * _line_height(rect):
            box |= r
            text = t + " " + text if r.y0 < rect.y0 else text + " " + t
    if not key[0] and near:                        # название конструкции — в соседней строке: обводим и её
        box |= min(near, key=lambda r: _gap(rect, r))
    return _norm(page, box), src, " ".join(text.split())[:200]


def _near(a, b):
    """Соседние строки: та же строка таблицы (название слева, значение справа, «фундаментная плита … B40;») или
    не дальше NEAR_LINES высот строки."""
    overlap = min(a.y1, b.y1) - max(a.y0, b.y0)
    if overlap > 0.5 * min(a.height, b.height):
        return max(0, max(a.x0, b.x0) - min(a.x1, b.x1)) < SAME_ROW_GAP
    return _gap(a, b) < NEAR_LINES * _line_height(a)


def _line_height(rect):
    """Высота строки поперёк текста: у вертикальных строк (повёрнутые листы) это ширина рамки."""
    return max(min(rect.height, rect.width), 6)


def _gap(a, b):
    """Расстояние между рамками (0, если пересекаются)."""
    dx = max(0, max(a.x0, b.x0) - min(a.x1, b.x1))
    dy = max(0, max(a.y0, b.y0) - min(a.y1, b.y1))
    return (dx * dx + dy * dy) ** 0.5


def locate_room(file_id, pno, room):
    """Номер помещения на плане (не в экспликации) → рамка с запасом ROOM_PAD."""
    from ml.rooms import room_labels
    page = _page(file_id, pno)
    data = read_page(file_id, pno, ocr=has_ocr(file_id, pno))
    want = room.lstrip("0")
    for t in room_labels(data):
        if t["room"].lstrip("0") == want:
            r = _visible(page, t["bbox"], "text" if t.get("src") == "text" else "ocr")
            box = pymupdf.Rect(r.x0 - ROOM_PAD, r.y0 - ROOM_PAD, r.x1 + ROOM_PAD, r.y1 + ROOM_PAD)
            return _norm(page, box), "text_layer" if t.get("src") == "text" else "ocr", t["text"]
    return None


def annotate(submission):
    """Дописать рамки во все элементы evidence ответа. Возвращает (сколько найдено, сколько всего)."""
    found = total = 0
    for check in submission["checks"]:
        code, location = check["parameter_code"], check.get("location")
        for ev in check.get("evidence", []):
            total += 1
            if ev.get("bbox_norm"):                 # трек уже знает рамку (экспликации: строка таблицы)
                found += 1
                continue
            try:
                if location_type(code) == ROOM:
                    hit = locate_room(ev["file_id"], ev["pdf_page_number"], location)
                else:
                    value = check.get(STAGE_VALUE[ev["stage"]]) or " ".join(
                        str(check.get(f) or "") for f in STAGE_VALUE.values())
                    hit = locate_value(ev["file_id"], ev["pdf_page_number"], code, location, value)
            except Exception as e:                  # рамка — украшение: её ошибка не должна ронять ответ
                print(f"  рамка {code} {ev['file_id']}:{ev['pdf_page_number']}: {e}", file=sys.stderr)
                hit = None
            if hit:
                found += 1
                ev["bbox_norm"], ev["bbox_source"], ev["fragment"] = hit
            else:
                ev["bbox_norm"] = None
    return found, total


def render_check(submission, out_dir, dpi=60):
    """Картинки страниц-доказательств с нарисованной рамкой — для проверки глазами."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for check in submission["checks"]:
        for ev in check.get("evidence", []):
            if not ev.get("bbox_norm"):
                continue
            page = _page(ev["file_id"], ev["pdf_page_number"])
            pix = page.get_pixmap(dpi=dpi)
            x0, y0, x1, y1 = ev["bbox_norm"]
            X0, Y0, X1, Y1 = int(x0 * pix.width), int(y0 * pix.height), int(x1 * pix.width), int(y1 * pix.height)
            for x in range(max(0, X0 - 2), min(pix.width, X1 + 3)):
                for y in (Y0 - 2, Y0 - 1, Y1 + 1, Y1 + 2):
                    if 0 <= y < pix.height:
                        pix.set_pixel(x, y, (255, 0, 0))
            for y in range(max(0, Y0 - 2), min(pix.height, Y1 + 3)):
                for x in (X0 - 2, X0 - 1, X1 + 1, X1 + 2):
                    if 0 <= x < pix.width:
                        pix.set_pixel(x, y, (255, 0, 0))
            name = f"{check['parameter_code']}_{check['location']}_{ev['stage']}_{ev['file_id']}_{ev['pdf_page_number']}"
            pix.save(out_dir / (re.sub(r"[^\w.-]+", "_", name) + ".png"))


def main(argv):
    path = Path(argv[0])
    submission = paths.read_json(path)
    found, total = annotate(submission)
    paths.write_json(path, submission)
    print(f"{path}: рамки найдены для {found} из {total} ссылок-доказательств")
    for check in submission["checks"]:
        for ev in check.get("evidence", []):
            frag = (ev.get("fragment") or "—")[:90]
            print(f"  {check['parameter_code']:9} {check['location'][:28]:28} {ev['stage']} {ev['file_id']}:"
                  f"{ev['pdf_page_number']:<4} {ev.get('bbox_norm')}  «{frag}»")
    if "--png" in argv:
        render_check(submission, argv[argv.index("--png") + 1])


if __name__ == "__main__":
    main(sys.argv[1:])
