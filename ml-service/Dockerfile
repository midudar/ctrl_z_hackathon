# Инспектор ИИ — ML-часть. Сборка образа для стенда жюри.
#
#   docker build -t inspector-ml .
#   docker run --gpus all \
#     -v /path/to/01_ДОКУМЕНТАЦИЯ:/input/docs:ro \
#     -v /path/to/data:/input/data:ro \
#     -v /path/to/output:/output \
#     inspector-ml OBJ-RECHNIKOV-7-7
#
# /input/docs — папка документов (01_ДОКУМЕНТАЦИЯ из пакета участника, пути как в реестре);
# /input/data — document_manifest.jsonl, parameter_catalog_132.jsonl, submission_schema.json (02_ФОРМАТ…/data);
# /output     — сюда пишутся submission_<объект>.json и integrity_<объект>.json.
#
# OCR во время прогона не запускается: распознанный текст сканов ИД (PaddleOCR на GPU, 910 страниц Речникова) и
# листов ОВ (EasyOCR) лежит в образе — кэш /app/cache, ключ — SHA-256 файла. Страницы без кэша читаются только
# текстовым слоем. Базовый образ несёт CUDA runtime и torch; драйвер NVIDIA в образ класть нельзя, он с хоста.
# Контекст сборки ограничен .dockerignore (в папке проекта ~42 ГБ данных).
FROM pytorch/pytorch:2.4.1-cuda12.1-cudnn9-runtime

# fonts-dejavu-core — кириллица в PDF-карточках доказательств (ml/paths.py: font_file)
# libarchive-tools — bsdtar: список файлов внутри архивов .7z и .rar для отчёта целостности (ml/integrity.py);
#   без него временные файлы внутри таких архивов (у Речникова — F0339 .rar, F0412 .7z) не находятся
RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-dejavu-core libarchive-tools \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY ml/ ml/
# Модели EasyOCR и кэш распознанных страниц — из архива inspector_ml_assets.zip (GitHub Releases). Шаблоны «model[s]/easyoc[r]»,
# «ou[t]/cach[e]» делают их необязательными (шаблон нужен на каждой части пути: без архива в контексте сборки нет
# и самой папки out/): образ собирается, но сканы читаются только текстовым слоем
RUN mkdir -p models/easyocr cache
COPY model[s]/easyoc[r] models/easyocr/
COPY ou[t]/cach[e] cache/

ENV PYTHONIOENCODING=utf-8 \
    PYTHONUNBUFFERED=1 \
    INSPECTOR_ROOT=/app \
    INSPECTOR_DOCS=/input/docs \
    INSPECTOR_DATA=/input/data \
    INSPECTOR_OUT=/output \
    INSPECTOR_CACHE=/app/cache

# Аргумент — код объекта (без него обрабатываются все объекты из реестра)
ENTRYPOINT ["python", "-m", "ml.run"]
