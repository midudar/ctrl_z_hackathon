"""Этапы 2–4 для чертежей ОВ: помещение → системы, сравнение ПД и РД по помещениям.

Идея: на планах и схемах у марок систем (П2, В2.7, ВЕ13) есть ближайший номер помещения.
Собираем для каждого помещения множество систем по всем чертёжным листам стадии и сравниваем
ПД с РД. Сравнение на уровне систем (В2, ВЕ, П2), а не веток (В2.7): так устойчивее к OCR.

    python -m ml.rooms OBJ-TYUMENSKAYA-5-GOLD-SEED      # прогон + submission + оценка
"""
import math
import re
import sys
from collections import defaultdict
from functools import lru_cache

from ml import paths
from ml.marks import parse_marks, system_of
from ml.pages import has_ocr, read_page

import numpy as np

MAX_DIST = 200          # pt: без «прямой видимости» марка относится к номеру не дальше этого
VISIBLE_DIST = 600      # pt: с прямой видимостью (без пересечения стен) — к номеру не дальше этого
WALL_MIN_LEN = 100      # pt: более короткие отрезки — не стены (CAD режет линии на куски ~1 pt;
                        #     контуры оборудования на схемах ~60–90 pt)
LEADER_MIN_LEN = 10     # pt: выноски
LEADER_MAX_LEN = 400
LEADER_SNAP = 6         # pt: насколько далеко от рамки подписи может начинаться выноска
LEADER_FIRST = False    # эксперимент 19.09: ломает 012, ложных не убирает; подпись с выноской относится к тому, на что указывает выноска
MERGE_WALLS = False     # эксперимент 19.09: ложных не убирает; склеивать соосные куски стен (в РД стены нарисованы кусками по 90–96 pt)
MOVED_SAFETY = True     # страховка «установка есть в РД у другого помещения» → COMPARISON_IMPOSSIBLE
USE_LAYERS = True       # стены и выноски по слоям CAD, если они сохранились в PDF
WALL_LAYER_RE = re.compile(r"архитект|монолит|перегород|газоблок|стен|wall|door|^_?ар$", re.I)
LEADER_LAYER_RE = re.compile(r"выноск|leader", re.I)
MIN_LAYER_SEGMENTS = 200  # меньше отрезков на слоях стен — слоёв по сути нет, работает эвристика
LABEL_CLEARANCE = 14    # pt: вокруг номера помещения кружок/рамка на слое архитектуры — не стена
LEADER_MIN_REACH = 25   # pt: конец «выноски» ближе к подписи — это край полки, а не выноска
LEADER_OVERSHOOT = 5    # pt: насколько продлеваем выноску за её конец, внутрь элемента
USE_SHELVES = True      # подпись на полке выноски (ГОСТ 2.316: полка + наклонная линия) — к тому, на что указывает
                        #     наклонная линия (26.09: на листах без слоя выносок; см. shelf_anchor)
MIN_MARKS_PER_STAGE = 3  # страховка: у помещения на стадии меньше марок → сравнение ненадёжно
ROOM_RE = re.compile(r"^\d{3}$")

MIN_ROOMS_ON_DRAWING = 5    # чертёжный лист: крупный формат и хотя бы столько номеров помещений
MIN_DRAWING_SIDE = 1000     # pt: A3 и больше; A4-страницы с номерами — это таблицы и акты

# Файлы ОВ по объектам (стадия → file_id). Выбор файлов по дисциплине — задача реестра (этап 0).
DRAWING_FILES = {
    "OBJ-TYUMENSKAYA-5-GOLD-SEED": {"PD": ["F0171"], "RD": ["F0201", "F0202"]},
}


def drawing_pages(file_id):
    """Чертёжные листы файла: крупный формат и ≥ MIN_ROOMS_ON_DRAWING номеров помещений в текстовом слое."""
    import pymupdf
    from ml.pages import file_path
    pages = []
    for i, page in enumerate(pymupdf.open(file_path(file_id))):
        if max(page.rect.width, page.rect.height) < MIN_DRAWING_SIDE:
            continue
        rooms = {w[4] for w in page.get_text("words") if ROOM_RE.match(w[4])}
        if len(rooms) >= MIN_ROOMS_ON_DRAWING:
            pages.append(i + 1)
    return pages


def drawings(object_id):
    return {stage: [(f, drawing_pages(f)) for f in files] for stage, files in DRAWING_FILES[object_id].items()}


def _center(t):
    b = t["bbox"]
    return (b[0] + b[2]) / 2, (b[1] + b[3]) / 2


def _room_text(text):
    """«267,» → «267» (перечисление помещений в одной подписи: «267, 270»)."""
    s = text.strip().rstrip(",;.")
    return s if ROOM_RE.match(s) else None


def room_labels(page):
    """Номера помещений на плане. Колонки таблиц (экспликация) отбрасываются: там ≥4 номера
    стоят на одной вертикали. У токена дописывается поле room — номер без знаков препинания."""
    cands = [dict(t, room=_room_text(t["text"])) for t in page["tokens"] if t["src"] == "text"]
    cands = [t for t in cands if t["room"]]
    if len(cands) < 5:
        cands = [dict(t, room=_room_text(t["text"])) for t in page["tokens"]]
        cands = [t for t in cands if t["room"]]
    labels = []
    for t in cands:
        same_col = sum(1 for o in cands if o is not t and abs(o["bbox"][0] - t["bbox"][0]) < 2)
        if same_col < 4:
            labels.append(t)
    return labels


def room_group(label, labels):
    """Номера из одной подписи «267, 270»: та же строка, стоят вплотную, разделены запятой.
    Собираем в обе стороны от найденного номера."""
    line = sorted((o for o in labels if abs(o["bbox"][1] - label["bbox"][1]) < 2), key=lambda o: o["bbox"][0])
    i = next(k for k, o in enumerate(line) if o is label)
    linked = lambda a, b: 0 < b["bbox"][0] - a["bbox"][2] < 12 and a["text"].strip().endswith(",")
    lo = i
    while lo > 0 and linked(line[lo - 1], line[lo]):
        lo -= 1
    hi = i
    while hi + 1 < len(line) and linked(line[hi], line[hi + 1]):
        hi += 1
    return [o["room"] for o in line[lo:hi + 1]]


def _item_segments(it):
    """Отрезки одного элемента пути pymupdf: линия, прямоугольник или четырёхугольник."""
    if it[0] == "l":
        return [(it[1].x, it[1].y, it[2].x, it[2].y)]
    if it[0] == "re":
        r = it[1]
        return [(r.x0, r.y0, r.x1, r.y0), (r.x1, r.y0, r.x1, r.y1), (r.x0, r.y1, r.x1, r.y1), (r.x0, r.y0, r.x0, r.y1)]
    if it[0] == "qu":
        q = it[1]
        pts = [q.ul, q.ur, q.lr, q.ll, q.ul]
        return [(a.x, a.y, b.x, b.y) for a, b in zip(pts, pts[1:])]
    return []


@lru_cache(maxsize=64)
def page_geometry(file_id, pno):
    """Геометрия листа, прочитанная один раз (на листе РД до 165 тыс. элементов).

    Если CAD сохранил слои, стены и выноски берутся по имени слоя; иначе остаются эвристики:
    стены — длинные ахроматические отрезки, выноски — ахроматические отрезки средней длины."""
    import pymupdf
    from ml.pages import file_path
    page = pymupdf.open(file_path(file_id))[pno - 1]
    layer_walls, layer_leaders, heur_raw, heur_leaders = [], [], [], []
    for d in page.get_drawings():
        segs = [s for it in d["items"] for s in _item_segments(it)]
        layer = d.get("layer") or ""
        if WALL_LAYER_RE.search(layer):
            layer_walls += [s for s in segs if math.hypot(s[2] - s[0], s[3] - s[1]) >= 2]
        if LEADER_LAYER_RE.search(layer):
            layer_leaders += [s for s in segs if math.hypot(s[2] - s[0], s[3] - s[1]) >= 2]
        color = d.get("color")
        if color is None or max(color) - min(color) > 0.1 or min(color) > 0.6:
            continue
        heur_raw += segs
        heur_leaders += [s for s in segs if d["items"] and d["items"][0][0] == "l"
                         and LEADER_MIN_LEN <= math.hypot(s[2] - s[0], s[3] - s[1]) <= LEADER_MAX_LEN]
    if MERGE_WALLS:
        heur_walls = _merge_collinear(heur_raw)
    else:
        heur_walls = [s for s in heur_raw if math.hypot(s[2] - s[0], s[3] - s[1]) >= WALL_MIN_LEN]
    arr = lambda xs: np.array(xs, dtype=float).reshape(-1, 4)
    use_walls = USE_LAYERS and len(layer_walls) >= MIN_LAYER_SEGMENTS
    use_leaders = USE_LAYERS and len(layer_leaders) >= 10
    return {
        "walls": arr(layer_walls if use_walls else heur_walls),
        "leaders": arr(layer_leaders if use_leaders else heur_leaders),
        # для подписей ключевых элементов: их выноски не всегда лежат на слое «…Выноски»
        "leaders_any": arr(layer_leaders + heur_leaders),
        # все тёмные обводки любой длины — в них ищутся полки выносок (shelf_anchor)
        "strokes": arr([s for s in heur_raw if math.hypot(s[2] - s[0], s[3] - s[1]) >= 0.8]),
        "wall_layers": use_walls,
        "leader_layers": use_leaders,
    }


def wall_segments(file_id, pno):
    """Стены и перегородки листа как массив [[x1, y1, x2, y2], ...] (по слоям или по эвристике)."""
    return page_geometry(file_id, pno)["walls"]


def _merge_collinear(segs, tol=0.6, gap=2.5):
    """Стены в РД нарисованы кусками по 90–96 pt: склеиваем соосные горизонтальные и вертикальные
    отрезки, идущие встык, и оставляем те, что после склейки не короче WALL_MIN_LEN.
    Замкнутые контуры установок (60–90 pt) при этом остаются короткими."""
    out = []
    lines = {"h": defaultdict(list), "v": defaultdict(list)}
    for x1, y1, x2, y2 in segs:
        if abs(y1 - y2) <= tol:
            lines["h"][round(y1 / tol)].append((min(x1, x2), max(x1, x2)))
        elif abs(x1 - x2) <= tol:
            lines["v"][round(x1 / tol)].append((min(y1, y2), max(y1, y2)))
        elif math.hypot(x2 - x1, y2 - y1) >= WALL_MIN_LEN:
            out.append((x1, y1, x2, y2))
    for kind, groups in lines.items():
        for key, spans in groups.items():
            c = key * tol
            spans.sort()
            a, b = spans[0]
            for s, e in spans[1:] + [(math.inf, math.inf)]:
                if s <= b + gap:
                    b = max(b, e)
                    continue
                if b - a >= WALL_MIN_LEN:
                    out.append((a, c, b, c) if kind == "h" else (c, a, c, b))
                a, b = s, e
    return out


def leader_segments(file_id, pno):
    """Выноски от подписей к элементам (по слоям «…Выноски» или по эвристике)."""
    return page_geometry(file_id, pno)["leaders"]


def follow_leader(bbox, leaders, hops=2):
    """Если у подписи начинается выноска (конец отрезка у рамки текста), идём к дальнему концу.
    Полка выноски + наклонная линия = две ступени, поэтому до двух переходов. None — выноски нет."""
    if not len(leaders):
        return None
    x0, y0, x1, y1 = bbox
    pad = LEADER_SNAP
    ends = [leaders[:, 0:2], leaders[:, 2:4]]
    point = None
    for i in (0, 1):
        e = ends[i]
        near = (e[:, 0] >= x0 - pad) & (e[:, 0] <= x1 + pad) & (e[:, 1] >= y0 - pad) & (e[:, 1] <= y1 + pad)
        idx = np.flatnonzero(near)
        if len(idx):
            far = ends[1 - i][idx]
            # берём выноску, уходящую дальше всего от центра подписи
            c = np.array([(x0 + x1) / 2, (y0 + y1) / 2])
            j = int(np.argmax(np.hypot(*(far - c).T)))
            cand = far[j]
            if point is None or math.dist(cand, c) > math.dist(point, c):
                point = cand
    if point is None:
        return None
    prev = np.array([(x0 + x1) / 2, (y0 + y1) / 2])
    for _ in range(hops - 1):
        e0 = np.hypot(*(leaders[:, 0:2] - point).T) < 1.5
        e1 = np.hypot(*(leaders[:, 2:4] - point).T) < 1.5
        nxt = np.vstack([leaders[e0][:, 2:4], leaders[e1][:, 0:2]])
        # не возвращаемся назад: следующий конец должен быть дальше от подписи, чем текущая точка
        nxt = nxt[np.hypot(*(nxt - prev).T) > math.dist(point, prev) + 1.5]
        if not len(nxt):
            break
        point = nxt[int(np.argmax(np.hypot(*(nxt - point).T)))]
    return float(point[0]), float(point[1])


def shelf_anchor(bbox, strokes):
    """Конец выноски, если подпись стоит на полке; иначе None.

    Выноска по ГОСТ 2.316: текст на горизонтальной полке, к концу полки примыкает наклонная линия к элементу.
    Так подписаны установки у стен: «В3.1» на листе 10 ПД Тюменской стоит над стеной 313 / 314, а линия ведёт
    к установке в 314; «В17.13» — над коридором 168, линия — к клапану в горячем цехе 189. Выноски там лежат
    на слое «0», а на слое «…Выноски» только белые маски под текстом, поэтому follow_leader их не видит.
    Полка — горизонтальный отрезок у нижнего края текста, под большей частью его ширины и не длиннее
    2,5 ширины + 30 pt (не стена и не рамка таблицы). Примыкающая линия — только наклонная (не горизонталь —
    полка соседней подписи, не вертикаль — стена или стояк), один шаг: цепочки по отрезкам уходят по стенам."""
    if not len(strokes):
        return None
    x0, y0, x1, y1 = bbox
    w, h = x1 - x0, y1 - y0
    sx0, sy0, sx1, sy1 = strokes.T
    ymid = (sy0 + sy1) / 2
    lo, hi = np.minimum(sx0, sx1), np.maximum(sx0, sx1)
    shelves = np.flatnonzero((np.abs(sy1 - sy0) <= 0.6) & (ymid >= y1 - 0.35 * h) & (ymid <= y1 + 0.6 * h)
                             & (np.minimum(hi, x1) - np.maximum(lo, x0) >= 0.6 * w) & (hi - lo <= 2.5 * w + 30))
    c = ((x0 + x1) / 2, (y0 + y1) / 2)
    best = None
    for i in shelves:
        for end in (strokes[i][0:2], strokes[i][2:4]):
            at0 = np.hypot(*(strokes[:, 0:2] - end).T) < 1.5
            at1 = np.hypot(*(strokes[:, 2:4] - end).T) < 1.5
            for j in np.flatnonzero(at0 | at1):
                if j == i:
                    continue
                far = strokes[j][2:4] if at0[j] else strokes[j][0:2]
                dx, dy = abs(strokes[j][2] - strokes[j][0]), abs(strokes[j][3] - strokes[j][1])
                if dx < 2 or dy < 2 or not 0.18 <= dy / dx <= 5.7:     # 10°…80° к горизонтали
                    continue
                if math.dist(far, c) <= max(math.dist(end, c) + 1.5, LEADER_MIN_REACH):
                    continue                                          # линия должна уводить от подписи
                if best is None or math.dist(far, c) > math.dist(best, c):
                    best = far
    return None if best is None else (float(best[0]), float(best[1]))


def _towards(p, q, d):
    """Точка на отрезке p→q на расстоянии d от p."""
    L = math.dist(p, q)
    if L <= d or L == 0:
        return p
    return p[0] + (q[0] - p[0]) * d / L, p[1] + (q[1] - p[1]) * d / L


def _crosses(p, q, segs):
    """Пересекает ли отрезок p–q хотя бы один из segs (строгое пересечение)."""
    if not len(segs):
        return False
    x1, y1, x2, y2 = segs.T
    def orient(ax, ay, bx, by, cx, cy):
        return (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)
    d1 = orient(x1, y1, x2, y2, p[0], p[1])
    d2 = orient(x1, y1, x2, y2, q[0], q[1])
    d3 = orient(p[0], p[1], q[0], q[1], x1, y1)
    d4 = orient(p[0], p[1], q[0], q[1], x2, y2)
    return bool(np.any((d1 * d2 < 0) & (d3 * d4 < 0)))


def page_room_systems(file_id, pno, trace=None):
    """{номер помещения: {система: [марки]}} для одной страницы.

    Марка относится к ближайшему номеру помещения, до которого можно провести отрезок, не пересекая
    стену (радиус VISIBLE_DIST). Если такого нет — к ближайшему номеру в радиусе MAX_DIST.
    """
    ocr = has_ocr(file_id, pno)
    if not ocr:
        print(f"  ! {file_id} стр.{pno}: OCR нет в кэше, только текстовый слой (подписи-линии потеряются)")
    page = read_page(file_id, pno, ocr=ocr)
    rooms = room_labels(page)
    result = defaultdict(lambda: defaultdict(set))
    if not rooms:
        return result
    geo = page_geometry(file_id, pno)
    walls, leaders = geo["walls"], geo["leaders"]
    # выноски со своего слоя надёжны: подпись относится к тому, на что указывает выноска
    leader_first = LEADER_FIRST or geo["leader_layers"]
    hops = 8 if geo["leader_layers"] else 2

    def visible_room(point):
        for r in sorted(rooms, key=lambda r: math.dist(_center(r), point)):
            rc = _center(r)
            d = math.dist(rc, point)
            if d > VISIBLE_DIST:
                return None
            # концы отрезка чуть укорачиваем: вокруг номера помещения на слое архитектуры нарисован кружок
            a = _towards(point, rc, 3)
            b = _towards(rc, point, LABEL_CLEARANCE)
            if d <= LABEL_CLEARANCE + 3 or not _crosses(a, b, walls):
                return r
        return None

    for t in page["tokens"]:
        marks = parse_marks(t["text"])
        if not marks:
            continue
        c = _center(t)
        # если от подписи идёт выноска, подпись относится к тому, на что она указывает
        chosen = None
        if leader_first:
            anchor = follow_leader(t["bbox"], leaders, hops=hops)
            if anchor is not None and math.dist(anchor, c) >= LEADER_MIN_REACH:
                chosen = visible_room(anchor)
        if chosen is None and USE_SHELVES:        # подпись на полке выноски (слоя нет или на нём только маски текста)
            anchor = shelf_anchor(t["bbox"], geo["strokes"])
            if anchor is not None:
                chosen = visible_room(anchor)
        if chosen is None:
            chosen = visible_room(c)
        if chosen is None and not leader_first:
            anchor = follow_leader(t["bbox"], leaders, hops=hops)
            if anchor is not None:
                chosen = visible_room(anchor)
        if chosen is None:
            nearest = min(rooms, key=lambda r: math.dist(_center(r), c))
            if math.dist(_center(nearest), c) <= MAX_DIST:
                chosen = nearest
        if chosen is None:
            continue
        for m in marks:
            result[chosen["room"]][system_of(m)].add(m)
        if trace is not None:        # для карточек проверки: какая подпись к какому помещению отнесена
            trace.append({"room": chosen["room"], "marks": marks, "bbox": t["bbox"], "room_bbox": chosen["bbox"]})
    return result


# Ключевые элементы: подпись на чертеже → (код параметра, название элемента).
# FREE-HEATING-001 — эталонный код свободного поиска вне матрицы 132 (тёплые полы Тюменской).
KEYWORD_ELEMENTS = [
    (re.compile(r"т[её]пл\w*\W*пол", re.I), "FREE-HEATING-001", "тёплый пол"),
]
# Тема элемента: по ней выбирается лист РД для доказательства (по названию листа в штампе).
KEYWORD_TOPIC = {"тёплый пол": re.compile(r"отоплен|теплоснаб")}

# OCR читает курсивный шрифт штампов (ISOCPEUR) с латинскими двойниками: «OmonлeHue» = «отопление»
_ITALIC2CYR = str.maketrans("abcehkmnoprtuxyg", "авсенктпоргтихуд")


def stamp_text(file_id, pno):
    """Текст штампа (правый нижний угол листа), нормализованный к кириллице в нижнем регистре."""
    page = read_page(file_id, pno, ocr=has_ocr(file_id, pno))
    W, H = page["width"], page["height"]
    words = [t["text"] for t in page["tokens"] if t["bbox"][0] > W * 0.6 and t["bbox"][1] > H * 0.88]
    return " ".join(words).lower().translate(_ITALIC2CYR)


def leader_ends(bbox, leaders, hops=2):
    """Все концы выносок, начинающихся у рамки подписи (у одной подписи бывает несколько выносок)."""
    if not len(leaders):
        return []
    x0, y0, x1, y1 = bbox
    pad = LEADER_SNAP
    frontier = []
    for i in (0, 1):
        e = leaders[:, 2 * i:2 * i + 2]
        near = (e[:, 0] >= x0 - pad) & (e[:, 0] <= x1 + pad) & (e[:, 1] >= y0 - pad) & (e[:, 1] <= y1 + pad)
        frontier += [tuple(p) for p in leaders[near][:, 2 * (1 - i):2 * (1 - i) + 2]]
    inside = lambda p: x0 - pad <= p[0] <= x1 + pad and y0 - pad <= p[1] <= y1 + pad
    c = ((x0 + x1) / 2, (y0 + y1) / 2)
    # (точка, откуда пришли): для первого шага — центр подписи
    frontier = [(p, c) for p in frontier if not inside(p)] or [(p, c) for p in frontier]
    ends = []
    for _ in range(hops - 1):
        nxt_frontier = []
        for p, prev in frontier:
            pa = np.array(p)
            e0 = np.hypot(*(leaders[:, 0:2] - pa).T) < 1.5
            e1 = np.hypot(*(leaders[:, 2:4] - pa).T) < 1.5
            nxt = [(tuple(q), p) for q in np.vstack([leaders[e0][:, 2:4], leaders[e1][:, 0:2]])
                   if math.dist(q, p) > 1.5 and math.dist(q, prev) > math.dist(p, prev) and not inside(q)]
            if nxt:
                nxt_frontier += nxt
            else:
                ends.append((p, prev))
        frontier = nxt_frontier
    result = []
    for p, prev in ends + frontier:
        if math.dist(p, c) < LEADER_MIN_REACH:
            continue                      # край полки у самой подписи, а не выноска
        # чуть дальше по направлению выноски: конец часто упирается в элемент, стоящий у самой стены
        d = math.dist(p, prev) or 1.0
        result.append((p[0] + (p[0] - prev[0]) / d * LEADER_OVERSHOOT, p[1] + (p[1] - prev[1]) / d * LEADER_OVERSHOOT))
    return result


def _keyword_hits(tokens):
    """Подписи ключевых элементов. Фраза бывает разбита на слова: склеиваем соседей по строке."""
    hits = []
    for i, t in enumerate(tokens):
        line = [t] + [o for o in tokens[i + 1:i + 4]
                      if abs(o["bbox"][1] - t["bbox"][1]) < 3 and 0 <= o["bbox"][0] - t["bbox"][2] < 15]
        text = " ".join(o["text"] for o in line)
        for rx, code, name in KEYWORD_ELEMENTS:
            if rx.search(text) and not any(h[2] == name and h[3] is t for h in hits):
                bbox = [min(o["bbox"][0] for o in line), min(o["bbox"][1] for o in line),
                        max(o["bbox"][2] for o in line), max(o["bbox"][3] for o in line)]
                hits.append((code, name, bbox, t))
    return hits


def _text_block(bbox, tokens):
    """Многострочная подпись целиком: добавляем строки, примыкающие сверху/снизу и перекрытые по x.
    Нужна, чтобы полка выноски над первой строкой считалась частью подписи, а не выноской."""
    box = list(bbox)
    changed = True
    while changed:
        changed = False
        for t in tokens:
            b = t["bbox"]
            vgap = max(b[1] - box[3], box[1] - b[3])
            if vgap < 4 and b[0] < box[2] + 10 and b[2] > box[0] - 10 and not (
                    box[0] <= b[0] and b[2] <= box[2] and box[1] <= b[1] and b[3] <= box[3]):
                new = [min(box[0], b[0]), min(box[1], b[1]), max(box[2], b[2]), max(box[3], b[3])]
                if new[2] - new[0] < 250 and new[3] - new[1] < 60:     # не разрастаться на весь лист
                    box, changed = new, True
    return box


def page_room_keywords(file_id, pno):
    """{помещение: {(код, элемент)}} и множество элементов, встреченных на странице вообще."""
    page = read_page(file_id, pno, ocr=has_ocr(file_id, pno))
    rooms = room_labels(page)
    found = defaultdict(set)
    seen = set()
    hits = _keyword_hits(page["tokens"])
    if not hits:
        return found, seen
    walls = wall_segments(file_id, pno)
    leaders = page_geometry(file_id, pno)["leaders_any"]
    for code, name, bbox, _ in hits:
        seen.add((code, name))
        box = _text_block(bbox, page["tokens"])
        anchors = leader_ends(box, leaders) or [_center({"bbox": bbox})]
        for a in anchors:
            for r in sorted(rooms, key=lambda r: math.dist(_center(r), a)):
                if math.dist(_center(r), a) > VISIBLE_DIST:
                    break
                if not _crosses(a, _center(r), walls):
                    for room in room_group(r, rooms):
                        found[room].add((code, name))
                    break
    return found, seen


def stage_room_systems(sources):
    """Сводка по стадии: room → {system → marks}, плюс где встретилось помещение."""
    rooms = defaultdict(lambda: defaultdict(set))
    where = defaultdict(lambda: defaultdict(set))     # room → (file, page) → системы на этой странице
    for file_id, pages in sources:
        for pno in pages:
            for room, systems in page_room_systems(file_id, pno).items():
                for s, ms in systems.items():
                    rooms[room][s] |= ms
                    where[room][(file_id, pno)].add(s)
    return rooms, where


def _vocab(file_id, pno):
    page = read_page(file_id, pno, ocr=has_ocr(file_id, pno))
    return {w for w in (re.sub(r"\W", "", t["text"].lower()) for t in page["tokens"]) if len(w) >= 3}


def stage_keywords(sources):
    """room → {(код, элемент): {страницы}}, все упомянутые элементы, room → страницы, где есть номер."""
    by_room = defaultdict(lambda: defaultdict(set))
    seen = set()
    present = defaultdict(set)
    for file_id, pages in sources:
        for pno in pages:
            found, s = page_room_keywords(file_id, pno)
            seen |= s
            for room, elems in found.items():
                for e in elems:
                    by_room[room][e].add((file_id, pno))
            for r in room_labels(read_page(file_id, pno, ocr=has_ocr(file_id, pno))):
                present[r["room"]].add((file_id, pno))
    return by_room, seen, present


def mentioned_anywhere(file_ids, rx):
    """Есть ли подпись элемента хоть где-то в файлах стадии (весь текстовый слой + OCR из кэша)."""
    import pymupdf
    from ml.pages import CACHE, file_path, file_sha
    for f in file_ids:
        for page in pymupdf.open(file_path(f)):
            if rx.search(page.get_text()):
                return True
        for cached in (CACHE / file_sha(f)[:16]).glob("*_ocr.json"):
            if rx.search(" ".join(t["text"] for t in paths.read_json(cached)["tokens"])):
                return True
    return False


def compare_keywords(object_id, cfg):
    """Элемент из ПД у помещения (тёплый пол) → в РД его нет совсем → MISSING_DESIGN_ELEMENT."""
    pd_by_room, _, _ = stage_keywords(cfg["PD"])
    rd_by_room, rd_seen, rd_present = stage_keywords(cfg["RD"])
    rd_files = [f for f, _ in cfg["RD"]]
    checks = []
    for room, elems in sorted(pd_by_room.items()):
        if room not in rd_present:
            continue                                   # помещения нет на чертежах РД — сравнивать не с чем
        for (code, name), pd_pages in sorted(elems.items()):
            if (code, name) in rd_by_room.get(room, {}):
                continue                               # в РД у этого помещения элемент тоже есть
            rx = next(rx for rx, c, n in KEYWORD_ELEMENTS if n == name)
            absent_everywhere = (code, name) not in rd_seen and not mentioned_anywhere(rd_files, rx)
            pd_page = sorted(pd_pages)[0]
            pd_vocab = _vocab(*pd_page)
            topic = KEYWORD_TOPIC.get(name)
            rd_page = max(sorted(rd_present[room]), key=lambda k: (
                bool(topic and topic.search(stamp_text(*k))),
                len(pd_vocab & _vocab(*k)) / (len(pd_vocab | _vocab(*k)) or 1)))
            checks.append({
                "parameter_code": code,
                "location": room,
                "violation_label": "VIOLATION_PRESENT" if absent_everywhere else "COMPARISON_IMPOSSIBLE",
                "pd_value": f"{name}: предусмотрен",
                "rd_value": f"{name}: не найден" if absent_everywhere else f"{name}: упомянут в РД, но не у этого помещения",
                "id_value": None,
                "evidence": [
                    {"stage": "PD", "file_id": pd_page[0], "pdf_page_number": pd_page[1]},
                    {"stage": "RD", "file_id": rd_page[0], "pdf_page_number": rd_page[1]},
                ],
                "_debug": {"type": "MISSING_DESIGN_ELEMENT", "missing_in_rd": [name], "changed": [],
                           "marks_pd": len(pd_pages), "marks_rd": 0},
            })
    return checks


def best_page(pages, focus, context):
    """Страница-доказательство: больше всего систем из focus (то, что расходится), при равенстве —
    больше систем из context (всё, что есть в помещении на другой стадии)."""
    return max(pages, key=lambda k: (len(pages[k] & focus), len(pages[k] & context), len(pages[k])))


def all_marks(sources):
    """Все марки систем на чертёжных листах стадии, независимо от привязки к помещениям."""
    marks = set()
    for file_id, pages in sources:
        for pno in pages:
            for t in read_page(file_id, pno, ocr=has_ocr(file_id, pno))["tokens"]:
                marks.update(parse_marks(t["text"]))
    return marks


def compare(object_id):
    cfg = drawings(object_id)
    pd_rooms, pd_where = stage_room_systems(cfg["PD"])
    rd_rooms, rd_where = stage_room_systems(cfg["RD"])
    rd_all_marks = all_marks(cfg["RD"])
    checks = []
    for room in sorted(set(pd_rooms) & set(rd_rooms)):
        pd_sys, rd_sys = set(pd_rooms[room]), set(rd_rooms[room])
        n_pd = sum(len(v) for v in pd_rooms[room].values())
        n_rd = sum(len(v) for v in rd_rooms[room].values())
        # 1) система из ПД в помещении РД отсутствует целиком (MISSING_DESIGN_ELEMENT)
        missing = {s for s in pd_sys - rd_sys if s.startswith(("В", "П"))}
        # 2) система есть в обеих стадиях, но ни одна ветка ПД не совпала с ветками РД (CONFIGURATION_MISMATCH).
        #    Проверяем только системы, у которых на обеих стадиях есть ветки с номером (В2.7, а не просто В2).
        changed = set()
        for s in (pd_sys & rd_sys) - {"ВЕ", "ПЕ"}:
            pd_br = {m for m in pd_rooms[room][s] if "." in m}
            rd_br = {m for m in rd_rooms[room][s] if "." in m}
            if pd_br and rd_br and not (pd_br & rd_br):
                changed.add(s)
        if not missing and not changed:
            continue
        diff_type = "MISSING_DESIGN_ELEMENT" if missing else "CONFIGURATION_MISMATCH"
        supply_units = [s for s in pd_sys if s.startswith("П") and s != "ПЕ"]
        code = "IOS4-079" if len(supply_units) >= 3 else "IOS4-078"
        if missing:
            reliable = min(n_pd, n_rd) >= MIN_MARKS_PER_STAGE or (n_pd >= 3 and n_rd >= 1)
        else:
            # «конфигурация изменена» по одной ветке РД — слишком слабое доказательство: в РД нужно ≥ 2 веток.
            # В ПД одной ветки достаточно (147: ПД В2.10 | РД В2.3, В2.4). До полок выносок (26.09) так не
            # работало: «В17.13» с полки над коридором 168 давала ложное 168 (ПД В17.13 | РД В17.9, В17.11).
            reliable = all(len({m for m in rd_rooms[room][s] if "." in m}) >= 2 for s in changed)
        # Страховка: целая установка из ПД (П4, ПД19 — марка без номера ветки) «пропала» у помещения, но на
        # чертежах РД она есть — значит, её подпись отнесена к другому помещению (выноска, перенумерация).
        # Это не доказанная пропажа: помещаем в «сравнить нельзя». Ветки (В2.7) правило не трогает.
        moved = {s for s in missing if all("." not in m for m in pd_rooms[room][s])
                 and pd_rooms[room][s] & rd_all_marks}
        # Если хотя бы половина «пропавших» систем — такие установки, расхождение в основном объясняется
        # ошибкой привязки подписей, и доверять ему нельзя.
        if MOVED_SAFETY and missing and not changed and len(moved) * 2 >= len(missing):
            reliable = False
        label = "VIOLATION_PRESENT" if reliable else "COMPARISON_IMPOSSIBLE"
        pd_page = best_page(pd_where[room], missing | changed, rd_sys)
        rd_page = best_page(rd_where[room], changed, pd_sys)
        def describe(rooms_):
            return "; ".join(f"{s}: {', '.join(sorted(rooms_[room][s]))}" for s in sorted(rooms_[room]))

        checks.append({
            "parameter_code": code,
            "location": room,
            "violation_label": label,
            "pd_value": describe(pd_rooms),
            "rd_value": describe(rd_rooms),
            "id_value": None,
            "evidence": [
                {"stage": "PD", "file_id": pd_page[0], "pdf_page_number": pd_page[1]},
                {"stage": "RD", "file_id": rd_page[0], "pdf_page_number": rd_page[1]},
            ],
            "_debug": {"type": diff_type, "missing_in_rd": sorted(missing), "changed": sorted(changed), "moved": sorted(moved),
                       "marks_pd": n_pd, "marks_rd": n_rd},
        })
    checks += compare_keywords(object_id, cfg)
    return checks, pd_rooms, rd_rooms


def main(argv):
    from ml import eval as ev
    from ml import submission as sub

    object_id = argv[0] if argv else "OBJ-TYUMENSKAYA-5-GOLD-SEED"
    checks, pd_rooms, rd_rooms = compare(object_id)
    print(f"помещений: ПД {len(pd_rooms)}, РД {len(rd_rooms)}, общих {len(set(pd_rooms) & set(rd_rooms))}")
    print(f"кандидатов: {sum(c['violation_label'] == 'VIOLATION_PRESENT' for c in checks)}, "
          f"несравнимо: {sum(c['violation_label'] != 'VIOLATION_PRESENT' for c in checks)}")
    for c in checks:
        d = c["_debug"]
        print(f"  {c['parameter_code']} {c['location']} {c['violation_label'][:10]} {d['type'][:13]}"
              f" нет в РД: {d['missing_in_rd']} изменены: {d['changed']}"
              f"  (марок ПД {d['marks_pd']}, РД {d['marks_rd']})\n      ПД  {c['pd_value']}\n      РД  {c['rd_value']}")
    clean = [{k: v for k, v in c.items() if not k.startswith("_")} for c in checks]
    result = sub.merge_checks(sub.skeleton(object_id), clean)
    out = paths.OUT / f"submission_{object_id}.json"
    paths.write_json(out, result)
    errors = sub.validate(result)
    print(f"\n{out}: ошибок проверки {len(errors)}")
    if object_id != paths.TEST_OBJECT:
        print()
        ev.report(ev.evaluate([result], paths.read_jsonl(paths.GOLD)))


if __name__ == "__main__":
    main(sys.argv[1:])
