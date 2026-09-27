"""Трек КР: класс прочности бетона (KR-055) и толщины монолитных конструкций (KR-058).

Значения лежат в тексте общих данных и записок, а не на чертежах:
- ПД: «фундаментная плита        B40;», «пилоны, колонны, стены -3 по +1     В60;»
- РД: «Класс бетона … для стен В60», в спецификации «B60, F150, W8» рядом с «Ж/б стены на отм.-13.750»

Поэтому ищем класс, берём вокруг него окно из соседних строк и в окне опознаём конструкцию.

    python -m ml.concrete OBJ-NOVOSLOBODSKAYA
"""
import re
import sys
from collections import defaultdict

import pymupdf

from ml import paths
from ml import registry
from ml.pages import file_path

def kr_files(object_id, stage):
    """Файлы конструкций (КР/КЖ/СВГ/РС) — выбирает реестр по разделу и шифру."""
    return registry.files(object_id, stage, "KR")


def zero_level_files(object_id, stage):
    """Отметку 0.000 в ПД пишут в пояснительной записке, в РД — в общих данных КР."""
    return registry.files(object_id, stage, "PZ") + kr_files(object_id, stage)

# Класс прочности: В25, B40, В7,5. Перед буквой не должно быть другой буквы или цифры (иначе «АВ40»).
CLASS_RE = re.compile(r"(?<![A-Za-zА-Яа-я0-9])[ВB]\s?(\d{1,2}(?:[,.]\d)?)(?![\d.,]*\s*(?:мм|м\b))")
# Отметка или прямое указание на подземную часть — признак «ниже нуля»
UNDERGROUND_RE = re.compile(r"подземн|нулевого\s+цикла|отм\.?\s*-|-1[0-9][.,]\d|\s-\d\s|\s-\dго|фундамент", re.I)

# Конструкции: как называется в ответе → как встречается в документах → где: "under" — только с признаком
# подземной части (в окне или в названии комплекта РД), "above" — только без него, None — где угодно.
VERTICAL_RE = re.compile(r"пилон\w*|колонн\w*|стен\w*|вертикальн\w*\s+конструкц\w*"
                         r"|конструкц\w*\s+(нулевого\s+цикла|подземн\w*)", re.I)
# «Горизонтальные конструкции выполняются из бетона класса В30» — так в общих указаниях комплектов плит РД
SLAB_RE = re.compile(r"плит\w*\s+(перекрыт\w*|покрыти\w*)|перекрыти[йяюе]\w*|горизонтальн\w*\s+конструкц\w*", re.I)
ELEMENTS = [
    ("Стена в грунте", re.compile(r"стен\w*\s+в\s+грунте|«стены\s+в\s+грунте»", re.I), None),
    ("Фундаментная плита", re.compile(r"фундамент[^.;|]{0,40}плит\w*|фунд\.?\s*плит\w*|плит\w*[^.;|]{0,20}фундамент", re.I), None),
    # 25.09: надземные конструкции — у Речникова 281 акт ИД про плиты и вертикальные конструкции этажей 3–12.
    # Плиты, как и вертикальные, делятся на подземные и надземные: плиты паркинга и плиты этажей — разные
    # комплекты РД с разными классами, сравнивать акт этажа с РД паркинга нельзя.
    ("Плиты перекрытий подземной части", SLAB_RE, "under"),
    ("Плиты перекрытий надземной части", SLAB_RE, "above"),
    ("Вертикальные конструкции подземной части", VERTICAL_RE, "under"),
    ("Вертикальные конструкции надземной части", VERTICAL_RE, "above"),
]
# Стандартный ряд классов бетона по прочности: «В1», «В2» в тексте РД — это оси и марки систем, а не бетон
STANDARD_CLASSES = {7.5, 10, 12.5, 15, 20, 22.5, 25, 27.5, 30, 35, 40, 45, 50, 55, 60, 70, 80, 90, 100}
# Классы арматуры по ГОСТ 34028: OCR сканов даёт «А270С», «А506» — такого не бывает
STANDARD_REBAR = {240, 300, 400, 500, 600, 800, 1000}
# Название комплекта РД говорит, подземная ли это часть: «Вертикальные конструкции -3 этажа», «…-1 этажа. Парковка».
# Название объекта («Жилой дом с подземной автостоянкой») стоит на каждом титуле — его вырезаем.
# Минус — отдельный знак отрицательного этажа («-1 этажа»), а не диапазон («2-12 этажей»)
SET_UNDERGROUND_RE = re.compile(r"(?<![\d\w])-\s?\d+\s*(?:-?го\s+)?этаж|подземн\w*\s+(?:этаж|част)|парковк|паркинг"
                                r"|котлован", re.I)
OBJECT_NAME_RE = re.compile(r"с\s+подземн\w+\s+(?:автостоянк|паркинг|парковк)\w*", re.I)

# Конструкции для толщин (KR-058 плита, KR-059 перекрытия, KR-061 стены)
THICK_ELEMENTS = [
    ("Стена в грунте", ELEMENTS[0][1], False),
    ("Фундаментная плита", ELEMENTS[1][1], False),
    ("Плиты перекрытий", re.compile(r"перекрыт\w*|покрыт\w*", re.I), False),
    ("Монолитные стены", re.compile(r"(ж/б\s+)?(монолитн\w+\s+)?стен\w*", re.I), False),
]

# Класс арматуры: А500С (кириллица) и A500C (латиница) — одно и то же
REBAR_RE = re.compile(r"(?<![A-Za-zА-Яа-я0-9])[АA]\s?(\d{3})\s?([СCсc])?(?![\d])")
REBAR_CONTEXT_RE = re.compile(r"арматур|стержн", re.I)

# KR-057 по конструкциям. В ПД Новослободской: «форшахта – балка … / вязанными арматурными каркасами, основная
# рабочая арматура А400, / конструктивная А240», в РД: «Продольные стержни арматуры ∅12 А240, использованные для
# армирования форшахты» — организаторы считают это понижением рабочей арматуры (А400 → А240), подтверждённым актами.
# Сравнивать минимумы по всему объекту нельзя: конструктивная А240 из ПД «прячет» такое понижение.
REBAR_ELEMENTS = [
    ("Форшахта", re.compile(r"форшахт", re.I)),
    ("Стена в грунте", ELEMENTS[0][1]),
    ("Фундаментная плита", ELEMENTS[1][1]),
    ("Плиты перекрытий", re.compile(r"перекрыт\w*|покрыти\w*", re.I)),
    ("Колонны и пилоны", re.compile(r"колонн\w*|пилон\w*", re.I)),
]
WORKING_RE = re.compile(r"рабоч|основн|продольн", re.I)
CONSTRUCTIVE_RE = re.compile(r"конструктивн|хомут|поперечн|распредел|монтажн|шпильк|петл|закладн", re.I)
REBAR_LOOKBACK = 3      # строк абзаца выше, где в записке названа конструкция
PARAGRAPH_GAP = 26      # pt между соседними строками одного абзаца (межстрочный ~21 pt)

# Толщина: «толщиной 1200 и 1500 мм», «Толщина фундаментной плиты -1000, 1200мм»
THICKNESS_RE = re.compile(r"толщин\w*", re.I)
A4_MAX_WIDTH = 700      # pt: до этой ширины лист считаем текстовой запиской, а не чертежом
# Заголовок списка толщин: «Толщины перекрытия и покрытия:» — дальше идут строки «конструкция — 250мм»
THICK_HEADER_RE = re.compile(r"толщин\w*[^:]{0,60}:\s*$", re.I)
# числа после слова «толщина» до конца предложения: «1000мм и 1200мм», «1200 и 1500 мм», «-1000, 1200мм»
THICKNESS_VALUES_RE = re.compile(r"(\d{2,4}(?:\s*(?:,|и|/)\s*\d{2,4})*)\s*мм", re.I)

# Контексты, где класс бетона относится не к несущей конструкции
SKIP_RE = re.compile(r"подготовк|стяжк|отмостк|бетон\w*\s+пол|щебень|пустышк|форшахт|сва[ий]|буросек|под\s+оборудовани"
                     r"|зазор|блок|скц|утеплител|выравнивающ|кладк|штукатур|мембран|гидроизол", re.I)
# «Все конструкции нулевого цикла кроме фундаментной плиты …» — про плиту здесь как раз не говорится
EXCEPT_RE = re.compile(r"кроме\s*$", re.I)


_lines_cache = {}


def page_lines(page):
    """Строки страницы: (текст, bbox). Порядок — как в PDF.

    Кэш по (файл, страница): экстракторов пять (классы, толщины, арматура, сечения, спецификации), и на тяжёлых
    векторных листах get_text("dict") — самое дорогое место прогона."""
    key = (page.parent.name, page.number)
    if key not in _lines_cache:
        out = []
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                text = " ".join(s["text"] for s in line["spans"]).strip()
                if text:
                    out.append((text, line["bbox"]))
        _lines_cache[key] = out
    return _lines_cache[key]


def page_rows(page, y_tol=3.5):
    """Строки, собранные в ряды: куски с одинаковым y склеиваются слева направо.

    В записках ПД название конструкции и её значение — это два отдельных куска одного ряда:
    «Перекрытия -2 и -1 этажей» на x≈135 и «- 250мм» на x≈503.
    """
    rows = defaultdict(list)
    for text, bbox in page_lines(page):
        rows[round(bbox[1] / y_tol)].append((bbox[0], text, bbox))
    out = []
    for key in sorted(rows):
        parts = sorted(rows[key])
        text = "  ".join(p[1] for p in parts)
        bbox = [min(p[2][0] for p in parts), min(p[2][1] for p in parts),
                max(p[2][2] for p in parts), max(p[2][3] for p in parts)]
        out.append((text, bbox))
    return out


def _vertical(bbox):
    """Строка идёт вертикально (лист повёрнут): узкая и высокая рамка. На повёрнутых листах РД Речникова
    соседняя строка сдвинута по x, а не по y (F0398 стр. 3: «…абсолютной» / «отметке в пределах: 122,65»)."""
    return (bbox[3] - bbox[1]) > 2 * (bbox[2] - bbox[0])


def _across(bbox):
    """Координата поперёк строки: y для обычной, x для вертикальной."""
    return bbox[0] if _vertical(bbox) else bbox[1]


def with_continuation(rows, i, max_gap=20):
    """Ряд плюс начало следующего, если предложение не закончено: «…толщиной 800» / «мм, 900 мм»."""
    text, bbox = rows[i]
    if text.rstrip().endswith((".", ":", ";")) or i + 1 >= len(rows):
        return text
    nxt, nbox = rows[i + 1]
    gap = nbox[0] - bbox[2] if _vertical(bbox) else nbox[1] - bbox[3]
    if gap > max_gap:
        return text
    return text + " " + nxt


def context_of(lines, i, radius=1, max_gap=30):
    """Окно вокруг строки i: сама строка плюс соседние, если они рядом поперёк строки."""
    text, bbox = lines[i]
    parts = [text]
    for j in range(max(0, i - radius), min(len(lines), i + radius + 1)):
        if j == i:
            continue
        other, obox = lines[j]
        if _vertical(obox) == _vertical(bbox) and abs(_across(obox) - _across(bbox)) <= max_gap:
            parts.append(other)
    return " | ".join(parts)


_manifest = {}


def underground_set(file_id):
    """Комплект РД про подземную часть — по названию комплекта (строка после «…ДОКУМЕНТАЦИЯ» на титуле)
    и по папкам/имени файла. Весь текст первой страницы не годится: на листе без титула в легенде схемы
    встречается «Паркинг» (F0375 Речникова — плиты этажей 2–12)."""
    if not _manifest:
        _manifest.update({r["file_id"]: r for r in paths.read_jsonl(paths.MANIFEST)})
    if _manifest[file_id]["stage"] not in ("RD", "RD_ID_MIXED"):
        return False                    # том ПД описывает всё здание сразу
    return bool(SET_UNDERGROUND_RE.search(set_name(file_id)))


def placed(where, ctx, set_under):
    """Подходит ли место конструкции ("under" / "above" / None) к окну текста."""
    if where is None:
        return True
    under = set_under or bool(UNDERGROUND_RE.search(ctx))
    return under if where == "under" else not under and not re.search(r"грунт", ctx, re.I)


# Спецификация материалов конструкции: заголовок «Спецификация материалов на ж/б фундаментную плиту…»
# и строка марки бетона «B40,F150,W6». На большом листе они далеко друг от друга, окно строк их не свяжет, а
# эталон (KR-055 «Фундаментная плита» Новослободской) берёт доказательством именно эту страницу.
SPEC_HEAD_RE = re.compile(r"спецификаци\w*\s+(?:материал\w*|бетон\w*)[^\n]{0,80}", re.I)
SPEC_MARK_RE = re.compile(r"(?<![А-Яа-яA-Za-z0-9])[ВB]\s?(\d{1,2}(?:[,.]5)?)\s*,\s*F\s?\(?I*\)?\s?\d{2,3}\s*,\s*W\s?\d{1,2}")


def spec_records(file_id, pno, page, set_under):
    """Классы из спецификации материалов страницы, отнесённые к конструкции из её заголовка."""
    text = page.get_text()
    heads = [m.group(0) for m in SPEC_HEAD_RE.finditer(text)]
    names = []
    for head in heads:
        for name, rx, where in ELEMENTS:
            if rx.search(head) and placed(where, head, set_under):
                names.append(name)
                break
    if len(set(names)) != 1:            # нет заголовка с конструкцией или на листе спецификации разных конструкций
        return []
    # spec — сколько раз на листе заголовок спецификации: настоящая спецификация повторяет его («начало»,
    # «продолжение», «окончание»), а в общих данных он один раз стоит в ведомости листов
    return [{"element": names[0], "value": float(m.group(1).replace(",", ".")), "file_id": file_id, "page": pno,
             "context": f"{heads[0][:80]} | {m.group(0)}", "spec": len(heads)}
            for m in SPEC_MARK_RE.finditer(text) if float(m.group(1).replace(",", ".")) in STANDARD_CLASSES]


def extract_classes(file_id):
    """Записи {элемент, класс, файл, страница, контекст} со всех страниц документа."""
    doc = pymupdf.open(file_path(file_id))
    set_under = underground_set(file_id)
    records = []
    for pno, page in enumerate(doc, start=1):
        records += spec_records(file_id, pno, page, set_under)
        lines = page_lines(page)
        for i, (text, _) in enumerate(lines):
            for m in CLASS_RE.finditer(text):
                value = float(m.group(1).replace(",", "."))
                if value not in STANDARD_CLASSES:
                    continue
                ctx = context_of(lines, i)
                # «мусорные» слова ищем в основном слева от значения: справа в строке может идти
                # уже другая конструкция («…«стена в грунте» из бетона B25; «сваи-пустышки» …»)
                prefix = (lines[i - 1][0][-50:] + " " if i else "") + text[:m.start()]
                if SKIP_RE.search(prefix[-90:] + text[m.start():m.start() + 15]):
                    continue
                for name, rx, where in ELEMENTS:
                    hit = rx.search(ctx)
                    if not hit or EXCEPT_RE.search(ctx[:hit.start()][-10:]):
                        continue
                    if not placed(where, ctx, set_under):
                        continue
                    records.append({"element": name, "value": value, "file_id": file_id,
                                    "page": pno, "context": ctx[:200]})
                    break          # конструкции упорядочены от частного к общему
    return records


def extract_thickness(file_id, elements=None):
    """Толщины монолитных конструкций: {элемент, значения (мм), файл, страница, контекст}."""
    elements = elements or THICK_ELEMENTS
    doc = pymupdf.open(file_path(file_id))
    records = []
    for pno, page in enumerate(doc, start=1):
        # Склейка в ряды нужна только на A4-записках, где «конструкция … значение» — две колонки.
        # На больших листах в одну горизонталь попадает текст из разных блоков, и склейка всё портит.
        lines = page_rows(page) if page.rect.width <= A4_MAX_WIDTH else page_lines(page)
        header_rows_left = 0
        for i, _ in enumerate(lines):
            text = with_continuation(lines, i)
            starts = [m.start() for m in THICKNESS_RE.finditer(text)]
            if THICK_HEADER_RE.search(lines[i][0]):
                header_rows_left = 6          # столько строк списка считаем продолжением заголовка
            elif header_rows_left and not starts and "мм" in text:
                starts = [0]                  # строка списка: «Перекрытия -2 и -1 этажей   -  250мм»
                header_rows_left -= 1
            elif header_rows_left:
                header_rows_left -= 1
            for at in starts:
                prefix = (lines[i - 1][0][-50:] + " " if i else "") + text[:at]
                if SKIP_RE.search(prefix[-140:] + text[at:at + 25]):
                    continue
                tail = re.split(r"[.;|]", text[at:at + 90])[0]
                if "мм" not in tail:
                    continue
                nums = [n for g in THICKNESS_VALUES_RE.findall(tail[:70]) for n in re.findall(r"\d{2,4}", g)]
                values = sorted({int(v) for v in nums if 50 <= int(v) <= 3000})
                if not values:
                    continue
                ctx = context_of(lines, i)
                # конструкция, названная в самой строке, важнее названной у соседей:
                # «- Перекрытия -2 и -1 этажей  -  250мм» стоит рядом со строкой про фундаментную плиту
                for where in (text, ctx):
                    matched = False
                    for name, rx, needs_underground in elements:
                        hit = rx.search(where)
                        if not hit or EXCEPT_RE.search(where[:hit.start()][-10:]):
                            continue
                        if needs_underground and not UNDERGROUND_RE.search(where):
                            continue
                        records.append({"element": name, "values": values, "file_id": file_id,
                                        "page": pno, "context": ctx[:200]})
                        matched = True
                        break
                    if matched:
                        break
    return records


# Подпись толщины на опалубочном плане или разрезе: «Железобетонная фундаментная плита h=1200 мм»
THICK_LABEL_RE = re.compile(r"\bh\s*=\s*(\d{2,4})\s*мм", re.I)


def thickness_drawings(file_id, elements=None):
    """Листы чертежей, где толщина конструкции подписана «… h=1200 мм». По матрице источник РД для KR-058/059/061 —
    опалубочные чертежи и разрезы, и эталон Новослободской для KR-058 даёт вторую страницу РД именно такую (`F0140`
    стр. 7). Только для выбора страницы-доказательства: значения отсюда в сравнение не идут."""
    records = []
    for pno, page in enumerate(pymupdf.open(file_path(file_id)), start=1):
        for text, _ in page_lines(page):
            m = THICK_LABEL_RE.search(text)
            if not m or SKIP_RE.search(text[:m.start()]):
                continue
            for name, rx, _ in elements or THICK_ELEMENTS:
                if rx.search(text[:m.start()]):
                    records.append({"element": name, "values": [int(m.group(1))], "file_id": file_id,
                                    "page": pno, "context": text[:200]})
                    break
    return records


def add_drawing_page(check, element, rd_rows, drawings):
    """К страницам РД с текстом добавить лист чертежа с той же толщиной (своего комплекта, того же файла, раньше)."""
    rd_ev = [e for e in check["evidence"] if e["stage"] == "RD"]
    if not rd_rows or not rd_ev or len(rd_ev) >= MAX_EVIDENCE_PAGES:
        return
    shown = {(e["file_id"], e["pdf_page_number"]) for e in rd_ev}
    values = {v for r in rd_rows for v in r["values"]}
    cands = [r for r in drawings.get(element, []) if set(r["values"]) <= values and (r["file_id"], r["page"]) not in shown]
    if not cands:
        return
    key_rx = next((rx for name, rx in SET_KEYS if element.startswith(name)), None)
    best = min(cands, key=lambda r: (0 if key_rx and key_rx.search(set_name(r["file_id"])) else 1,
                                     r["file_id"] != rd_ev[0]["file_id"], r["page"], r["file_id"]))
    check["evidence"].append({"stage": "RD", "file_id": best["file_id"], "pdf_page_number": best["page"]})


def rebar_role(text, start, end):
    """Роль класса по его части фразы между запятыми: «основная рабочая арматура А400, конструктивная А240»."""
    clause = re.split(r"[,;]", text[:start])[-1] + text[start:end] + re.split(r"[,;]", text[end:])[0]
    if CONSTRUCTIVE_RE.search(clause):
        return "constructive"
    if WORKING_RE.search(clause):
        return "working"
    return None


def rebar_construction(lines, i, text_page):
    """Конструкция, к которой относится арматура: в той же строке, а в записке — ещё в абзаце выше."""
    for name, rx in REBAR_ELEMENTS:
        if rx.search(lines[i][0]):
            return name
    if not text_page:
        return None                 # на чертеже соседние строки из разных блоков
    for j in range(i - 1, max(-1, i - 1 - REBAR_LOOKBACK), -1):
        if lines[j + 1][1][1] - lines[j][1][3] > PARAGRAPH_GAP:
            break
        for name, rx in REBAR_ELEMENTS:
            if rx.search(lines[j][0]):
                return name
    return None


# KR-060: сечения пилонов и колонн. В записке ПД: «Пилоны сечением 2100 х 300, 1800 х 300», «колонны с сечением:
# D500, 400х400, 400х500»; в спецификации РД: «Пилон монолитный 200х1200 h=3050, шт.».
SECTION_WORD_RE = re.compile(r"пилон\w*|колонн\w*", re.I)
SECTION_TEXT_RE = re.compile(r"сечени\w*", re.I)
SECTION_SPEC_RE = re.compile(r"(?:пилон|колонн)\w*\s+монолитн\w*\s+(\d{3,4})\s*[хxХX×]\s*(\d{3,4})", re.I)
# Запятая после размера — это перечень («550х1200, 600х1300»), а не дробь: запрещаем только «,цифра»
DIM_RE = re.compile(r"(?<!\d)(?<!\d[.,])(\d{3,4})\s*[хxХX×]\s*(\d{3,4})(?!\d)(?![.,]\d)"
                    r"|(?<![A-Za-zА-Яа-я])[DdД]\s?(\d{3,4})\b")
SECTION_SKIP_RE = re.compile(r"балк|ригел|проем|проём|отверст|капител|фундамент", re.I)
SECTION_ELEMENTS = ("Пилоны и колонны подземной части", "Пилоны и колонны надземной части")


def _areas(text):
    """[(подпись, площадь мм²)]: «2100 х 300» → 630000, «D500» — круглая колонна."""
    out = []
    for a, b, d in DIM_RE.findall(text):
        if d:
            out.append((f"D{d}", round(3.14159 * int(d) ** 2 / 4)))
        elif 100 <= int(a) <= 3000 and 100 <= int(b) <= 3000:
            x, y = sorted((int(a), int(b)))
            out.append((f"{x}×{y}", x * y))
    return out


def extract_sections(file_id):
    """Сечения пилонов и колонн: {элемент (подземн./надземн.), сечения [(подпись, мм²)], файл, страница, контекст}."""
    doc = pymupdf.open(file_path(file_id))
    set_under = underground_set(file_id)
    records = []
    for pno, page in enumerate(doc, start=1):
        text_page = page.rect.width <= A4_MAX_WIDTH
        lines = page_rows(page) if text_page else page_lines(page)
        for i, (text, _) in enumerate(lines):
            spec = SECTION_SPEC_RE.findall(text)
            if spec:
                found = _areas(" ".join(f"{a}х{b}" for a, b in spec))
            elif SECTION_WORD_RE.search(text) and SECTION_TEXT_RE.search(text) and not SECTION_SKIP_RE.search(text):
                # «…колонны с сечением 550х1200, 600х1300,» / «600х1500, 1000х1500.» — перечень переносится,
                # на листах РД тоже: «…пилоны сечением» / «500х1700мм, 500х1500мм» (F0141 Новослободской)
                found = _areas(with_continuation(lines, i))
            else:
                continue
            if not found:
                continue
            ctx = context_of(lines, i)
            where = "under" if (set_under or UNDERGROUND_RE.search(ctx)) else "above"
            records.append({"element": SECTION_ELEMENTS[0] if where == "under" else SECTION_ELEMENTS[1],
                            "sections": found, "values": sorted({a for _, a in found}),
                            "file_id": file_id, "page": pno, "context": ctx[:200]})
    return records


def section_checks(pd_records, rd_records):
    """KR-060. Нарушение не ставим: тип пилона между стадиями по тексту не сопоставить (в РД может быть
    отдельный мелкий тип, которого нет в перечне ПД). Минимальное сечение РД не меньше ПД — «нет нарушения»,
    меньше — «сравнить нельзя» с пояснением для инспектора."""
    checks = []
    labels = lambda rows: "/".join(sorted({l for r in rows for l, _ in r["sections"]},
                                          key=lambda s: (len(s), s)))[:200]
    for name in SECTION_ELEMENTS:
        pd = [r for r in pd_records if r["element"] == name]
        rd = [r for r in rd_records if r["element"] == name]
        if not pd or not rd:
            continue
        pd_min = min(min(r["values"]) for r in pd)
        rd_min_rec = min(rd, key=lambda r: min(r["values"]))
        rd_min = min(rd_min_rec["values"])
        check = {"parameter_code": "KR-060", "location": name, "pd_value": labels(pd), "rd_value": labels(rd),
                 "id_value": None,
                 "violation_label": "NO_VIOLATION" if rd_min >= pd_min else "COMPARISON_IMPOSSIBLE",
                 "evidence": [{"stage": "PD", "file_id": min(pd, key=lambda r: r["page"])["file_id"],
                               "pdf_page_number": min(pd, key=lambda r: r["page"])["page"]},
                              {"stage": "RD", "file_id": rd_min_rec["file_id"], "pdf_page_number": rd_min_rec["page"]}]}
        if rd_min < pd_min:
            small = min((s for r in rd for s in r["sections"]), key=lambda s: s[1])[0]
            big = min((s for r in pd for s in r["sections"]), key=lambda s: s[1])[0]
            check["note"] = (f"в РД есть сечение {small}, меньше наименьшего по ПД ({big}): уменьшение сечения "
                             f"или отдельный тип, которого нет в перечне ПД — проверить инспектору")
        checks.append(check)
    return checks


def extract_rebar(file_id):
    """Класс арматуры: А500С / A500C → число 500 и нормализованная запись, плюс конструкция и роль.

    element — «Рабочая арматура» для сравнения по объекту; construction и role — для сравнения по конструкциям.
    """
    doc = pymupdf.open(file_path(file_id))
    records = []
    for pno, page in enumerate(doc, start=1):
        lines = page_lines(page)
        text_page = page.rect.width <= A4_MAX_WIDTH
        for i, (text, _) in enumerate(lines):
            # «… арматура А400, / конструктивная А240» — слово «арматура» строкой выше
            prev = lines[i - 1][0] if text_page and i else ""
            same_line = bool(REBAR_CONTEXT_RE.search(text))
            if not (same_line or REBAR_CONTEXT_RE.search(prev) and REBAR_RE.search(text)):
                continue
            for m in REBAR_RE.finditer(text):
                value = int(m.group(1))
                if value not in STANDARD_REBAR:
                    continue
                label = "А" + m.group(1) + ("С" if m.group(2) else "")
                records.append({"element": "Рабочая арматура", "value": value, "label": label,
                                "construction": rebar_construction(lines, i, text_page),
                                "role": rebar_role(text, m.start(), m.end()), "same_line": same_line,
                                "file_id": file_id, "page": pno, "context": text[:200]})
    return records


def stage_classes(object_id, stage):
    records = []
    for file_id in kr_files(object_id, stage):
        records += extract_classes(file_id)
    return records


# PZ-009: «За относительную отметку 0,000 принята абсолютная отметка 159,95 м»
ZERO_LEVEL_RE = re.compile(r"относительн\w*\s+отметк\w*\s*[±]?0[.,]000[^.;]{0,80}?отметк\w*\s*(\d{1,4}[.,]\d{1,3})", re.I)
# Общие указания КЖ Речникова: «За относительную отм. 0,000 принята отметка чистого пола входной группы 1 этажа
# и нежилых встроенных помещений, что соответствует абсолютной / отметке в пределах: 122,65 в Балтийской системе»
ZERO_LEVEL_ABS_RE = re.compile(r"относительн\w*\s+(?:отметк\w*|отм\.?)\s*[±]?0[.,]000[^;]{0,200}?абсолютн\w*\s+"
                               r"(?:отметк\w*|отм\.?)[^0-9;]{0,20}?(\d{2,4}[.,]\d{1,3})", re.I)
ZERO_LEVEL_NAME = "Отметка 0.000"


def extract_zero_level(file_id):
    """Абсолютная отметка нуля здания."""
    doc = pymupdf.open(file_path(file_id))
    records = []
    for pno, page in enumerate(doc, start=1):
        lines = page_lines(page)
        for i, (text, _) in enumerate(lines):
            ctx = context_of(lines, i).replace(" | ", " ")
            m = (ZERO_LEVEL_RE.search(text) or ZERO_LEVEL_RE.search(ctx)
                 or ZERO_LEVEL_ABS_RE.search(text) or ZERO_LEVEL_ABS_RE.search(ctx))
            if m:
                records.append({"element": ZERO_LEVEL_NAME, "value": float(m.group(1).replace(",", ".")),
                                "file_id": file_id, "page": pno, "context": text[:200]})
                break        # одного упоминания на страницу достаточно
    return records


def _best(records):
    """Запись-доказательство: сначала та, где конструкция названа в той же строке, что и значение."""
    def key(r):
        first_line = r["context"].split(" | ")[0]
        named = any(rx.search(first_line) for _, rx, _ in ELEMENTS)
        return (0 if named else 1, r["page"])
    return sorted(records, key=key)[0]


# Комплект РД, посвящённый конструкции: «Вертикальные конструкции -3 этажа», «Конструкции фундаментной плиты».
# Эталон Новослободской берёт доказательство РД именно оттуда (KR-055 «Вертикальные…» — F0141 стр. 4, а не общие
# данные комплекта плиты), и со страницы спецификации материалов этой конструкции (плита — F0140 стр. 27).
SET_KEYS = [("Стена в грунте", re.compile(r"стен\w*\s+в\s+грунте|\bс[в]?г\b|ограждени\w*\s+котлован", re.I)),
            ("Фундаментная плита", re.compile(r"фундаментн\w*\s+плит", re.I)),
            ("Плиты перекрытий", re.compile(r"плит\w*\s+(перекрыт|покрыт)|горизонтальн", re.I)),
            ("Вертикальные конструкции", re.compile(r"вертикальн", re.I)),
            ("Монолитные стены", re.compile(r"вертикальн|стен(?!\w*\s+в\s+грунте)", re.I))]
MAX_EVIDENCE_PAGES = 3              # на стадию: по странице на каждое значение, которого ещё не показали
_set_names = {}


def set_name(file_id):
    """Название комплекта РД: строка после «…ДОКУМЕНТАЦИЯ» на титуле плюс папки и имя файла."""
    if file_id not in _set_names:
        from ml import revisions
        row = _manifest.get(file_id) or next(r for r in paths.read_jsonl(paths.MANIFEST) if r["file_id"] == file_id)
        m = re.search(r"ДОКУМЕНТАЦИЯ\s+(.{0,80})", re.sub(r"\s+", " ", revisions.title_page(file_id)), re.I)
        _set_names[file_id] = OBJECT_NAME_RE.sub(" ", (m.group(1) if m else "") + " " + row["relative_path"])
    return _set_names[file_id]


def evidence_rows(rows, element, stage):
    """Записи-доказательства стадии. РД: сначала комплект этой конструкции, потом спецификация материалов,
    потом упоминание в той же строке; ПД — как раньше (упоминание в строке, номер страницы). Если значений
    несколько (толщины 1200/1500), добавляется страница на каждое ещё не показанное — у KR-058 эталон даёт две."""
    key_rx = next((rx for name, rx in SET_KEYS if element.startswith(name)), None)

    def rank(r):
        named = any(rx.search(r["context"].split(" | ")[0]) for _, rx, _ in ELEMENTS)
        if stage != "RD":
            return (0 if named else 1, r["page"])
        own_set = bool(key_rx and key_rx.search(set_name(r["file_id"])))
        return (0 if own_set else 1, -r.get("spec", 0), 0 if named else 1, r["page"], r["file_id"])

    if stage != "RD":
        return [min(rows, key=rank)]            # в ПД эталон даёт одну страницу, даже когда значений два
    chosen, covered, seen = [], set(), set()
    for r in sorted(rows, key=rank):
        vals = set(r["values"]) if "values" in r else {r["value"]}
        if (r["file_id"], r["page"]) in seen or chosen and not vals - covered:
            continue
        chosen.append(r)
        covered |= vals
        seen.add((r["file_id"], r["page"]))
        if len(chosen) >= MAX_EVIDENCE_PAGES:
            break
    return chosen


def rebar_checks(pd_records, rd_records):
    """KR-057 по конструкциям: рабочая арматура из ПД против арматуры той же конструкции в РД.

    Нарушение — в РД у конструкции нет арматуры того класса, который ПД прямо называет рабочим
    («основная рабочая арматура А400», в РД только А240). Если роль в ПД не названа, нарушение не ставим:
    РД не слабее ПД — «нет нарушения», слабее — «сравнить нельзя». Конструктивная арматура не сравнивается.
    """
    checks = []
    labels = lambda rows: "/".join(sorted({r["label"] for r in rows}))
    for name, _ in REBAR_ELEMENTS:
        pd = [r for r in pd_records if r["construction"] == name and r["role"] != "constructive"]
        rd = [r for r in rd_records if r["construction"] == name and r["role"] != "constructive"]
        if not pd or not rd:
            continue
        work = [r for r in pd if r["role"] == "working"]
        rd_best = max(r["value"] for r in rd)
        if work:
            label = "VIOLATION_PRESENT" if rd_best < min(r["value"] for r in work) else "NO_VIOLATION"
            pd_txt = "рабочая " + labels(work)
        else:
            label = "NO_VIOLATION" if rd_best >= max(r["value"] for r in pd) else "COMPARISON_IMPOSSIBLE"
            pd_txt = labels(pd)
        pd_ev = min(work or pd, key=lambda r: (r["page"], r["file_id"]))
        rd_ev = min(rd, key=lambda r: (r["value"], r["role"] != "working", r["page"]))   # самое слабое место РД
        checks.append({"parameter_code": "KR-057", "location": name, "violation_label": label,
                       "pd_value": pd_txt, "rd_value": labels(rd), "id_value": None,
                       "evidence": [{"stage": "PD", "file_id": pd_ev["file_id"], "pdf_page_number": pd_ev["page"]},
                                    {"stage": "RD", "file_id": rd_ev["file_id"], "pdf_page_number": rd_ev["page"]}]})
    return checks


def compare(object_id):
    """Проверки KR-055 (класс бетона) и KR-058 (толщина фундаментной плиты)."""
    data = {}
    for stage in ("PD", "RD"):
        classes, thick, rebar = defaultdict(list), defaultdict(list), []
        for file_id in kr_files(object_id, stage):
            for r in extract_classes(file_id):
                classes[r["element"]].append(r)
            for r in extract_thickness(file_id):
                thick[r["element"]].append(r)
            rebar += extract_rebar(file_id)
        data[stage] = (classes, thick, rebar)

    checks = []

    def add(code, element, pd_rows, rd_rows, fmt, decreased):
        if not pd_rows or not rd_rows:
            label, pd_txt, rd_txt = "COMPARISON_IMPOSSIBLE", fmt(pd_rows), fmt(rd_rows)
        else:
            label = "VIOLATION_PRESENT" if decreased(pd_rows, rd_rows) else "NO_VIOLATION"
            pd_txt, rd_txt = fmt(pd_rows), fmt(rd_rows)
        evidence = []
        for stage, rows in (("PD", pd_rows), ("RD", rd_rows)):
            for b in evidence_rows(rows, element, stage) if rows else []:
                evidence.append({"stage": stage, "file_id": b["file_id"], "pdf_page_number": b["page"]})
        checks.append({"parameter_code": code, "location": element, "violation_label": label,
                       "pd_value": pd_txt, "rd_value": rd_txt, "id_value": None, "evidence": evidence})

    for element, _, _ in ELEMENTS:
        pd_rows, rd_rows = data["PD"][0].get(element, []), data["RD"][0].get(element, [])
        add("KR-055", element, pd_rows, rd_rows,
            fmt=lambda rows: "B" + "/B".join(f"{v:g}" for v in sorted({r["value"] for r in rows})) if rows else None,
            # нарушение — понижение класса: минимальный класс в РД ниже минимального в ПД
            decreased=lambda p, r: min(x["value"] for x in r) < min(x["value"] for x in p))

    zero = {stage: [r for f in zero_level_files(object_id, stage) for r in extract_zero_level(f)]
            for stage in ("PD", "RD")}
    add("PZ-009", ZERO_LEVEL_NAME, zero["PD"], zero["RD"],
        fmt=lambda rows: f"{min(r['value'] for r in rows):g}" if rows else None,
        # отметка нуля должна совпадать: любое расхождение — расхождение (в матрице «расхождение > 0»)
        decreased=lambda p, r: abs(min(x["value"] for x in p) - min(x["value"] for x in r)) > 0.001)

    # толщины: KR-058 фундаментная плита, KR-059 перекрытия, KR-061 несущие стены.
    # Нарушение — уменьшение минимальной толщины в РД.
    thin_fmt = lambda rows: "/".join(str(v) for v in sorted({v for r in rows for v in r["values"]})) + " мм" if rows else None
    thin_less = lambda p, r: min(v for x in r for v in x["values"]) < min(v for x in p for v in x["values"])
    drawings = defaultdict(list)                   # листы РД с подписью «… h=1200 мм» — вторая страница-доказательство
    for file_id in kr_files(object_id, "RD"):
        for r in thickness_drawings(file_id):
            drawings[r["element"]].append(r)
    for code, element in (("KR-058", "Фундаментная плита"), ("KR-059", "Плиты перекрытий"), ("KR-061", "Монолитные стены")):
        add(code, element, data["PD"][1].get(element, []), data["RD"][1].get(element, []),
            fmt=thin_fmt, decreased=thin_less)
        add_drawing_page(checks[-1], element, data["RD"][1].get(element, []), drawings)

    # KR-060: сечения пилонов и колонн (осторожно: без нарушений, см. section_checks)
    sections = {stage: [r for f in kr_files(object_id, stage) for r in extract_sections(f)] for stage in ("PD", "RD")}
    checks += section_checks(sections["PD"], sections["RD"])

    # KR-057: класс рабочей арматуры — по конструкциям, где она названа в ПД и в РД (rebar_checks).
    # Если ни одной такой конструкции нет, остаётся прежнее сравнение по объекту: понижение минимального класса.
    by_construction = rebar_checks(data["PD"][2], data["RD"][2])
    checks += by_construction
    if not by_construction:
        same_line = lambda rows: [r for r in rows if r["same_line"]]
        add("KR-057", "Рабочая арматура", same_line(data["PD"][2]), same_line(data["RD"][2]),
            fmt=lambda rows: "/".join(sorted({r["label"] for r in rows})) if rows else None,
            decreased=lambda p, r: min(x["value"] for x in r) < min(x["value"] for x in p))

    # ИД: классы бетона и арматуры из актов АОСР против РД (ml/acts.py)
    from ml import acts
    return acts.merge_into(checks, object_id, data["RD"][0], data["RD"][2])


def main(argv):
    object_id = argv[0] if argv else "OBJ-NOVOSLOBODSKAYA"
    if argv and argv[0] == "compare" or len(argv) > 1 and argv[1] == "compare":
        object_id = argv[0] if argv[0] != "compare" else "OBJ-NOVOSLOBODSKAYA"
        from ml import eval as ev
        from ml import submission as sub
        checks = compare(object_id)
        for c in checks:
            print(f"  {c['parameter_code']} «{c['location']}»: ПД {c['pd_value']} | РД {c['rd_value']} → {c['violation_label']}")
            print(f"      {[(e['stage'], e['file_id'], e['pdf_page_number']) for e in c['evidence']]}")
        result = sub.merge_checks(sub.skeleton(object_id), checks)
        out = paths.OUT / f"submission_{object_id}.json"
        paths.write_json(out, result)
        print(f"\n{out}: ошибок проверки {len(sub.validate(result))}\n")
        if object_id == paths.TEST_OBJECT:
            print("Тестовый объект: оценка по эталону не проводится, правила по нему не подбираем.")
            return
        ev.report(ev.evaluate([result], paths.read_jsonl(paths.GOLD)))
        return
    for stage in ("PD", "RD"):
        by_element = defaultdict(list)
        for r in stage_classes(object_id, stage):
            by_element[r["element"]].append(r)
        print(f"===== {stage}")
        for element, rows in sorted(by_element.items()):
            values = sorted({r["value"] for r in rows})
            print(f"  {element}: B{', B'.join(f'{v:g}' for v in values)}  ({len(rows)} упоминаний)")
            for r in rows[:4]:
                print(f"      {r['file_id']} стр.{r['page']}  B{r['value']:g}  {r['context'][:120]}")
        thick = defaultdict(list)
        for file_id in kr_files(object_id, stage):
            for r in extract_thickness(file_id):
                thick[r["element"]].append(r)
        for element, rows in sorted(thick.items()):
            values = sorted({v for r in rows for v in r["values"]})
            print(f"  толщина {element}: {values} мм")
            for r in rows[:3]:
                print(f"      {r['file_id']} стр.{r['page']}  {r['values']}  {r['context'][:110]}")


if __name__ == "__main__":
    main(sys.argv[1:])
