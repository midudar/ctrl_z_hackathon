"""Формат поля location: тип места для каждого параметра и нормализация строки.

Скоринг сопоставляет ответы по object_id + parameter_code + location, поэтому все экстракторы
обязаны прогонять location через normalize_location().

Типы из эталона (public_gold_checks.jsonl): ROOM («012») и CONSTRUCTION_ELEMENT («Фундаментная плита»).
Остальные типы — наше предположение по названиям параметров, их нужно подтвердить
(пример протокола из ТЗ показывает параметры без места, т.е. на объект целиком).
"""
import re
import unicodedata

ROOM = "ROOM"                                   # номер помещения: 012, 140
CONSTRUCTION_ELEMENT = "CONSTRUCTION_ELEMENT"   # конструкция: «Фундаментная плита», «Отметка 0.000»
SYSTEM = "SYSTEM"                               # инженерная система: «П2.1», «В1», «К2»   (предположение)
OBJECT = "OBJECT"                               # объект целиком                           (предположение)

OBJECT_LOCATION = "Объект"   # строка-заглушка для параметров уровня объекта (предположение)

_BY_CODE = {
    # подтверждено эталоном
    "PZ-009": CONSTRUCTION_ELEMENT,
    "IOS4-078": ROOM,
    "IOS4-079": ROOM,
    # помещения и пути эвакуации / МГН
    **{c: ROOM for c in ["AR-040", "AR-041", "AR-042", "AR-043", "AR-047", "AR-050", "AR-051",
                         "PPM-104", "PPM-105", "PPM-106", "PPM-107", "PPM-108", "PPM-110",
                         "ODI-116", "ODI-117", "ODI-118", "ODI-119", "ODI-120", "ODI-122",
                         "IOS4-077"]},
    # конструкции
    **{c: CONSTRUCTION_ELEMENT for c in ["AR-044", "AR-045", "AR-048", "AR-049",
                                         "KR-054", "KR-055", "KR-056", "KR-057", "KR-058", "KR-059",
                                         "KR-060", "KR-061", "KR-062", "KR-063", "KR-064", "KR-065",
                                         "KR-066", "ZU-125", "ZU-128", "PPM-103", "PPM-111"]},
    # инженерные системы
    **{c: SYSTEM for c in ["IOS1-068", "IOS1-069", "IOS1-070", "IOS2-071", "IOS2-072", "IOS2-073",
                           "IOS3-074", "IOS3-075", "IOS4-076", "IOS5-080", "PPM-109", "PPM-112",
                           "PPM-113"]},
}

# Эталонный код свободного поиска вне матрицы
_BY_CODE["FREE-HEATING-001"] = ROOM


def location_type(parameter_code):
    return _BY_CODE.get(parameter_code, OBJECT)


# Латинские буквы, которые OCR и CAD путают с кириллицей: приводим к кириллице.
_LAT2CYR = str.maketrans("ABCEHKMOPTXaceopxy", "АВСЕНКМОРТХасеорху")


def _clean(text):
    text = unicodedata.normalize("NFC", str(text))
    text = text.replace(" ", " ").replace("ё", "е").replace("Ё", "Е")
    return re.sub(r"\s+", " ", text).strip()


def normalize_room(raw):
    """«пом. 12», «№012», «012» → «012». Буквенный суффикс сохраняется: «12а» → «012а»."""
    s = _clean(raw).lower()
    s = re.sub(r"^(пом(ещение)?\.?|№)\s*", "", s)
    m = re.fullmatch(r"(\d+)\s*([а-яa-z]?)", s)
    if not m:
        return _clean(raw)
    return m.group(1).zfill(3) + m.group(2).translate(_LAT2CYR)


def normalize_system(raw):
    """«B2» (латиница) → «В2», «П 2.1» → «П2.1»."""
    s = _clean(raw).upper().translate(_LAT2CYR)
    return re.sub(r"\s+", "", s)


def normalize_location(parameter_code, raw):
    kind = location_type(parameter_code)
    if raw is None or _clean(raw) == "":
        return OBJECT_LOCATION if kind == OBJECT else ""
    if kind == ROOM:
        return normalize_room(raw)
    # марка системы («К1», «B2», «П 2.1») — к виду марки; описательное место («Системы противопожарной защиты») — как текст
    if kind == SYSTEM and re.fullmatch(r"[A-Za-zА-Яа-яЁё]{1,5}\s?[\d.,/\-]*[A-Za-zА-Яа-я]?", _clean(raw)):
        return normalize_system(raw)
    s = _clean(raw)
    return s[:1].upper() + s[1:]


def location_key(parameter_code, raw):
    """Ключ для сравнения в eval: нормализованная строка без регистра."""
    return normalize_location(parameter_code, raw).casefold()
