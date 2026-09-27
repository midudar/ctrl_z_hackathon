"""PDF с карточками кандидатов для проверки глазами: фрагменты листов ПД и РД с подсветкой.

    python -m ml.review_pdf OBJ-TYUMENSKAYA-5-GOLD-SEED IOS4-078:140 IOS4-078:002 IOS4-079:006 IOS4-078:339

Подсветка полупрозрачной заливкой (на чертежах свои красные и зелёные линии, рамки с ними сливаются):
синяя — номер помещения; жёлтая — система есть на обеих стадиях; красная — система есть только на этой стадии.
"""
import sys

import pymupdf

from ml import paths
from ml.marks import system_of
from ml.pages import file_path
from ml.rooms import page_room_systems, room_labels, read_page, has_ocr

FONT = paths.font_file()
FONT_BOLD = paths.font_file(bold=True)
BLUE, YELLOW, RED = (0.1, 0.35, 1.0), (1.0, 0.8, 0.0), (1.0, 0.0, 0.0)
PAD = 110           # pt вокруг помещения и его марок
MIN_W, MIN_H = 520, 360


def _union(boxes):
    return pymupdf.Rect(min(b[0] for b in boxes), min(b[1] for b in boxes),
                        max(b[2] for b in boxes), max(b[3] for b in boxes))


def crop_image(file_id, pno, room, own_systems, other_systems, max_w, max_h):
    """Фрагмент листа вокруг помещения с подсветкой. Возвращает (pixmap, число подсвеченных марок)."""
    trace = []
    page_room_systems(file_id, pno, trace)
    labels = [r for r in room_labels(read_page(file_id, pno, ocr=has_ocr(file_id, pno))) if r["room"] == room]
    marks = [t for t in trace if t["room"] == room]
    doc = pymupdf.open(file_path(file_id))
    page = doc[pno - 1]
    boxes = [r["bbox"] for r in labels] + [m["bbox"] for m in marks]
    if not boxes:
        return None, 0
    # если марки разбросаны далеко (схема на весь лист), держимся ближе к номеру помещения
    area = _union(boxes)
    if area.width > 1400 or area.height > 900:
        area = _union([r["bbox"] for r in labels] or boxes)
    area = pymupdf.Rect(area.x0 - PAD, area.y0 - PAD, area.x1 + PAD, area.y1 + PAD)
    if area.width < MIN_W:
        area.x0 -= (MIN_W - area.width) / 2; area.x1 = area.x0 + MIN_W
    if area.height < MIN_H:
        area.y0 -= (MIN_H - area.height) / 2; area.y1 = area.y0 + MIN_H
    area &= page.rect

    # подсветка рисуется на странице (документ открыт только в памяти, файл не меняется)
    for r in labels:
        page.draw_rect(pymupdf.Rect(r["bbox"]) + (-5, -5, 5, 5), color=BLUE, fill=BLUE, width=1.5, fill_opacity=0.3)
    for m in marks:
        systems = {system_of(x) for x in m["marks"]}
        color = YELLOW if systems <= other_systems else RED
        page.draw_rect(pymupdf.Rect(m["bbox"]) + (-3, -3, 3, 3), color=color, fill=color, width=1.2, fill_opacity=0.35)
    zoom = min(max_w / area.width, max_h / area.height, 3.0)
    return page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=area), len(marks)


def systems_of(value):
    """«В2: В2.7, В2.8; П2: П2» → {"В2", "П2"}."""
    if not value:
        return set()
    return {part.split(":")[0].strip() for part in value.split(";") if ":" in part}


def gold_keys():
    return {(g["object_id"], g["parameter_code"], g["location"]) for g in paths.read_jsonl(paths.GOLD)}


def card(out, object_id, check, is_gold):
    W, H = 842, 595
    page = out.new_page(width=W, height=H)
    page.insert_font(fontname="A", fontfile=FONT)
    page.insert_font(fontname="B", fontfile=FONT_BOLD)
    ev = {e["stage"]: e for e in check["evidence"]}
    pd_sys, rd_sys = systems_of(check["pd_value"]), systems_of(check["rd_value"])
    only_pd = sorted(s for s in pd_sys - rd_sys if s.startswith(("В", "П")))
    only_rd = sorted(rd_sys - pd_sys)

    status = "ЭТАЛОН: подтверждённое нарушение (образец)" if is_gold else "КАНДИДАТ ВНЕ РАЗМЕТКИ: нужна проверка"
    page.insert_text((30, 34), f"Помещение {check['location']}  ·  {check['parameter_code']}", fontname="B", fontsize=16)
    page.insert_text((30, 52), status, fontname="B", fontsize=10.5, color=(0.1, 0.5, 0.1) if is_gold else RED)
    lines = [
        f"ПД  (что обещали):  {check['pd_value']}",
        f"РД  (по чему строят):  {check['rd_value']}",
        f"Есть в ПД, нет в РД:  {', '.join(only_pd) or '—'}        Есть только в РД:  {', '.join(only_rd) or '—'}",
    ]
    y = 70
    for line in lines:
        page.insert_textbox(pymupdf.Rect(30, y, W - 30, y + 26), line, fontname="A", fontsize=9)
        y += 15 if len(line) < 150 else 26

    top, bottom = y + 8, H - 58
    col_w = (W - 70) / 2
    for i, (stage, own, other) in enumerate((("PD", pd_sys, rd_sys), ("RD", rd_sys, pd_sys))):
        e = ev[stage]
        x0 = 30 + i * (col_w + 10)
        page.insert_text((x0, top + 10), f"{'ПД' if stage == 'PD' else 'РД'}:  {e['file_id']}, стр. {e['pdf_page_number']}",
                         fontname="B", fontsize=10)
        pix, n = crop_image(e["file_id"], e["pdf_page_number"], check["location"], own, other,
                            col_w * 2.2, (bottom - top - 20) * 2.2)
        box = pymupdf.Rect(x0, top + 16, x0 + col_w, bottom)
        if pix is None:
            page.insert_textbox(box, "номер помещения на этой странице не найден", fontname="A", fontsize=10)
        else:
            page.insert_image(box, pixmap=pix, keep_proportion=True)
        page.draw_rect(box, color=(0.75, 0.75, 0.75), width=0.5)

    legend = [(BLUE, "номер помещения"), (YELLOW, "система есть и в ПД, и в РД"),
              (RED, "система есть только на этой стадии — предполагаемое расхождение")]
    x = 30
    for color, text in legend:
        page.draw_rect(pymupdf.Rect(x, H - 47, x + 12, H - 38), color=color, fill=color, width=1, fill_opacity=0.4)
        page.insert_text((x + 16, H - 39), text, fontname="A", fontsize=8)
        x += 30 + len(text) * 4.3
    page.insert_text((30, H - 18), "Решение:   ☐ нарушение     ☐ нарушения нет     ☐ система ошиблась с привязкой     ☐ неясно",
                     fontname="A", fontsize=10)


def intro(out, object_id, n_cand):
    page = out.new_page(width=842, height=595)
    page.insert_font(fontname="A", fontfile=FONT)
    page.insert_font(fontname="B", fontfile=FONT_BOLD)
    page.insert_text((40, 60), "Инспектор ИИ — кандидаты на проверку глазами", fontname="B", fontsize=20)
    text = (
        f"Объект: {object_id} (Тюменская-5, школа). Сравнение вентиляции ПД (том ОВ 5.4.2) и РД (ОВ1, ОВ2.1) по помещениям.\n\n"
        "Зачем это нужно. На этом объекте организаторы разметили 10 нарушений, и система находит 8 из них без ошибок. "
        f"Но кроме них она выдала ещё {n_cand} кандидата, которых в разметке нет. Разметка неполная, поэтому "
        "неизвестно, это реальные нарушения или ошибки системы. Только проверка глазами показывает, сколько у нас "
        "ложных срабатываний (порог ТЗ: не больше 10%).\n\n"
        "Как читать карточку. Сверху — какие системы вентиляции система нашла у помещения в ПД и в РД. "
        "Ниже — фрагменты листов: слева ПД, справа РД. Синяя заливка — номер помещения; жёлтая и красная — "
        "марки систем, которые система отнесла к этому помещению. Жёлтые есть на обеих стадиях, красные — только "
        "на одной: это и есть предполагаемое расхождение.\n\n"
        "Что проверить. (1) Правильно ли марки отнесены к помещению — не соседнее ли это помещение. "
        "(2) Действительно ли в РД у помещения нет системы, которая есть в ПД. (3) Если расхождение есть — "
        "похоже ли оно на нарушение (как у образца на следующей странице) или это переименование / перенос системы.\n\n"
        "Первая карточка — эталонное нарушение (помещение 140) как образец того, как выглядит настоящее расхождение."
    )
    page.insert_textbox(pymupdf.Rect(40, 85, 800, 560), text, fontname="A", fontsize=11.5, lineheight=1.35)


def main(argv):
    object_id, wanted = argv[0], [a.split(":") for a in argv[1:]]
    sub = paths.read_json(paths.OUT / f"submission_{object_id}.json")
    by_key = {(c["parameter_code"], c["location"]): c for c in sub["checks"]}
    gold = gold_keys()
    out = pymupdf.open()
    n_cand = sum((object_id, code, loc) not in gold for code, loc in wanted)
    intro(out, object_id, n_cand)
    for code, loc in wanted:
        check = by_key.get((code, loc))
        if not check:
            print(f"нет в ответе: {code} {loc}")
            continue
        card(out, object_id, check, (object_id, code, loc) in gold)
    target = paths.ROOT / "Инспектор_ИИ_кандидаты_на_проверку.pdf"
    out.save(target, garbage=3, deflate=True)
    print(f"{target}: {out.page_count} стр.")


if __name__ == "__main__":
    main(sys.argv[1:])
