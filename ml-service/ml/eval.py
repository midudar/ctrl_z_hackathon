"""Оценка файла ответа по размеченным проверкам (public_gold_checks.jsonl).

    python -m ml.eval out/submission_OBJ-TYUMENSKAYA-5-GOLD-SEED.json [ещё файлы...]

Считаем так, как описано в ТЗ 14.3 и scoring_summary:
- ключ совпадения: object_id + parameter_code + location (после нормализации);
- находка (TP) — эталонное нарушение, которое мы пометили VIOLATION_PRESENT;
- строгий TP — плюс верное доказательство: по каждой стадии эталона у нас есть тот же файл и страница;
- FP — наше VIOLATION_PRESENT, где эталон говорит NO_VIOLATION. Наши нарушения по ключам, которых
  в эталоне нет, считаются отдельно («без разметки»): эталон неполный, судить о них нельзя;
- FPR — доля эталонных NO_VIOLATION, которые мы назвали нарушением;
- критический пропуск — эталонное критическое нарушение без нашего VIOLATION_PRESENT
  (по правилам итог тогда ограничен 59 баллами).
"""
import sys
from collections import defaultdict

from ml import paths
from ml.location import location_key


def key(object_id, check):
    return (object_id, check["parameter_code"], location_key(check["parameter_code"], check.get("location")))


def evidence_ok(gold_ev, our_ev):
    ours = {(e["stage"], e["file_id"], e["pdf_page_number"]) for e in our_ev}
    by_stage = defaultdict(set)
    for e in gold_ev:
        by_stage[e["stage"]].add((e["stage"], e["file_id"], e["pdf_page_number"]))
    return all(pages & ours for pages in by_stage.values())


def safe_div(a, b):
    return a / b if b else 0.0


def evaluate(submissions, gold_rows):
    objects = {s["object_id"] for s in submissions}
    gold = {key(g["object_id"], g): g for g in gold_rows if g["object_id"] in objects}
    ours = {}
    for s in submissions:
        for c in s["checks"]:
            ours[key(s["object_id"], c)] = c

    tp = tp_strict = fp = fn = unlabeled = 0
    label_ok = label_total = 0
    loc_ok = loc_total = 0
    neg_total = neg_fp = 0
    critical_missed = []
    rows = []

    for k, g in gold.items():
        c = ours.get(k)
        our_label = c["violation_label"] if c else "—"
        if c:
            label_total += 1
            label_ok += our_label == g["violation_label"]
        if g["violation_label"] == "VIOLATION_PRESENT":
            hit = our_label == "VIOLATION_PRESENT"
            strict = hit and evidence_ok(g["evidence"], c["evidence"])
            tp += hit
            tp_strict += strict
            fn += not hit
            loc_total += 1
            loc_ok += bool(c) and evidence_ok(g["evidence"], c["evidence"])
            if not hit and (g.get("criticality") or "").startswith("Критич"):
                critical_missed.append(k)
            verdict = "TP" if strict else ("TP (доказательство неверно)" if hit else "FN")
        else:
            neg_total += 1
            is_fp = our_label == "VIOLATION_PRESENT"
            neg_fp += is_fp
            fp += is_fp
            verdict = "FP" if is_fp else "TN"
        rows.append((k, g["violation_label"], our_label, verdict))

    for k, c in ours.items():
        if k not in gold and c["violation_label"] == "VIOLATION_PRESENT":
            unlabeled += 1

    precision = safe_div(tp_strict, tp_strict + fp)
    recall = safe_div(tp_strict, tp_strict + fn)
    return {
        "gold_checks": len(gold),
        "tp_strict": tp_strict, "tp_key_only": tp - tp_strict, "fp": fp, "fn": fn,
        "precision": precision, "recall": recall,
        "f1": safe_div(2 * precision * recall, precision + recall),
        "fpr_on_negatives": safe_div(neg_fp, neg_total), "negatives": neg_total,
        "localization": safe_div(loc_ok, loc_total),
        "label_accuracy": safe_div(label_ok, label_total), "labels_compared": label_total,
        "violations_without_gold": unlabeled,
        "critical_missed": len(critical_missed),
        "rows": rows,
    }


def report(m):
    print(f"Эталонных проверок по этим объектам: {m['gold_checks']} (из них отрицательных {m['negatives']})")
    print(f"  TP {m['tp_strict']}  | TP без верного доказательства {m['tp_key_only']}  | FP {m['fp']}  | FN {m['fn']}")
    print(f"  Precision {m['precision']:.2f}   Recall {m['recall']:.2f}   F1 {m['f1']:.2f}")
    print(f"  FPR на отрицательных {m['fpr_on_negatives']:.2f}   Локализация (файл+страница) {m['localization']:.2f}")
    print(f"  Совпадение меток {m['label_accuracy']:.2f} на {m['labels_compared']} проверках с ответом")
    print(f"  Наших нарушений вне разметки: {m['violations_without_gold']}")
    if m["critical_missed"]:
        print(f"  ! Пропущено критических нарушений: {m['critical_missed']} → потолок 59/100")
    print()
    print(f"  {'объект':<28} {'параметр':<17} {'место':<42} {'эталон':<18} {'наш ответ':<22} итог")
    for (obj, code, loc), gl, ol, v in m["rows"]:
        print(f"  {obj:<28} {code:<17} {loc[:40]:<42} {gl:<18} {ol:<22} {v}")


def main(argv):
    if not argv:
        print(__doc__)
        return
    subs = [paths.read_json(p) for p in argv]
    if any(s["object_id"] == paths.TEST_OBJECT for s in subs):
        sys.exit("Тестовый объект не оцениваем: разметки нет, а подбор по нему запрещён.")
    report(evaluate(subs, paths.read_jsonl(paths.GOLD)))


if __name__ == "__main__":
    main(sys.argv[1:])
