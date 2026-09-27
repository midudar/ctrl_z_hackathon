# -*- coding: utf-8 -*-
"""Материалы для презентации ML-части: слайды 16:9 в PDF + каждый слайд и каждая картинка отдельным PNG.

    python -m ml.slides_pdf

→ Инспектор_ИИ_материалы_для_презентации.pdf (корень проекта) и handover/slides/*.png (вставлять в свою презентацию).
Цифры вписаны руками по итогам прогонов 24–26.09 (как в status_pdf.py): после новых прогонов править здесь.
Рамки доказательств и вырезки страниц берутся из текущих ответов out/submission_*.json; вырезки для слайда про
экспликации — с объектов пилота, их готовит `--pilot-crops` (см. PILOT_SHOTS), слайд выводится, если они есть.
"""
import io
import json
import sys
from pathlib import Path

import pymupdf
from PIL import Image, ImageDraw

from ml import paths
from ml.pages import file_path

OUT_PDF = paths.ROOT / "Инспектор_ИИ_материалы_для_презентации.pdf"
OUT_DIR = paths.ROOT / "handover" / "slides"
FONT_A, FONT_B = paths.font_file(), paths.font_file(bold=True)

W, H = 960, 540
M = 48
INK = (0.07, 0.07, 0.08)
MUTED = (0.36, 0.36, 0.34)
ACCENT = (0.165, 0.47, 0.84)          # #2a78d6 — единственный акцентный цвет
TILE = (0.955, 0.962, 0.975)
RULE = (0.84, 0.85, 0.87)
RED = (220, 38, 38)                   # рамка доказательства на вырезках


def submission(obj):
    return json.load(open(paths.OUT / f"submission_{obj}.json", encoding="utf-8"))


OBJECTS = ("OBJ-TYUMENSKAYA-5-GOLD-SEED", "OBJ-NOVOSLOBODSKAYA", "OBJ-RECHNIKOV-7-7")


def boxes():
    """(ссылок-доказательств с рамкой, всего ссылок) по трём ответам."""
    ev = [e for o in OBJECTS for c in submission(o)["checks"] for e in c["evidence"]]
    return sum(bool(e.get("bbox_norm")) for e in ev), len(ev)


def find(obj, code, location):
    return next(c for c in submission(obj)["checks"] if c["parameter_code"] == code and c["location"] == location)


def crop(file_id, pno, bbox, name, margin=(0.06, 0.05), dpi=150, max_w=1400):
    """Вырезка страницы вокруг рамки доказательства с красной рамкой → PNG. bbox — доли видимой страницы."""
    page = pymupdf.open(file_path(file_id))[pno - 1]
    pix = page.get_pixmap(dpi=dpi)
    im = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
    W_, H_ = im.size
    x0, y0, x1, y1 = bbox
    d = ImageDraw.Draw(im)
    pad = 4
    d.rectangle([x0 * W_ - pad, y0 * H_ - pad, x1 * W_ + pad, y1 * H_ + pad], outline=RED, width=4)
    mx, my = margin
    box = (max(0, int((x0 - mx) * W_)), max(0, int((y0 - my) * H_)),
           min(W_, int((x1 + mx) * W_)), min(H_, int((y1 + my) * H_)))
    im = im.crop(box)
    if im.width > max_w:
        im = im.resize((max_w, round(im.height * max_w / im.width)))
    path = OUT_DIR / f"{name}.png"
    im.save(path)
    return path


# Вырезки для слайда про экспликации — с объектов пилота организаторов. Запускать отдельно, с переменными пилота:
#   INSPECTOR_DOCS=pilot/docs INSPECTOR_DATA=pilot/data INSPECTOR_OUT=pilot/out python -m ml.slides_pdf --pilot-crops
# (имя, файл, страница, номера помещений — строки таблицы экспликации, которые обвести)
PILOT_SHOTS = [("pilot_sosh_pd", "SOSH25-003562", 49, ("1.108", "1.110")),
               ("pilot_sosh_rd", "SOSH25-003642", 34, ("1.108", "1.109", "1.110")),
               ("pilot_alt_pd", "ALT79B-000015", 19, ("21", "23")),
               ("pilot_alt_rd", "ALT79B-000077", 4, ("21", "22"))]


def pilot_crops():
    from ml import explication
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for name, fid, pno, nums in PILOT_SHOTS:
        rows = [r for t in explication.file_tables(fid) if t["page"] == pno for r in t["rows"] if r["num"] in nums]
        rows = [r for r in rows if r["num"] != nums[0] or r is rows[0]]          # первая таблица этажа
        page = pymupdf.open(file_path(fid))[pno - 1]
        rect = page.rect
        box = pymupdf.Rect(min(r["bbox"][0] for r in rows), min(r["bbox"][1] for r in rows),
                           max(r["bbox"][2] for r in rows), max(r["bbox"][3] for r in rows[:len(nums)]))
        # рамка — по линиям сетки таблицы: границы строки в кэше шире самой таблицы (до соседней таблицы)
        segs = [(a, b) for y, a, b in explication._hlines(page, box) if a >= box.x0 - 5 and b <= box.x1 + 5]
        if segs:
            box.x0, box.x1 = min(a for a, _ in segs), max(b for _, b in segs)
        print(name, crop(fid, pno, (box.x0 / rect.width, box.y0 / rect.height, box.x1 / rect.width,
                                    box.y1 / rect.height), name, margin=(0.004, 0.012), dpi=200))


def ocr_chart():
    """Точность OCR трёх движков: одна мера (строго, после постобработки пайплайна), выделен выбранный
    (PaddleOCR), порог ТЗ — подписанная линия. Цифры — `python -m ml.ocr_eval --bench`."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    engines = ["EasyOCR\n(было)", "Tesseract 5", "PaddleOCR v5\n(выбран)"]
    acc = [0.823, 0.931, 0.950]           # строго, с исправлением латинских двойников (pages.fix_homoglyphs)
    raw = [0.801, 0.929, 0.947]           # строго, как выдал движок
    colors = ["#b9b8b2", "#b9b8b2", "#2a78d6"]
    fig, ax = plt.subplots(figsize=(8, 3.6), dpi=200)
    ax.barh(engines, acc, color=colors, height=0.56)
    # подписи значений — одной колонкой правее линии порога, чтобы линия их не перечёркивала
    for i, (a, s) in enumerate(zip(acc, raw)):
        ax.text(0.968, i, f"{a:.3f}".replace(".", ","), va="center", fontsize=11, fontweight="bold", color="#0b0b0b")
        ax.text(1.003, i, "без постобр. " + f"{s:.3f}".replace(".", ","), va="center", fontsize=8.5,
                color="#52514e")
    ax.axvline(0.95, color="#52514e", linestyle=(0, (4, 3)), linewidth=1.2)
    ax.text(0.947, 2.5, "порог ТЗ 0,95", ha="right", va="bottom", fontsize=9, color="#52514e")
    ax.set_xlim(0.70, 1.07)
    ax.set_xticks([0.70, 0.75, 0.80, 0.85, 0.90, 0.95])
    ax.set_ylim(-0.6, 2.75)
    ax.set_xlabel("точность по символам, строго (1 − CER), 600 строк пилота организаторов", fontsize=9,
                  color="#52514e")
    ax.tick_params(axis="y", labelsize=10, colors="#0b0b0b", length=0)
    ax.tick_params(axis="x", labelsize=8.5, colors="#52514e")
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color("#c9c8c2")
    ax.grid(axis="x", color="#e6e5e0", linewidth=0.8)
    ax.set_axisbelow(True)
    fig.tight_layout()
    path = OUT_DIR / "chart_ocr.png"
    fig.savefig(path, facecolor="white")
    plt.close(fig)
    return path


class Slides:
    def __init__(self):
        self.doc = pymupdf.open()
        self.n = 0

    def new(self, title, subtitle=None):
        self.page = self.doc.new_page(width=W, height=H)
        self.page.insert_font(fontname="A", fontfile=FONT_A)
        self.page.insert_font(fontname="B", fontfile=FONT_B)
        self.n += 1
        self.text((M, 30, W - M, 70), title, 22, bold=True)
        if subtitle:
            self.text((M, 66, W - M, 90), subtitle, 11.5, color=MUTED)
        self.page.draw_line((M, 94), (W - M, 94), color=RULE, width=0.8)
        self.page.insert_text((W - M - 150, H - 18), f"Инспектор ИИ · ML · {self.n}", fontname="A", fontsize=8,
                              color=MUTED)
        return self.page

    def text(self, rect, s, size=11, bold=False, color=INK, align=0, lead=1.3):
        """Текст в прямоугольнике; не влез — шрифт уменьшается."""
        r = pymupdf.Rect(rect)
        while size > 6:
            rc = self.page.insert_textbox(r, s, fontname="B" if bold else "A", fontsize=size, color=color,
                                          align=align, lineheight=lead)
            if rc >= 0:
                return
            size -= 0.5
        raise ValueError(f"текст не влез: {s[:40]}")

    def tile(self, rect, big, caption, big_size=28):
        r = pymupdf.Rect(rect)
        self.page.draw_rect(r, color=None, fill=TILE, radius=0.06)
        self.text((r.x0 + 14, r.y0 + 10, r.x1 - 10, r.y0 + 52), big, big_size, bold=True, color=ACCENT)
        self.text((r.x0 + 14, r.y0 + 56, r.x1 - 12, r.y1 - 8), caption, 13, lead=1.25)

    def image(self, rect, path, caption=None, frame=True):
        r = pymupdf.Rect(rect)
        img_r = pymupdf.Rect(r.x0, r.y0, r.x1, r.y1 - (26 if caption else 0))
        self.page.insert_image(img_r, filename=str(path), keep_proportion=True)
        if frame:
            with Image.open(path) as im:
                iw, ih = im.size
            s = min(img_r.width / iw, img_r.height / ih)
            fw, fh = iw * s, ih * s
            fx = img_r.x0 + (img_r.width - fw) / 2
            fy = img_r.y0 + (img_r.height - fh) / 2
            self.page.draw_rect((fx, fy, fx + fw, fy + fh), color=RULE, width=0.6)
        if caption:
            self.text((r.x0, r.y1 - 24, r.x1, r.y1), caption, 9.5, color=MUTED, align=1)

    def flow(self, steps, y, h=150, gap=16, body=11.5):
        """Шаги слева направо: заголовок + пояснение, стрелки между ними."""
        n = len(steps)
        w = (W - 2 * M - gap * (n - 1)) / n
        body_size = body
        for i, (head, body) in enumerate(steps):
            x = M + i * (w + gap)
            r = pymupdf.Rect(x, y, x + w, y + h)
            self.page.draw_rect(r, color=None, fill=TILE, radius=0.05)
            self.text((x + 10, y + 10, x + w - 8, y + 46), head, 13, bold=True, color=ACCENT)
            self.text((x + 10, y + 46, x + w - 8, y + h - 6), body, body_size, lead=1.25)
            if i < n - 1:
                ax = x + w + 2
                self.page.draw_line((ax, y + h / 2), (ax + gap - 4, y + h / 2), color=MUTED, width=1.2)
                self.page.draw_polyline([(ax + gap - 9, y + h / 2 - 4), (ax + gap - 4, y + h / 2),
                                         (ax + gap - 9, y + h / 2 + 4)], color=MUTED, width=1.2)

    def snapshot(self, name):
        pix = self.page.get_pixmap(dpi=200)
        pix.save(OUT_DIR / f"slide_{self.n:02d}_{name}.png")

    def save(self):
        self.doc.save(OUT_PDF, garbage=3, deflate=True)


def build():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    s = Slides()

    # 1. Итоги в цифрах
    s.new("ML-часть «Инспектора ИИ»: итоги в цифрах",
          "Сверка ПД → РД → ИД по матрице 132 параметров. Система готовит кандидатов с доказательствами, решает инспектор")
    boxed, total = boxes()
    tiles = [
        ("P 1,00 · F1 0,89", "на эталоне организаторов (15 проверок, 2 обучающих объекта); ложных срабатываний — 0"),
        ("5 из 5", "страниц-доказательств совпали с эталоном организаторов (Новослободская)"),
        ("0,950", "точность OCR сканов по символам, строго (PaddleOCR + исправление латинских букв-двойников) — "
                  "было 0,801 (EasyOCR); порог ТЗ 0,95"),
        ("132", "параметра в каждом ответе: метка, значения ПД / РД / ИД, файл и страница доказательства"),
        ("309 актов", "найдено и разделено в сшитых папках сканов ИД тестового объекта (8 324 страницы сканов)"),
        (f"{boxed} из {total}", "ссылок-доказательств с рамкой фрагмента на странице — с учётом поворота листа и "
                                "CropBox"),
    ]
    tw, th, gx, gy = (W - 2 * M - 2 * 18) / 3, 150, 18, 22
    for i, (big, cap) in enumerate(tiles):
        x = M + (i % 3) * (tw + gx)
        y = 124 + (i // 3) * (th + gy)
        s.tile((x, y, x + tw, y + th), big, cap)
    s.snapshot("itogi")

    # 2. Пайплайн
    s.new("Как работает: от папки документов к карточке для инспектора",
          "Этапы независимы и перезапускаются по отдельности; тяжёлый OCR выполняется один раз и кэшируется по SHA-256 файла")
    s.flow([
        ("1. Реестр и редакции", "Стадия, раздел и шифр каждого файла. Из корректировок тома берётся действующая: в ПД "
                                 "тестового объекта исключено 38 устаревших томов из 89."),
        ("2. Чтение страниц", "Текстовый слой, где он есть. Сканы ИД — два прохода: заголовок страницы → тип (акт, "
                              "реестр, сертификат…), затем полный OCR только нужных страниц (PaddleOCR, GPU)."),
        ("3. Извлечение", "КР: классы бетона и арматуры, толщины, сечения, отметка 0.000. ОВ: марки систем → "
                          "помещения по слоям CAD. ИД: поля актов АОСР. ПЗ: огнестойкость, класс пожарной опасности."),
        ("4. Сравнение", "Правило из матрицы: нарушение — только «понижение» / «уменьшение». Страховки: сомнение → "
                         "«сравнить нельзя», по сканам — не меньше двух актов."),
        ("5. Ответ", "Все 132 параметра: метка, значения ПД / РД / ИД, страницы и рамки фрагментов, пояснение, отчёт "
                     "о целостности комплекта (дубли, временные файлы, нет раздела в РД)."),
    ], y=122, h=250)
    s.text((M, 392, W - M, 500),
           "Модели не обучаются (размеченных нарушений — 10), решение о расхождении принимают правила матрицы. "
           "Готовые модели — для чтения: PaddleOCR (сканы актов), EasyOCR (подписи на чертежах, нарисованные линиями).",
           10.5, color=MUTED)
    s.snapshot("payplayn")

    # 3. Карточка нарушения
    c = find("OBJ-NOVOSLOBODSKAYA", "KR-057", "Форшахта")
    ev = {e["stage"]: e for e in c["evidence"]}
    pics = [crop(ev[st]["file_id"], ev[st]["pdf_page_number"], ev[st]["bbox_norm"], f"forshahta_{st}",
                 margin=(0.03, 0.035)) for st in ("PD", "RD", "ID")]
    s.new("Найденный кандидат: арматура форшахты ниже, чем в проекте",
          "Новослободская · KR-057 «Класс рабочей арматуры» · критическое · совпадает с нарушением в проверенных "
          "сравнениях организаторов")
    cols = [("ПД — рабочая арматура А400", f"ПД · {ev['PD']['file_id']}, стр. {ev['PD']['pdf_page_number']}"),
            ("РД — Ø12 А240 для форшахты", f"РД · {ev['RD']['file_id']}, стр. {ev['RD']['pdf_page_number']}"),
            ("ИД — применена А240С d12", f"Акт АОСР · {ev['ID']['file_id']}, стр. {ev['ID']['pdf_page_number']}")]
    # вырезки широкие и низкие — строками: слева что и откуда, справа сам фрагмент
    for i, ((head, cap), pic) in enumerate(zip(cols, pics)):
        y = 106 + i * 94
        s.text((M, y + 20, M + 210, y + 44), head, 12.5, bold=True)
        s.text((M, y + 46, M + 210, y + 66), cap, 10, color=MUTED)
        s.image((M + 225, y, W - M, y + 86), pic)
    s.text((M, 392, W - M, 500),
           "Проект требует рабочую арматуру класса А400, рабочая документация для форшахты задаёт А240, и акт "
           "освидетельствования подтверждает, что применена А240С. Система выдаёт это как кандидата: три документа, "
           "страницы и подсвеченные фрагменты. Подтверждает или отклоняет инспектор.",
           11, lead=1.35)
    s.snapshot("kartochka")

    # 4. OCR
    chart = ocr_chart()
    s.new("Распознавание сканов: выбрали движок замером, а не на глаз",
          "Пилот организаторов: 600 строк обучающих объектов с эталоном из текстового слоя PDF · формула 1 − CER")
    s.image((M, 104, 600, 400), chart, frame=False)
    s.text((620, 110, W - M, 400),
           "Строк без единой ошибки:\nEasyOCR — 21%\nTesseract — 57%\nPaddleOCR — 66%\n\n"
           "Проверка на деле — 20 актов Новослободской, отрисованных как сканы: PaddleOCR даёт те же конструкцию, "
           "классы бетона и арматуры и дату, что текстовый слой, — 20 из 20.\n\n"
           "Скорость на GPU: 1,8 с на страницу A4.", 11, lead=1.3)
    s.text((M, 416, W - M, 520),
           "«Строго» — латинская и кириллическая буква одинакового начертания (A и А) считаются разными. PaddleOCR "
           "сам даёт 0,947; пайплайн исправляет латинские буквы-двойники в русском тексте («AKT» → «АКТ», оси «2/B» → "
           "«2/В»), и строгая точность — 0,950 (без штрафа за двойники — 0,954). Главная трудность — курсивный шрифт "
           "чертежей («Лист» → «hucm» у EasyOCR).", 10, color=MUTED)
    s.snapshot("ocr")

    # 5. Сканы ИД
    s.new("Исполнительная документация тестового объекта — это 8 324 страницы сканов",
          "Акты сшиты в папки по ~300 страниц. Сплошной полный OCR — часы, поэтому два прохода")
    s.flow([
        ("8 324 страницы", "31 файл ИД, почти всё — сканы без текстового слоя."),
        ("Проход 1: заголовки", "Верхние 40% страницы, 120 dpi → тип: бланк акта, реестр, документ о качестве, "
                                "сертификат. Проверка на Новослободской: бланки актов 226 из 226."),
        ("910 страниц", "Полный OCR только бланков актов и реестров приложений (PaddleOCR, GPU, 1,8 с/стр)."),
        ("309 актов", "Сшитые папки разделены на акты по началу бланка (на Новослободской — 99 из 99)."),
        ("168 → конструкции", "Вид работ → конструкция (плиты перекрытий, вертикальные конструкции); классы бетона "
                              "и арматуры из актов сравниваются с РД."),
    ], y=122, h=220)
    s.text((M, 364, W - M, 500),
           "Защита от ошибок OCR: «понижение класса» по сканам ставится, только если его показывают не меньше двух "
           "актов; из марки смеси «В30 П4 F200 W10» берётся только первый класс (OCR читает W10 как «В10»); строки-"
           "перечни продукции завода («B7,5 … B60») пропускаются. Правила проверены на обучающем объекте.", 10.5,
           color=MUTED)
    s.snapshot("skany_id")

    # 6. Доказательность: рамки на трудных страницах
    s.new("Каждое доказательство — страница и рамка фрагмента",
          "Координаты в долях видимой страницы: интерфейс подсвечивает фрагмент на любом листе, в том числе повёрнутом")
    shots = []
    c = find("OBJ-NOVOSLOBODSKAYA", "KR-055", "Фундаментная плита")
    e = next(x for x in c["evidence"] if x["stage"] == "PD")
    shots.append((crop(e["file_id"], e["pdf_page_number"], e["bbox_norm"], "bbox_tablica", margin=(0.05, 0.05)),
                  "Строка таблицы записки ПД: «фундаментная плита … B40»"))
    c = find("OBJ-RECHNIKOV-7-7", "KR-055", "Фундаментная плита")
    e = next(x for x in c["evidence"] if x["stage"] == "RD")
    shots.append((crop(e["file_id"], e["pdf_page_number"], e["bbox_norm"], "bbox_povorot", margin=(0.06, 0.04)),
                  "Лист РД, повёрнутый на 90°: общие указания"))
    c = find("OBJ-RECHNIKOV-7-7", "KR-055", "Плиты перекрытий надземной части")
    e = next(x for x in c["evidence"] if x["stage"] == "ID")
    shots.append((crop(e["file_id"], e["pdf_page_number"], e["bbox_norm"], "bbox_skan", margin=(0.03, 0.04)),
                  "Скан акта (OCR): «Бетонная смесь В30 …»"))
    c = find("OBJ-TYUMENSKAYA-5-GOLD-SEED", "IOS4-078", "140")
    e = next(x for x in c["evidence"] if x["stage"] == "RD")
    shots.append((crop(e["file_id"], e["pdf_page_number"], e["bbox_norm"], "bbox_pomeshenie", margin=(0.05, 0.05)),
                  "План ОВ: помещение 140, пропала вытяжка"))
    sw = (W - 2 * M - 16) / 2
    for i, (pic, cap) in enumerate(shots):
        x = M + (i % 2) * (sw + 16)
        y = 104 + (i // 2) * 208
        s.image((x, y, x + sw, y + 198), pic, cap)
    s.snapshot("ramki")

    # 7. Метрики против порогов ТЗ
    s.new("Метрики против порогов ТЗ 14.3 — честно",
          "Обучающие объекты: 15 эталонных проверок организаторов (10 нарушений, 5 отрицательных) — маленькая выборка")
    rows = [("Precision", "≥ 0,90", "1,00", "проходим"), ("Доля ложных на отрицательных (FPR)", "≤ 0,10", "0,00", "проходим"),
            ("Recall", "≥ 0,80", "0,80", "проходим, на границе"), ("F1", "≥ 0,85", "0,89", "проходим"),
            ("OCR, точность по символам, строго", "≥ 0,95", "0,950 (без штрафа за двойники 0,954)",
             "проходим, на границе"),
            ("Точность ключевых полей", "≥ 0,90", "1,00 (эталон: 11 из 11; акты: 20 × 4 поля)", "проходим"),
            ("Локализация: файл и страница", "≥ 0,95", "0,80 (Новослободская — 5 из 5)", "не проходим"),
            ("Рамки фрагментов (bbox)", "выдавать", f"{boxed} из {total}", "есть")]
    y = 112
    xs = [M, M + 330, M + 470, M + 740]
    for j, h in enumerate(("Метрика", "Порог", "У нас", "")):
        s.text((xs[j], y, xs[j] + 260, y + 18), h, 10, bold=True, color=MUTED)
    y += 24
    for name, thr, ours, verdict in rows:
        s.page.draw_line((M, y - 4), (W - M, y - 4), color=RULE, width=0.6)
        s.text((xs[0], y, xs[1] - 10, y + 24), name, 11)
        s.text((xs[1], y, xs[2] - 10, y + 24), thr, 11, color=MUTED)
        s.text((xs[2], y, xs[3] - 10, y + 24), ours, 11, bold=True)
        s.text((xs[3], y, W - M, y + 24), verdict, 11,
               color=ACCENT if verdict.startswith("проходим") or verdict == "есть" else INK)
        y += 32
    s.text((M, y + 6, W - M, 520),
           "Recall и F1 подняты правилом чтения выносок по ГОСТ: подпись на полке относится к тому, на что указывает "
           "наклонная линия, а не к ближайшему помещению (найдено 147, ложного кандидата не появилось). Остались два "
           "пропуска на планах ОВ: 012 снимает страховка от ложных срабатываний, у 314 в РД одна ветка из двух — правило "
           "ловит только полную замену веток. Локализация считается по всем 10 нарушениям, поэтому без них не 0,95. "
           "Мы сознательно предпочитаем промолчать («сравнить нельзя»), чем выдать инспектору ложное нарушение.",
           10.5, color=MUTED)
    s.snapshot("metriki")

    # 8. Экспликации помещений — проверка на объектах пилотной разметки организаторов
    shots = {n: OUT_DIR / f"{n}.png" for n, *_ in PILOT_SHOTS}
    if all(p.exists() for p in shots.values()):
        s.new("Помещения и площади: сверка экспликаций ПД и РД",
              "Параметр PZ-003. У обучающих объектов РД по архитектуре нет — трек сделан и проверен на 8 объектах "
              "пилотной разметки организаторов")
        iw = (W - 2 * M - 190 - 16) / 2
        for i, (head, cap, pd, rd, h) in enumerate([
                ("СОШ, Полярная 25", "в РД появилось помещение 1.109 — 18,2 м²", "pilot_sosh_pd", "pilot_sosh_rd", 132),
                ("Алтуфьевское 79Б", "23 «Помещение» → 22 «Комната отдыха» (опечатка — в самом РД)",
                 "pilot_alt_pd", "pilot_alt_rd", 96)]):
            y = 100 if i == 0 else 246
            s.text((M, y + 8, M + 180, y + 30), head, 12.5, bold=True)
            s.text((M, y + 32, M + 180, y + 90), cap, 10, color=MUTED)
            s.image((M + 190, y, M + 190 + iw, y + h), shots[pd], "ПД")
            s.image((M + 190 + iw + 16, y, W - M, y + h), shots[rd], "РД")
        rows = [("Объект", "Отметили организаторы", "Нашли мы"),
                ("СОШ, Полярная 25", "новое помещение 1.109", "то же; обе их страницы — среди доказательств"),
                ("Алтуфьевское 79Б", "изменены назначения и площади", "3 смены назначения, площади этажей; все 4 страницы"),
                ("ДОО, Полярная 25", "изменён пищеблок", "крупнейшие изменения площадей — пищеблок; обе страницы"),
                ("Полярная 17 — контроль", "расхождений нет", "0 кандидатов, сверено 347 помещений")]
        y = 356
        xs = [M, M + 190, M + 420]
        for j, row in enumerate(rows):
            for k, cell in enumerate(row):
                s.text((xs[k], y, (xs[k + 1] - 10) if k < 2 else W - M, y + 20), cell, 10.5 if j else 9.5,
                       bold=(j == 0 or k == 2), color=MUTED if j == 0 else (ACCENT if k == 2 else INK))
            y += 24
        s.text((M, y + 4, W - M, 525),
               "Сравнивается последняя ПД с последней РД; перенумерация без смены назначения — справка, а не кандидат; "
               "разные этажи и секции не сопоставляются. Не находим перенос двери (Лосевская) — это геометрия плана, "
               "площади не менялись.", 9.5, color=MUTED)
        s.snapshot("eksplikacii")

    # 9. Ограничения и следующие шаги
    s.new("Что дальше", "Сильная сторона — точность и доказательность, слабая — охват параметров")
    cw = (W - 2 * M - 18) / 2
    blocks = [
        ("Сейчас",
         "• Извлечение написано для 19 параметров из 132: классы бетона и арматуры, толщины плит и стен, сечения "
         "колонн, сталь, отметка 0.000, огнестойкость здания и дверей, вентиляция по помещениям, помещения и "
         "площади, трубы канализации, кабели, освещение, мощность. Остальные в ответе честно помечены «не "
         "проверялось системой».\n"
         "• Для многих параметров значений нет в тексте РД (ТЭП, общая мощность) — нужны таблицы и чертежи.\n"
         "• OCR сканов выполнен заранее и лежит в кэше образа."),
        ("Следующие шаги",
         "• Таблицы ТЭП и спецификации (общая площадь, двери, окна) — там, где значений нет в тексте.\n"
         "• Семантические якоря (Sentence-BERT, многоязычный MiniLM): поиск абзаца под параметр по смыслу — быстрее "
         "добавлять параметры.\n"
         "• Спецификации АР (двери, окна); PaddleOCR на чертежах и второе мнение локальной VLM по кандидатам — на GPU стенда.\n"
         "• Семантический диссонанс из ТЗ: «Техническое» в ПД против «Склад ГСМ» в РД."),
    ]
    for i, (head, body) in enumerate(blocks):
        x = M + i * (cw + 18)
        s.page.draw_rect((x, 112, x + cw, 380), color=None, fill=TILE, radius=0.04)
        s.text((x + 16, 124, x + cw - 14, 150), head, 15, bold=True, color=ACCENT)
        s.text((x + 16, 158, x + cw - 14, 372), body, 12.5, lead=1.35)
    s.snapshot("dalshe")
    s.save()
    print(OUT_PDF, "слайдов", s.n, "| картинки:", OUT_DIR)


if __name__ == "__main__":
    pilot_crops() if sys.argv[1:2] == ["--pilot-crops"] else build()
