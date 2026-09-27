"""Пути к данным и загрузчики. Всё остальное в ml/ берёт пути только отсюда.

Корень — папка над `ml/`, поэтому код работает и на ноутбуке, и в Docker без правок.
На стенде жюри документы и выход могут лежать в смонтированных папках: каждую
можно переопределить переменной окружения (`INSPECTOR_DOCS`, `INSPECTOR_DATA`,
`INSPECTOR_OUT`, `INSPECTOR_MODELS`, `INSPECTOR_ROOT`).
"""
import json
import os
from pathlib import Path


def _env_path(name, default):
    value = os.environ.get(name)
    return Path(value) if value else Path(default)


ROOT = _env_path("INSPECTOR_ROOT", Path(__file__).resolve().parents[1])
PACKAGE = ROOT / "01_ПАКЕТ" / "ХАКАТОН_УЧАСТНИКАМ_ГОТОВО_К_ПЕРЕДАЧЕ"
DOCS = _env_path("INSPECTOR_DOCS", PACKAGE / "01_ДОКУМЕНТАЦИЯ")
DATA = _env_path("INSPECTOR_DATA", PACKAGE / "02_ФОРМАТ_ДАННЫХ_И_ПРИМЕРЫ" / "data")
TRAIN_DATA = ROOT / "TRAIN_data"          # data/ из РАЗМЕЧЕННЫЙ_TRAIN_PUBLIC_203.zip (extract_safe.py)
OUT = _env_path("INSPECTOR_OUT", ROOT / "out")
MODELS = _env_path("INSPECTOR_MODELS", ROOT / "models")
# Кэш токенов страниц и OCR (ключ — SHA-256 файла). Отдельно от OUT: на стенде OUT — пустая смонтированная папка
# ответа, а готовый OCR сканов (PaddleOCR в Colab, CPU-стенд его не повторит за разумное время) лежит в образе
CACHE = _env_path("INSPECTOR_CACHE", OUT / "cache")

# Шрифты с кириллицей для PDF-карточек. В Docker: `apt-get install fonts-dejavu-core`.
_FONTS = {
    False: ("INSPECTOR_FONT", ["fonts/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                               "C:/Windows/Fonts/arial.ttf"]),
    True: ("INSPECTOR_FONT_BOLD", ["fonts/DejaVuSans-Bold.ttf",
                                   "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                                   "C:/Windows/Fonts/arialbd.ttf"]),
}


def font_file(bold=False):
    """Первый найденный шрифт: переменная окружения, папка fonts/ проекта, DejaVu в Linux, Arial в Windows."""
    env, candidates = _FONTS[bold]
    if os.environ.get(env):
        candidates = [os.environ[env]] + candidates
    for c in candidates:
        path = Path(c) if Path(c).is_absolute() else ROOT / c
        if path.exists():
            return str(path)
    raise FileNotFoundError(f"нет шрифта с кириллицей ({'жирный' if bold else 'обычный'}): "
                            f"задайте {env} или установите fonts-dejavu-core")

CATALOG = DATA / "parameter_catalog_132.jsonl"
MANIFEST = DATA / "document_manifest.jsonl"
SCHEMA = DATA / "submission_schema.json"
SPLIT = DATA / "split_policy.json"
GOLD = TRAIN_DATA / "public_gold_checks.jsonl"   # 15 проверок: 10 нарушений + 5 отрицательных

TEST_OBJECT = "OBJ-RECHNIKOV-7-7"


def read_jsonl(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
