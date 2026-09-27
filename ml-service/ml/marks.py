"""Марки инженерных систем на чертежах ОВ: нормализация OCR-текста и разбор списков.

«B2.7,8,9» → В2.7, В2.8, В2.9;  «В2.4-В2.6» → В2.4, В2.5, В2.6;  «П2/ВЕ» → П2, ВЕ;
«В1О:» → В10;  «ПZВЕ» → П2, ВЕ.  Коды решёток вроде «П03.162» и размеры «П300*300» отбрасываются.
"""
import re

_LAT2CYR = str.maketrans("ABCEHKMOPTXY", "АВСЕНКМОРТХУ")
# Буквы, которые OCR ставит вместо цифр внутри номера
_DIGITLIKE = str.maketrans({"О": "0", "O": "0", "З": "3", "Z": "2", "Б": "6", "I": "1", "L": "1", "|": "1", "Т": "7"})

PREFIXES = ("ПЕ", "ВЕ", "ПД", "ДУ", "П", "В")          # порядок важен: сначала двухбуквенные
_MARK = re.compile(r"^(ПЕ|ВЕ|ПД|ДУ|П|В)(\d{1,2}(?:\.\d{1,2})?)?$")


def _fix_digits(s):
    """Внутри числовой части меняем похожие на цифры буквы: «1О» → «10», «1Z.2» → «12.2»."""
    m = re.match(r"^(ПЕ|ВЕ|ПД|ДУ|П|В)(.*)$", s)
    if not m:
        return s
    head, tail = m.groups()
    if tail and (tail[0].isdigit() or tail[0] in "ОOЗZБIL|Т"):
        tail = tail.translate(_DIGITLIKE)
    return head + tail


def _expand(part):
    """Одна «словоформа» → список марок."""
    part = part.strip(" .:;,]|[()")
    if not part:
        return []
    # диапазон В2.4-В2.6 или В2.4-6
    m = re.match(r"^(ПЕ|ВЕ|ПД|ДУ|П|В)(\d{1,2})\.(\d{1,2})\s*[-–]\s*(?:\1\2\.)?(\d{1,2})$", part)
    if m:
        pre, sys_no, a, b = m.groups()
        a, b = int(a), int(b)
        if 0 < b - a <= 20:
            return [f"{pre}{sys_no}.{i}" for i in range(a, b + 1)]
    # перечисление В2.7,8,9
    m = re.match(r"^(ПЕ|ВЕ|ПД|ДУ|П|В)(\d{1,2})\.(\d{1,2}(?:\s*,\s*\d{1,2})+)$", part)
    if m:
        pre, sys_no, rest = m.groups()
        return [f"{pre}{sys_no}.{int(x)}" for x in re.split(r"\s*,\s*", rest)]
    part = _fix_digits(part)
    if re.match(r"^П0\d", part) or "*" in part or "Х" in part[1:]:
        return []                      # коды решёток П03.162, размеры
    m = _MARK.match(part)
    if not m:
        return []
    num = m.group(2)
    if not num and m.group(1) not in ("ВЕ", "ПЕ"):
        return []                      # голые «П» / «В» — шум
    if num and len(num.split(".")[0]) == 2 and num.startswith("0"):
        return []
    return [part]


def parse_marks(text):
    """Все марки систем из строки токена."""
    s = text.upper().translate(_LAT2CYR)
    s = re.sub(r"\s*([,\-–])\s*", r"\1", s)          # «В2.7, 8» → «В2.7,8»
    out = []
    for word in s.split():
        # «ПZВЕ» = «П2/ВЕ», где «/» распознан как «Z»
        word = re.sub(r"^П(\d*)Z(ВЕ|В\d)", lambda m: f"П{m.group(1) or '2'}/{m.group(2)}", word)
        for part in re.split(r"[/\\]", word):
            out.extend(_expand(part))
    return out


def system_of(mark):
    """В2.7 → В2; ВЕ13 → ВЕ; П2.1 → П2. ВЕ/ПЕ с номером — это отдельные вентиляторы естественной
    вытяжки одной системы, поэтому схлопываются до ВЕ/ПЕ."""
    m = _MARK.match(mark)
    if not m:
        return mark
    pre, num = m.groups()
    if pre in ("ВЕ", "ПЕ") or not num:
        return pre
    return pre + num.split(".")[0]


if __name__ == "__main__":
    for s in ["B2.7,8,9", "В2.4-В2.6", "П2/ВЕ", "В1О:", "AMP_K 400х350 ПZВЕ", "П03.162", "П12/В12.1|",
              "ВЕ13", "П300*300", "В2.3,4", "П1З", "B2 2", "ПД1О"]:
        print(f"{s!r:>24} → {parse_marks(s)}  системы {[system_of(m) for m in parse_marks(s)]}")
