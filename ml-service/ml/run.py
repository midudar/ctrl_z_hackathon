"""Единая точка входа: папка объекта → файл ответа по всем 132 параметрам.

    python -m ml.run OBJ-NOVOSLOBODSKAYA
    python -m ml.run OBJ-RECHNIKOV-7-7 --out /output/submission.json

Так же запускается образ на стенде жюри (см. Dockerfile). Пути к документам и данным берутся из
переменных окружения `INSPECTOR_DOCS`, `INSPECTOR_DATA`, `INSPECTOR_OUT` (см. `ml/paths.py`).

Треки независимы: если один падает, остальные всё равно отрабатывают, а в ответе на их месте
остаются заглушки скелета. Это важно на стенде: лучше неполный ответ, чем никакого.
"""
import argparse
import sys
import time
import traceback
from pathlib import Path

from ml import paths, registry, submission


def _tracks(object_id):
    """(название, функция) — каждая возвращает список проверок в формате submission."""
    from ml import concrete, doors, electric, explication, fire, pipes, rooms, steel

    def kr():
        return concrete.compare(object_id)          # КР + ИД (acts.merge_into внутри)

    def ov():
        if object_id not in rooms.DRAWING_FILES:
            print("  трек ОВ: чертежи этого объекта не заданы, пропускаю")
            return []
        checks, _, _ = rooms.compare(object_id)     # ещё возвращает помещения по стадиям — для отладки
        # поля на «_» — внутренние (трассировка привязки), в ответ они не идут
        return [{k: v for k, v in c.items() if not k.startswith("_")} for c in checks]

    return [("КР и ИД", kr), ("ОВ", ov),
            ("пожарные характеристики", lambda: fire.compare(object_id)),    # PZ-022, PZ-023
            ("экспликации помещений", lambda: explication.compare(object_id)),   # PZ-003 по помещениям
            ("сталь металлопроката", lambda: steel.compare(object_id)),          # KR-056
            ("канализационные трубы", lambda: pipes.compare(object_id)),         # IOS3-075
            ("электрика: кабели, свет, мощность", lambda: electric.compare(object_id)),  # PPM-109, IOS1-069, ZU-130, PZ-014
            ("огнестойкость дверей", lambda: doors.compare(object_id))]          # PPM-103


def run(object_id, out_path=None):
    t0 = time.time()
    result = submission.skeleton(object_id)
    for name, track in _tracks(object_id):
        t = time.time()
        try:
            checks = track()
            result = submission.merge_checks(result, checks)
            print(f"  трек {name}: {len(checks)} проверок за {time.time() - t:.0f} с")
        except Exception:                            # один сломанный трек не должен ронять прогон
            print(f"  трек {name}: ОШИБКА за {time.time() - t:.0f} с", file=sys.stderr)
            traceback.print_exc()

    # Рамки доказательств (ТЗ 9.1): к каждой ссылке «файл + страница» — bbox_norm в долях видимой страницы
    t = time.time()
    try:
        from ml import evidence_boxes
        found, total = evidence_boxes.annotate(result)
        print(f"  рамки доказательств: {found} из {total} за {time.time() - t:.0f} с")
    except Exception:
        print(f"  рамки доказательств: ОШИБКА за {time.time() - t:.0f} с", file=sys.stderr)
        traceback.print_exc()

    out = paths.OUT / f"submission_{object_id}.json" if out_path is None else out_path
    # Целостность комплекта (пустые и битые файлы, дубли, нет дисциплины в РД, редакции…): полный отчёт —
    # рядом с ответом, краткий — в ответ ключом document_integrity (схема ответа лишние ключи допускает).
    t = time.time()
    try:
        from ml import integrity
        report = integrity.check(object_id)
        paths.write_json(Path(out).parent / f"integrity_{object_id}.json", report)
        result["document_integrity"] = integrity.compact(report)
        print(f"  целостность комплекта: дефектов {len(report['findings'])} за {time.time() - t:.0f} с")
    except Exception:
        print(f"  целостность комплекта: ОШИБКА за {time.time() - t:.0f} с", file=sys.stderr)
        traceback.print_exc()

    errors = submission.validate(result)
    for e in errors:
        print(f"  ошибка ответа: {e}", file=sys.stderr)
    paths.write_json(out, result)

    labels = {}
    for c in result["checks"]:
        labels[c["violation_label"]] = labels.get(c["violation_label"], 0) + 1
    print(f"{out}: строк {len(result['checks'])}, ошибок проверки {len(errors)}, "
          f"за {time.time() - t0:.0f} с")
    for label, n in sorted(labels.items(), key=lambda kv: -kv[1]):
        print(f"  {label:22} {n}")
    return result, errors


def objects():
    """Объекты, которые есть в реестре документов."""
    return sorted({r["object_id"] for r in paths.read_jsonl(paths.MANIFEST)})


def main(argv):
    ap = argparse.ArgumentParser(description="Инспектор ИИ: сравнение стадий документации")
    ap.add_argument("object_id", nargs="?", help="код объекта; без него — все объекты реестра")
    ap.add_argument("--out", help="куда положить файл ответа (по умолчанию out/submission_<объект>.json)")
    args = ap.parse_args(argv)

    ids = [args.object_id] if args.object_id else objects()
    if args.object_id and args.object_id not in objects():
        raise SystemExit(f"объекта {args.object_id} нет в реестре; есть: {', '.join(objects())}")
    if args.out and len(ids) > 1:
        raise SystemExit("--out можно указывать только для одного объекта")

    worst = 0
    for object_id in ids:
        print(f"\n=== {object_id}")
        for stage in ("PD", "RD", "ID"):
            n = sum(len(registry.files(object_id, stage, d)) for d in ("KR", "OV", "AR", "PZ"))
            print(f"  {stage}: файлов по дисциплинам {n}")
        _, errors = run(object_id, args.out)
        worst = max(worst, len(errors))
    return 1 if worst else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
