"""Трек ИД: акты освидетельствования скрытых работ (АОСР) → конструкция, класс бетона, класс арматуры.

Бланк АОСР типовой, нужные поля пронумерованы:
    1. К освидетельствованию предъявлены следующие работы: «Бетонирование захватки №8»
    2. Работы выполнены по проектной документации: «НВС-2025/03-КР1 - …», «17_ПД/25-СВГ - Стена в грунте»
    3. При выполнении работ применены: материалы или «Перечислено в реестре приложений №1»
Класс бетона чаще всего стоит в реестре приложений: «Документ о качестве бетонной смеси … БСТ В25 П4F(I)200W8».
Марка смеси переносится на следующую строку, поэтому класс ищется по коду смеси (`БСТ В25`), а не по слову
«бетон» в той же строке.

Сравнение РД ↔ ИД подмешивается в проверки трека КР (`merge_into`): нарушение — если в акте класс бетона
(арматуры) ниже минимального по РД для той же конструкции. В эталоне организаторов нет ни одной проверки по ИД,
поэтому правила консервативные: акт относится к конструкции только при явном признаке, а спорные работы
(сваи, форшахта, подготовка, технологическая дорога) пропускаются.

Текст страницы — текстовый слой, а если его нет, распознанный текст из кэша OCR (`ml.pages`). У Речникова ИД
почти целиком из сканов, поэтому без OCR трек по нему видит только редкие цифровые страницы.

Пока один файл считается одним актом (так у Новослободской). У Речникова акты сшиты в папки по 300 страниц:
делить файл на акты — следующий шаг.

    python -m ml.acts OBJ-NOVOSLOBODSKAYA
"""
import re
import sys
from collections import Counter, defaultdict

import pymupdf

from ml import page_kinds, paths
from ml.concrete import (CLASS_RE, ELEMENTS, SKIP_RE, SLAB_RE, STANDARD_CLASSES, STANDARD_REBAR, UNDERGROUND_RE,
                         VERTICAL_RE)
from ml.pages import cache_path, file_path, fix_homoglyphs, has_ocr

ACT_FIELD1_RE = re.compile(r"к\s+освидетельствованию\s+предъявлены", re.I)
MIN_TEXT = 80                     # символов: меньше — страница считается сканом без текста

# Код бетонной смеси по ГОСТ 7473: БСТ (тяжёлый), БСМ (мелкозернистый) + класс
MIX_RE = re.compile(r"\bБС[ТМ]\s*([ВB]\s?\d{1,2}(?:[,.]5)?)(?![\d])")
CONCRETE_WORD_RE = re.compile(r"бетон", re.I)
REBAR_WORD_RE = re.compile(r"арматур", re.I)
# Класс арматуры. Суффикс «С» только вплотную и не как начало слова: в «А240 Сертификат» С — от «Сертификат»
REBAR_RE = re.compile(r"(?<![A-Za-zА-Яа-я0-9])[АA]\s?(\d{3})([СCсc](?![А-Яа-яA-Za-z]))?(?!\d)")

# Работы, у которых свой класс бетона и которые к несущим конструкциям не относятся
ACT_SKIP_RE = re.compile(SKIP_RE.pattern + r"|дорог|обвязочн|шламов|демонтаж|скважин|трубопровод|водопониж", re.I)
# Проектная документация, по которой делают стену в грунте
WALL_DESIGN_RE = re.compile(r"стен\w*\s+в\s+грунте|свг\b|ограждающ\w*\s+конструкц\w*\s+котлован", re.I)
WALL_WORK_RE = re.compile(r"захватк", re.I)
# Форшахта — своя конструкция (KR-057: в ПД Новослободской рабочая А400, в РД и актах А240). Для класса бетона
# (KR-055) она не используется: её В15 не должен попадать в стену в грунте.
FORSHAHTA_RE = re.compile(r"форшахт", re.I)
NO_MATERIAL_WORK_RE = re.compile(r"демонтаж|разработк|срубк", re.I)
# Армированные буросекущие сваи по проекту стены в грунте — это и есть стена (B25, как у захваток); так их
# считают и организаторы: доказательство ИД у KR-055 «Стена в грунте» — акт №1А-БСС. Неармированные сваи
# (БСС-1н) — В15, их по-прежнему пропускаем.
REINFORCED_PILES_RE = re.compile(r"(?<!не)армированн\w*\s+сва", re.I)
ACT_DATE_RE = re.compile(r"от\s+(\d{2})\.(\d{2})\.(\d{4})")
MIN_OCR_ACTS = 2


def field(text, n):
    """Поле «n.» бланка: строки после заголовка до пояснения в скобках."""
    m = re.search(rf"(?m)^\s*{n}\.\s[^\n]*\n(.*?)\n\s*\(", text, re.S)
    return re.sub(r"\s+", " ", m.group(1)).strip() if m else None


# Бланк АОСР по полному тексту (слой или OCR целой страницы). Слова короче 4 букв сравнение не видит,
# поэтому «при», «по» в признаках нет. Первая страница — шапка «Объект капитального строительства» и поле 1,
# вторая — поля 2 и 3. OCR сканов Речникова: «Объскт капитального строптельства», «Прн выполненин работ применены».
FORM_TOP = ("объект", "капитального", "строительства")
FORM_FIELDS = {1: ("предъявлены", "следующие", "работы"),
               2: ("работы", "выполнены", "проектной"),
               3: ("выполнении", "работ", "применены")}
TOP_WORDS = 60                  # шапка — в первых словах страницы


def form_marks(text):
    w = page_kinds._words(text)
    return {"top": page_kinds._phrase(w[:TOP_WORDS], *FORM_TOP),
            "f1": page_kinds._phrase(w, *FORM_FIELDS[1]),
            "f23": page_kinds._phrase(w, *FORM_FIELDS[2]) or page_kinds._phrase(w, *FORM_FIELDS[3])}


def act_starts(pages):
    """Индексы страниц, с которых начинается акт: шапка бланка — или поле бланка сразу после страницы, где бланка
    нет (первая страница бланка не распознана, акт начинается со второй). Проверено на Новослободской: 99 из 99."""
    marks = [form_marks(t) for _, t, _ in pages]
    starts = []
    for i, m in enumerate(marks):
        prev_form = i > 0 and any(marks[i - 1].values())
        if m["top"] or ((m["f1"] or m["f23"]) and not prev_form):
            starts.append(i)
    return starts


def field_fuzzy(text, n, max_lines=3):
    """Поле n по OCR: значение после двоеточия в строке заголовка и следующие строки до пояснения «(…»,
    следующего номера поля или заголовка следующего поля."""
    lines = text.splitlines()
    nxt_head = FORM_FIELDS.get(n + 1)
    for i, line in enumerate(lines):
        if not page_kinds._phrase(page_kinds._words(line), *FORM_FIELDS[n]):
            continue
        parts = [line.split(":", 1)[1].strip()] if ":" in line else []
        for s in (l.strip() for l in lines[i + 1:i + 1 + max_lines]):
            if s.startswith("(") or re.match(r"\d\s*[.,]\s", s) or \
                    nxt_head and page_kinds._phrase(page_kinds._words(s), *nxt_head):
                break
            parts.append(s)
        # OCR склеивает в одну строку значение, пояснение «(наименование…)» (часто «{…») и следующее поле
        # Tesseract теряет скобку подписи: «… 1-2.8/А-1.Л Тиашменование скрытых работ)» — режем и по слову подписи
        value = re.split(r"[({]|\s[1-9]\s?[.,]\s+\S|\S*менован|\S*реквизит", " ".join(p for p in parts if p))[0]
        return value.strip(" .,;:") or None
    return None


# Заполненные поля бланков Речникова набраны курсивом, OCR читает их латиницей-двойником («Верmuкальные»,
# «Плumа»): как в треке ОВ (rooms._ITALIC2CYR). Только для текста из OCR — в текстовом слое латиница настоящая.
ITALIC2CYR = str.maketrans("abcehkmnoprtuxyg", "авсенктпоргтихуд")
# Класс в строке об арматуре по OCR: «Ярмamура 070 Я5ООС» — буква и цифры-двойники (Я→А, О→0, З→3)
OCR_REBAR_RE = re.compile(r"(?<![0-9A-Za-zА-Яа-я])[ЯАA]\s?([0-9ОOЗз]{3})([СCсc])?(?![0-9A-Za-zА-Яа-я])")
OCR_CLASS_RE = re.compile(r"(?<![0-9A-Za-zА-Яа-я])[ВB]\s?([0-9ОOЗз]{2})(?![0-9A-Za-zА-Яа-я])")
OCR_DIGITS = str.maketrans("ОOЗз", "0033")
# Марка смеси по OCR Tesseract: «BCT BISII4F(1)150W6» = «БСТ В15П4F(I)150W6» (латиница, 1 → I, 5 → S, П → II).
# Класс — две «цифры» сразу после В, дальше П / II / не буква.
OCR_MIX_RE = re.compile(r"\b[BБ][CС][TТMМ]\s*[ВB]\s?([0-9ОOЗзIlS]{2})(?=[ПIl|]|[^0-9A-Za-zА-Яа-я]|$)")
OCR_MIX_DIGITS = str.maketrans("ОOЗзIlS", "0033115")


def ocr_rebar_fix(text):
    """Строки об арматуре и бетоне по OCR: «Ярмaтура 010 Я5ООС» → «арматура: … А500С», «БСТ ВЗ0» → «бетон: … В30».

    Слово-признак определяется нечётко, и его точная форма ставится в начало строки — дальше строку разбирают
    те же _rebar и _concrete, что и для текстового слоя."""
    out = []
    for line in text.splitlines():
        words = page_kinds._words(line)
        if page_kinds._has(words, "арматура"):
            line = "арматура: " + OCR_REBAR_RE.sub(
                lambda m: "А" + m.group(1).translate(OCR_DIGITS) + ("С" if m.group(2) else ""), line)
        elif OCR_MIX_RE.search(line):
            line = "бетон: " + OCR_MIX_RE.sub(lambda m: "БСТ В" + m.group(1).translate(OCR_MIX_DIGITS) + " ", line)
        elif page_kinds._has(words, "бетонная") or page_kinds._has(words, "бетона") or " БСТ" in f" {line}":
            line = "бетон: " + OCR_CLASS_RE.sub(lambda m: "В" + m.group(1).translate(OCR_DIGITS), line)
        out.append(line)
    return "\n".join(out)


MONTHS = ("январ", "феврал", "март", "апрел", "ма", "июн", "июл", "август", "сентябр", "октябр", "ноябр", "декабр")
TEXT_DATE_RE = re.compile(r"(\d{1,2})\W{0,3}\s*(январ|феврал|март|апрел|ма[йя]|июн|июл|август|сентябр|октябр|ноябр|декабр)"
                          r"\w*\s+(20\d{2})", re.I)


def text_date(text):
    """(год, месяц, день) из «“17” апреля 2026 г.» на первой странице бланка, или None."""
    m = TEXT_DATE_RE.search(text)
    if not m:
        return None
    month = next(i for i, p in enumerate(MONTHS, 1) if m.group(2).lower().startswith(p))
    return (m.group(3), f"{month:02d}", f"{int(m.group(1)):02d}")


def _ocr_text(file_id, pno):
    """Текст страницы из кэша OCR. Tesseract кладёт свой текст в порядке чтения (`ocr_text`: блоки и абзацы, поля
    бланка не перемешаны с соседней колонкой) — берём его; для старого кэша EasyOCR — фрагменты по строкам сверху
    вниз, слева направо. Латинские двойники внутри русских слов переводятся в кириллицу (`pages.fix_homoglyphs`)."""
    data = paths.read_json(cache_path(file_id, pno, ocr=True))
    if data.get("ocr_text"):
        return fix_homoglyphs(data["ocr_text"])
    toks = data["tokens"]
    toks = [t for t in toks if t.get("src") == "ocr"]
    rows = defaultdict(list)
    for t in toks:
        rows[round(t["bbox"][1] / 8)].append(t)
    return fix_homoglyphs("\n".join(" ".join(t["text"] for t in sorted(r, key=lambda t: t["bbox"][0]))
                                    for _, r in sorted(rows.items())))


def file_pages(file_id):
    """[(номер страницы, текст, источник)] — текстовый слой, иначе OCR из кэша, иначе пусто."""
    out = []
    with pymupdf.open(file_path(file_id)) as doc:
        for pno, page in enumerate(doc, start=1):
            text = page.get_text()
            if len(text.strip()) >= MIN_TEXT:
                out.append((pno, text, "text"))
            elif has_ocr(file_id, pno):
                out.append((pno, _ocr_text(file_id, pno), "ocr"))
            else:
                out.append((pno, "", "none"))
    return out


def _windows(text):
    """Строки страницы вместе со следующей: марка смеси и класс часто разнесены по двум строкам."""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    for i, line in enumerate(lines):
        yield line, (line + " " + lines[i + 1]) if i + 1 < len(lines) else line


def _concrete(pages):
    """[(класс, страница)]: по коду смеси «БСТ В25» или по «бетон … В25» в строке и следующей за ней."""
    found = []
    for pno, text, _ in pages:
        for line, win in _windows(text):
            # Перечень продукции завода («БСТ B7.5 … B12.5 … B15 … B60» — декларация о соответствии): три и больше
            # разных классов в одной строке — это не бетон акта
            if len({m.group(1) for m in CLASS_RE.finditer(line)}) >= 3:
                continue
            mix = MIX_RE.search(win)             # «БСТ» и класс бывают на соседних строках
            if mix and mix.start() <= len(line):  # код смеси начинается в этой строке
                found.append((float(re.sub(r"[ВB\s]", "", mix.group(1)).replace(",", ".")), pno))
            elif CONCRETE_WORD_RE.search(line) and not MIX_RE.search(win):
                # В марке смеси класс прочности один и идёт первым: «В30 П4 F200 W10»; OCR читает W10 как «В10»
                m = CLASS_RE.search(win)
                if m:
                    found.append((float(m.group(1).replace(",", ".")), pno))
    return [(v, p) for v, p in found if v in STANDARD_CLASSES]     # «B3», «B13» — шум OCR


def _rebar(pages):
    """[(значение, метка, страница)]: «Арматура А500С d10 Сертификат …»."""
    found = []
    for pno, text, _ in pages:
        for line, win in _windows(text):
            if REBAR_WORD_RE.search(line):
                for m in REBAR_RE.finditer(win):
                    found.append((int(m.group(1)), "А" + m.group(1) + ("С" if m.group(2) else ""), pno))
    return [f for f in found if f[0] in STANDARD_REBAR]            # «А270С», «А506» — шум OCR


def element_of(work, design):
    """Конструкция из ответа трека КР, к которой относится акт, или None."""
    if not work:
        return None
    if FORSHAHTA_RE.search(work) and not NO_MATERIAL_WORK_RE.search(work):
        return "Форшахта"
    if REINFORCED_PILES_RE.search(work) and design and WALL_DESIGN_RE.search(design):
        return "Стена в грунте"
    if ACT_SKIP_RE.search(work):
        return None
    for name, rx, where in ELEMENTS:
        if where is None and rx.search(work):
            return name
    if WALL_WORK_RE.search(work) and design and WALL_DESIGN_RE.search(design):
        return "Стена в грунте"
    if SLAB_RE.search(work):
        return _placed("Плиты перекрытий", work)
    if VERTICAL_RE.search(work):
        return _placed("Вертикальные конструкции", work)
    return fuzzy_element(work)


def _placed(kind, work):
    """«… подземной части» или «… надземной части» — по отметке и этажу в тексте акта."""
    return f"{kind} {'подземной' if UNDERGROUND_RE.search(work) else 'надземной'} части"


def fuzzy_element(work):
    """Конструкция по OCR-тексту акта: «Плита нерекрышя в осях…», «Вертикальные конспрукици…»."""
    w = page_kinds._words(work)
    has = lambda target, thr=page_kinds.FUZZ: page_kinds._has(w, target, thr)
    if has("фундаментная") and has("плита"):
        return "Фундаментная плита"
    if has("перекрытия", 0.7) or has("покрытия", 0.7) and has("плита"):
        return _placed("Плиты перекрытий", work)
    if has("вертикальные") or has("пилоны") or has("колонны") or has("стены"):
        return _placed("Вертикальные конструкции", work)
    return None


def parse_segment(file_id, pages, number=None):
    """Один акт: страницы [(номер, текст, источник)] от его бланка до следующего бланка."""
    pages = [(p, ocr_rebar_fix(t.translate(ITALIC2CYR)) if s == "ocr" else t, s) for p, t, s in pages]
    full = "\n".join(t for _, t, _ in pages)
    # Точный разбор по номерам полей — для текстового слоя; в OCR строки склеены и порваны, там — нечёткий
    ocr = any(s == "ocr" for _, _, s in pages) and not any(s == "text" for _, _, s in pages)
    get = (lambda n: field_fuzzy(full, n)) if ocr else (lambda n: field(full, n) or field_fuzzy(full, n))
    work, design = get(1), get(2)
    name = file_path(file_id).name
    return {
        "file_id": file_id,
        "name": name if number is None else f"{name} · акт {number} (стр. {pages[0][0]}–{pages[-1][0]})",
        "first_page": pages[0][0],
        "date": text_date(pages[0][1]) or (text_date(pages[1][1]) if len(pages) > 1 else None),
        "work": work,
        "design": design,
        "materials": get(3),
        "element": element_of(work, design),
        "concrete": _concrete(pages),
        "rebar": _rebar(pages),
        "pages": len(pages),
        "pages_text": sum(s == "text" for _, _, s in pages),
        "pages_ocr": sum(s == "ocr" for _, _, s in pages),
    }


def parse_file(file_id):
    """Акты файла: [] — не акт или нет текста; один — файл-акт (Новослободская); несколько — сшитая папка
    (Речников: акты по ~300 страниц в папке), делится по началам бланков (act_starts)."""
    pages = file_pages(file_id)
    starts = act_starts(pages)
    if not starts:
        return []
    if len(starts) == 1:
        return [parse_segment(file_id, pages)]
    bounds = starts + [len(pages)]
    return [parse_segment(file_id, pages[a:b], k) for k, (a, b) in enumerate(zip(bounds, bounds[1:]), 1)]


def id_files(object_id):
    """Все PDF исполнительной документации объекта. Редакций у ИД нет — акты не заменяют друг друга."""
    return [r["file_id"] for r in paths.read_jsonl(paths.MANIFEST)
            if r["object_id"] == object_id and r["extension"] == ".pdf"
            and r["stage"] in ("ID", "RD_ID_MIXED")]


def object_acts(object_id):
    acts, stats = [], Counter()
    for file_id in id_files(object_id):
        stats["файлов ИД"] += 1
        found = parse_file(file_id)
        if not found:
            stats["не акт или нет текста"] += 1
            continue
        if len(found) > 1:
            stats["сшитых папок"] += 1
            stats["актов из сшитых папок"] += len(found)
        acts += found
        stats["актов"] += len(found)
        stats["отнесено к конструкции"] += sum(a["element"] is not None for a in found)
    return acts, stats


def act_date(act):
    """(год, месяц, день) из имени «АОСР №1А-БСС от 24.02.2026.pdf», иначе из бланка; без даты — в конец."""
    m = ACT_DATE_RE.search(act.get("name") or "")
    if m:
        return (m.group(3), m.group(2), m.group(1))
    return act.get("date") or ("9999", "99", "99")


def merge_into(checks, object_id, rd_classes, rd_rebar):
    """Добавить сравнение РД ↔ ИД в проверки KR-055 и KR-057 (по конструкциям).

    rd_classes: {конструкция: [записи трека КР]}, rd_rebar: [записи трека КР] — значения РД.
    Метку меняем только в сторону нарушения. Если ИД согласуется с РД, пишем id_value и добавляем в доказательства
    страницу первого по дате акта этой конструкции, где найден класс: так в эталоне (KR-055 «Стена в грунте» —
    АОСР №1А-БСС, F0004 стр. 5) и в сравнениях организаторов (KR-057 форшахта — АОСР №1АФ, F0068 стр. 2).
    """
    acts, _ = object_acts(object_id)
    by_code = {(c["parameter_code"], c["location"]): c for c in checks}

    def apply(check, rd_rows, id_items, fmt, lower):
        """id_items: [(значение, подпись, акт, страница)]."""
        if not check or not id_items:
            return
        check["id_value"] = fmt(id_items)
        bad = []
        if rd_rows:
            rd_min = min(r["value"] for r in rd_rows)
            bad = [it for it in id_items if lower(it[0], rd_min)]
        # По сканам «понижение» засчитываем, только если его показывают хотя бы MIN_OCR_ACTS разных акта:
        # один misread («В30» вместо «В35») не должен становиться нарушением
        if bad and all(it[2]["pages_text"] == 0 for it in bad) and \
                len({(it[2]["file_id"], it[2]["first_page"]) for it in bad}) < MIN_OCR_ACTS:
            bad = []
        if not bad:
            first = min(id_items, key=lambda it: (act_date(it[2]), it[2]["file_id"], it[3]))
            if not any(e["stage"] == "ID" for e in check["evidence"]):
                check["evidence"].append({"stage": "ID", "file_id": first[2]["file_id"], "pdf_page_number": first[3]})
            return
        worst = min(bad, key=lambda it: it[0])
        check["violation_label"] = "VIOLATION_PRESENT"
        ev = [e for e in check["evidence"] if e["stage"] != "PD"] if check["evidence"] else []
        if not any(e["stage"] == "RD" for e in ev):
            b = min(rd_rows, key=lambda r: r["value"])
            ev.append({"stage": "RD", "file_id": b["file_id"], "pdf_page_number": b["page"]})
        ev.append({"stage": "ID", "file_id": worst[2]["file_id"], "pdf_page_number": worst[3]})
        check["evidence"] = ev

    for element, _, _ in ELEMENTS:
        items = [(v, f"B{v:g}", a, p) for a in acts if a["element"] == element for v, p in a["concrete"]]
        apply(by_code.get(("KR-055", element)), rd_classes.get(element, []), items,
              fmt=lambda its: "B" + "/B".join(f"{v:g}" for v in sorted({it[0] for it in its})),
              lower=lambda v, rd_min: v < rd_min)

    rebar_fmt = lambda its: "/".join(sorted({it[1] for it in its}))
    for check in checks:
        if check["parameter_code"] == "KR-057" and check["location"] != "Рабочая арматура":
            el = check["location"]
            items = [(v, label, a, p) for a in acts if a["element"] == el for v, label, p in a["rebar"]]
            rd_rows = [r for r in rd_rebar if r.get("construction") == el and r.get("role") != "constructive"]
            apply(check, rd_rows, items, fmt=rebar_fmt, lower=lambda v, rd_min: v < rd_min)

    # По объекту роль арматуры в акте не известна: А240С в акте — обычно хомуты, а не рабочая. Поэтому здесь
    # ИД только дописывает значение и доказательство, а нарушения не даёт никогда.
    items = [(v, label, a, p) for a in acts if a["element"] for v, label, p in a["rebar"]]
    apply(by_code.get(("KR-057", "Рабочая арматура")), rd_rebar, items,
          fmt=rebar_fmt, lower=lambda v, rd_min: False)
    return checks


def main(argv):
    object_id = argv[0] if argv else "OBJ-NOVOSLOBODSKAYA"
    acts, stats = object_acts(object_id)
    for k, v in stats.items():
        print(f"{k:28} {v}")
    by_element = defaultdict(list)
    for a in acts:
        by_element[a["element"] or "— не отнесён"].append(a)
    for element, items in sorted(by_element.items()):
        concrete = sorted({v for a in items for v, _ in a["concrete"]})
        rebar = sorted({label for a in items for _, label, _ in a["rebar"]})
        print(f"\n{element}: актов {len(items)}; бетон {['B%g' % v for v in concrete]}; арматура {rebar}")
        works = Counter(re.sub(r"\s*(№|в/о).*", "", a["work"] or "?")[:50] for a in items)
        for work, n in works.most_common(6):
            print(f"    {n:>3} × {work}")
    no_text = sum(a["pages"] - a["pages_text"] - a["pages_ocr"] for a in acts)
    print(f"\nстраниц без текста и без OCR в найденных актах: {no_text}")


if __name__ == "__main__":
    main(sys.argv[1:])
