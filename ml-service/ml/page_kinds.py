"""Тип страницы ИД по тексту её верхней части: бланк акта, реестр, документ о качестве, сертификат…

Источник текста — дешёвый проход OCR по полосам (`colab/make_strips.py` → `out/strips/results.jsonl`):
верхние 40% страницы при 120 dpi. Тип нужен, чтобы полный OCR делать только для бланков актов и реестров,
а не для всех 8 тысяч страниц ИД Речникова, и чтобы делить сшитые папки на отдельные акты.

Два правила, без которых не работает (замер 21.09 на Новослободской):
1. **Сравнение слов нечёткое.** В полосе OCR даёт «Объскт кяпитального строитсльства», «докумснтяции»,
   «качестне бетонной смеси». Точное совпадение не срабатывает, поэтому слова сравниваются с допуском.
2. **Бланк акта проверяется первым.** Его вторая страница сама перечисляет другие документы («Исполнительная
   схема…», «Протокол испытаний…», «Реестр приложений №1»), и по ним страница ошибочно уезжала в схему,
   протокол или реестр.

Правила — по стандартным заголовкам документов ИД (бланк АОСР по приказу Минстроя, документ о качестве
бетонной смеси по ГОСТ 7473), а не по тестовому объекту. Точность проверяется на Новослободской: там
у бланков и реестров есть текстовый слой, тип по нему — эталон для типа по распознанной полосе.

    python -m ml.page_kinds validate                  # точность на Новослободской
    python -m ml.page_kinds OBJ-RECHNIKOV-7-7         # сколько страниц каждого типа
"""
import json
import re
import sys
from collections import Counter, defaultdict
from difflib import SequenceMatcher

import pymupdf

from ml import paths
from ml.pages import file_path

STRIPS = paths.OUT / "strips" / "results.jsonl"
STRIP_FRAC = 0.40             # как в colab/make_strips.py
FUZZ = 0.78                   # порог похожести слова: «выполнсны» ≈ «выполнены» (0.89)

# OCR путает похожие буквы латиницы и кириллицы: сводим всё к кириллице
_LAT2CYR = str.maketrans("ABCEHKMOPTXYabcehkmopxyё", "АВСЕНКМОРТХУавсенкморхуе")


def normalize(text):
    return re.sub(r"\s+", " ", text.translate(_LAT2CYR).lower()).strip()


def _words(text):
    return [w for w in re.findall(r"[а-яa-z]{4,}", normalize(text))]


def _has(words, target, thr=FUZZ):
    """Есть ли в тексте слово, похожее на target (допуск на ошибки распознавания)."""
    for w in words:
        if abs(len(w) - len(target)) <= 3 and SequenceMatcher(None, w, target).ratio() >= thr:
            return True
    return False


def _all(words, *targets):
    return all(_has(words, t) for t in targets)


def _phrase(words, *targets, gap=2):
    """Слова идут подряд (допускается gap чужих слов между ними) и в нужном порядке.

    Без этого сертификат соответствия («…на выполнение работ по оценке соответствия…») считался бланком
    акта: слова «выполнении» и «работ» есть и там, просто в другой фразе.
    """
    pos = -1
    for t in targets:
        found = -1
        for i in range(pos + 1, len(words)):
            if pos >= 0 and i - pos > gap + 1:
                break
            if _has([words[i]], t):
                found = i
                break
        if found < 0:
            return False
        pos = found
    return True


# Признаки бланка АОСР: либо сильный (слово из заголовка), либо два слабых — поля бланка.
ACT_WEAK = (("объект", "капитального", "строительства"), ("застройщик",), ("технический", "заказчик"),
            ("осуществляющее", "строительство"), ("работы", "выполнены", "проектной"),
            ("предъявлены", "документы"), ("разрешается", "производство"), ("дополнительные", "сведения"),
            ("начала", "работ"), ("окончания", "работ"), ("работ", "применены"))


def _is_act(words):
    if _has(words, "освидетельствования") or _has(words, "освидетельствованию"):
        return True
    return sum(_phrase(words, *anchor) for anchor in ACT_WEAK) >= 2


# Порядок важен: бланк акта — первым (см. правило 2 в описании модуля).
RULES = [
    ("act", _is_act),
    ("register", lambda w: _has(w, "реестр")),
    # именно документ о качестве бетонной смеси: у металла свой «сертификат качества», он нам не нужен
    ("quality", lambda w: (_all(w, "качестве", "смеси") or _all(w, "качества", "смеси")
                           or _all(w, "паспорт", "бетонной"))),
    ("protocol", lambda w: _all(w, "протокол", "испытаний")),
    ("certificate", lambda w: _has(w, "сертификат") or _all(w, "декларация", "соответствии")),
    ("scheme", lambda w: _all(w, "исполнительная", "схема") or _all(w, "геодезическая", "схема")),
]


def kind_of(text):
    words = _words(text)
    for kind, rule in RULES:
        if rule(words):
            return kind
    return "other" if len(words) >= 5 else "empty"


def load_strips():
    """{(file_id, page): текст полосы} из результатов Colab. Токены — сверху вниз, слева направо."""
    out = {}
    with open(STRIPS, encoding="utf-8") as f:
        for line in f:
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue            # последняя строка обрывается, если ячейку в Colab прервали на записи
            toks = sorted(r.get("tokens", []), key=lambda t: (round(t[2] / 12), t[1]))
            out[(r["file_id"], r["page"])] = " ".join(t[0] for t in toks)
    return out


def text_layer_kind(page):
    """Эталон: тип по текстовому слою той же верхней части страницы (если слой есть)."""
    r = page.rect
    text = page.get_text(clip=pymupdf.Rect(r.x0, r.y0, r.x1, r.y0 + r.height * STRIP_FRAC))
    return kind_of(text) if len(text.strip()) >= 80 else None


def validate(object_id="OBJ-NOVOSLOBODSKAYA"):
    """Сверка типа по OCR полосы с типом по текстовому слою на страницах, где слой есть."""
    if object_id == paths.TEST_OBJECT:
        raise SystemExit("Тестовый объект для проверки правил не используем.")
    strips = load_strips()
    manifest = {r["file_id"]: r for r in paths.read_jsonl(paths.MANIFEST)}
    confusion, wrong = Counter(), []
    for fid in sorted({fid for fid, _ in strips}):
        if manifest[fid]["object_id"] != object_id:
            continue
        with pymupdf.open(file_path(fid)) as doc:
            for pno, page in enumerate(doc, start=1):
                truth = text_layer_kind(page)
                if truth is None or (fid, pno) not in strips:
                    continue
                got = kind_of(strips[(fid, pno)])
                confusion[(truth, got)] += 1
                if truth != got and len(wrong) < 12:
                    wrong.append((fid, pno, truth, got, strips[(fid, pno)][:110]))
    total = sum(confusion.values())
    ok = sum(n for (t, g), n in confusion.items() if t == g)
    print(f"страниц с эталоном: {total}, тип совпал: {ok} ({ok / max(total, 1):.2f})\n")
    kinds = sorted({t for t, _ in confusion} | {g for _, g in confusion})
    for t in kinds:
        row = {g: confusion[(t, g)] for g in kinds if confusion[(t, g)]}
        if row:
            print(f"  эталон {t:12} {sum(row.values()):>4}: {row}")
    if wrong:
        print("\nпримеры расхождений:")
        for fid, pno, t, g, txt in wrong:
            print(f"  {fid} стр.{pno}: эталон {t}, по полосе {g} | {txt}")


def summary(object_id):
    strips = load_strips()
    manifest = {r["file_id"]: r for r in paths.read_jsonl(paths.MANIFEST)}
    per_kind, per_file = Counter(), defaultdict(Counter)
    for (fid, pno), text in strips.items():
        if manifest[fid]["object_id"] != object_id:
            continue
        k = kind_of(text)
        per_kind[k] += 1
        per_file[fid][k] += 1
    print(f"{object_id}: страниц {sum(per_kind.values())}")
    for k, n in per_kind.most_common():
        print(f"  {k:12} {n}")
    print()
    for fid in sorted(per_file):
        c = per_file[fid]
        print(f"  {fid} актов {c['act']:>3}  реестров {c['register']:>3}  о качестве {c['quality']:>3}  "
              f"{manifest[fid]['relative_path'].split('/')[-1][:60]}")


def main(argv):
    if argv and argv[0] == "validate":
        validate(*(argv[1:2] or []))
    else:
        summary(argv[0] if argv else "OBJ-NOVOSLOBODSKAYA")


if __name__ == "__main__":
    main(sys.argv[1:])
