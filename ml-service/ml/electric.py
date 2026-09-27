"""ЭОМ: кабели (PPM-109, IOS1-069), источники света (ZU-130) и мощность объекта (PZ-014) — ПД (ИОС1) ↔ РД (ЭОМ / ЭМ / ЭО).

Мощность (PZ-014, триггер «превышение расчётной мощности в РД над лимитом ТУ из ПД»): только мощность объекта целиком
(«Расчётная мощность по объекту составляет: 523,3 кВт») и лимит по ТУ («максимальная / разрешённая мощность»). На пилоте
в РД ЭОМ общей мощности нет нигде — только нагрузки щитов, их между стадиями не сопоставить; тогда «сравнить нельзя»
со значением и страницей ПД. «Нарушение» — только против явного лимита по ТУ.

Кабели. Из текста берётся исполнение по ГОСТ 31565 — «нг(А)», «нг(А)-LS», «нг(А)-FRLS», «нг(А)-FRLSLTx», «нг(А)-HF»,
«нг(А)-FRHF»: признаки FR (огнестойкий), LS / HF (низкое дымовыделение / без галогенов), LTx (низкая токсичность).
- PPM-109 «Пожарная маркировка кабелей систем ПЗ», триггер «применение кабеля без индекса огнестойкости (замена FRLS на
  обычный LS)»: в ПД есть огнестойкие кабели (для систем противопожарной защиты их требует СП 6.13130), в РД огнестойких
  нет вовсе, а другие кабели есть → «нарушение»; огнестойкие есть на обеих стадиях → «нет нарушения».
- IOS1-069 «Сечение жил и маркировка распределительных кабелей»: какие признаки исполнения есть в ПД и в РД. Сечения
  между стадиями не сопоставить (линии ПД и РД не связаны), поэтому «нарушения» здесь не ставится: все признаки ПД есть
  в РД → «нет нарушения», какого-то нет → «сравнить нельзя» с пояснением.

Источники света (ZU-130, триггер «замена светодиодных светильников на люминесцентные или лампы накаливания»): классы
LED, люминесцентные, накаливания, разрядные (ДРЛ, ДНаТ, МГЛ). «Нарушение» — в ПД светодиодные, в РД светодиодных нет
вовсе, а люминесцентные или накаливания есть; в РД только классы, которые были в ПД, и LED остался → «нет нарушения»;
в РД появились люминесцентные / накаливания рядом с LED → «сравнить нельзя» с цитатой. Отбрасываются подписи осей («ЛЛ»),
характеристики датчиков и выключателей («нагрузка до 2000 Вт (лампы накаливания)»), запреты («не допускается»),
«фотолюминесцентные» знаки и «безгалогенные» кабели.

Разрабатывалось на объектах пилота (РД ЭОМ у СОШ, ДОО, Полярной 16 и 17, Лосевской); у обучающих объектов РД ЭОМ нет.

    python -m ml.electric <object_id>
"""
import re
import sys

import pymupdf

from ml import registry
from ml.concrete import context_of, page_lines
from ml.pages import file_path

FIRE_CODE, CABLE_CODE, LIGHT_CODE = "PPM-109", "IOS1-069", "ZU-130"
FIRE_LOC, CABLE_LOC, LIGHT_LOC = "Системы противопожарной защиты", "Распределительные сети", "Освещение"

# исполнение кабеля: «нг(А)», «нг(А)-FRLS», «нг(A)-LSLTx», «-нг(А)-FRHF», «нг-LS» (старое обозначение без категории)
EXEC_RE = re.compile(r"нг\s?(?P<cat>\(\s?[АA][^)]{0,6}\))?\s?(?:[-–]\s?(?P<idx>(?:FR)?(?:LS|HF)(?:\s?LTx)?))?(?![A-Za-z])",
                     re.I)
FIRE_RE = re.compile(r"противопожарн\w*|пожарн\w*|СПЗ|АУПС|АУПТ|СОУЭ|дымоудален\w*|подпор\w*|огнестойк\w*|"
                     r"аварийн\w*\s+(?:эвакуацион\w*\s+)?освещ\w*|эвакуацион\w*\s+освещ\w*|\bППУ\b|ППЗ", re.I)
ATTR_ORDER = ["нг(А)", "FR", "LS", "HF", "LTx"]
ATTR_NAME = {"FR": "огнестойкие (FR)", "LS": "низкое дымовыделение (LS)", "HF": "без галогенов (HF)",
             "LTx": "низкая токсичность (LTx)", "нг(А)": "не распространяющие горение (нг(А))"}

LIGHTS = [
    ("LED", re.compile(r"светодиод\w*|(?<![A-Za-z])LED(?![A-Za-z])", re.I)),
    ("люминесцентные", re.compile(r"(?<!фото)люминесцентн\w*|(?<![А-Яа-я])КЛЛ(?![А-Яа-я])", re.I)),
    ("накаливания", re.compile(r"накаливани\w*|(?<![А-Яа-я])ЛН\s?-?\s?\d{2,3}|(?<!без)галогенн\w*\s+ламп\w*", re.I)),
    ("разрядные", re.compile(r"(?<![А-Яа-я])(?:ДРЛ|ДНаТ|ДРИ|МГЛ)(?![А-Яа-я])")),
]
OLD_LIGHT = {"люминесцентные", "накаливания"}
LIGHT_CTX_RE = re.compile(r"светильник\w*|освещ\w*|ламп\w*|светодиод\w*|(?<![A-Za-z])LED(?![A-Za-z])|прожектор\w*", re.I)
# характеристики датчиков и выключателей, запреты, экраны и табло — не источники света объекта
LIGHT_SKIP_RE = re.compile(r"нагрузк\w*|коммутацион\w*|датчик\w*|выключател\w*|не\s+допуска\w*|запрещ\w*|"
                           r"не\s+применя\w*|вместо|замен\w*|фотолюминесц\w*|экран\w*|монитор\w*|табло|дисплей\w*|"
                           r"разрешени\w*\s+\d", re.I)

# PZ-014 «Расчётная электрическая мощность», триггер — «превышение расчётной мощности в РД над лимитом ТУ, выданным в ПД».
# Берётся только мощность объекта целиком («Расчётная мощность по объекту составляет: 523,3 кВт», «Расчётные нагрузки
# потребителя объекта составляют: - жилой дом Рр = 717,9 кВт») и лимит по ТУ («максимальная / разрешённая мощность»);
# нагрузки отдельных щитов, квартир и насосов — нет: их между стадиями не сопоставить.
POWER_CODE, POWER_LOC = "PZ-014", "Объект"
POWER_TOTAL_RE = re.compile(r"расч[её]тн\w*\s+(?:электрическ\w*\s+)?(?:мощност\w*|нагрузк\w*)\s+"
                            r"(?:по\s+объекту|объекта|здания|комплекса|потребител\w*(?:\s+объекта)?)", re.I)
POWER_LIMIT_RE = re.compile(r"(?:максимальн\w*|разреш[её]нн\w*)\s+(?:к\s+(?:использованию|присоединению)\s+)?"
                            r"(?:электрическ\w*\s+)?мощност\w*", re.I)
KW_RE = re.compile(r"(\d{1,5}(?:[  ]\d{3})*(?:[.,]\d+)?)\s*кВт(?!\s*[·*]?\s*ч)", re.I)
PART_RE = re.compile(r"щит\w*|(?<![А-Яа-я])(?:ЩР|ЩС|ЩО|ЩУ|ВРУ|ВРЩ|ГРЩ|ЩАО)|панел\w*|квартир\w*|категори\w*|насос\w*|"
                     r"лифт\w*|секци\w*|помещени\w*|групп\w*", re.I)


def eom_files(object_id, stage):
    return registry.files(object_id, stage, "EOM")


def _attrs(idx):
    a = {"нг(А)"}
    idx = (idx or "").upper()
    if idx.startswith("FR"):
        a.add("FR")
    if "LS" in idx:
        a.add("LS")
    if "HF" in idx:
        a.add("HF")
    if "LTX" in idx:
        a.add("LTx")
    return a


def _exec_name(idx):
    if not idx:
        return "нг(А)"
    return "нг(А)-" + re.sub(r"\s", "", idx).upper().replace("LTX", "LTx")


def extract(file_id):
    """({кабели: [{exec, attrs, fire, …}]}, {свет: [{kind, …}]}) по текстовому слою файла."""
    cables, lights = [], []
    try:
        doc = pymupdf.open(file_path(file_id))
    except Exception:
        return cables, lights
    for pno, page in enumerate(doc, 1):
        txt = page.get_text()
        has_cable = "нг" in txt
        has_light = any(rx.search(txt) for _, rx in LIGHTS)
        if not has_cable and not has_light:
            continue
        lines = page_lines(page)
        for i, (text, bbox) in enumerate(lines):
            base = {"file_id": file_id, "page": pno, "line": text.strip()[:160], "bbox": list(bbox)}
            if has_cable:
                for m in EXEC_RE.finditer(text):
                    if not (m.group("cat") or m.group("idx")):
                        continue                        # голое «нг» — не исполнение кабеля
                    window = context_of(lines, i, radius=1)
                    cables.append({**base, "exec": _exec_name(m.group("idx")), "attrs": _attrs(m.group("idx")),
                                   "fire": bool(FIRE_RE.search(text) or FIRE_RE.search(window))})
            if has_light:
                kinds = [k for k, rx in LIGHTS if rx.search(text)]
                if not kinds:
                    continue
                # «Коммутационная нагрузка / до 2000 Вт (лампы накаливания) / до 1000 Вт (люминесцентные лампы, LED)» — датчик
                near = " ".join(lines[j][0] for j in range(max(0, i - 2), min(len(lines), i + 2)))
                if LIGHT_SKIP_RE.search(near):
                    continue
                if not LIGHT_CTX_RE.search(context_of(lines, i, radius=2)):
                    continue
                for k in kinds:
                    lights.append({**base, "kind": k})
    return cables, lights


def stage_records(object_id, stage):
    cables, lights = [], []
    for fid in eom_files(object_id, stage):
        c, l = extract(fid)
        cables += c
        lights += l
    return cables, lights


def _ev(stage, recs, key=lambda r: 0):
    r = min(recs, key=key)
    ev = {"stage": stage, "file_id": r["file_id"], "pdf_page_number": r["page"]}
    try:
        from ml.evidence_boxes import text_box
        ev.update(text_box(r["file_id"], r["page"], r["bbox"], r["line"]))
    except Exception:
        pass
    return ev


def _execs(recs):
    order = ["нг(А)", "нг(А)-LS", "нг(А)-LSLTx", "нг(А)-HF", "нг(А)-HFLTx", "нг(А)-FRLS", "нг(А)-FRLSLTx", "нг(А)-FRHF",
             "нг(А)-FRHFLTx"]
    return ", ".join(sorted({r["exec"] for r in recs}, key=lambda e: order.index(e) if e in order else 99))


def _missing(code, loc, stage_missing, other_stage, other, files_exist, what):
    """Строка, когда на одной стадии значений нет: нет комплекта → «нет документа», есть, но без текста → «сравнить нельзя»."""
    st = "РД" if stage_missing == "RD" else "ПД"
    return {"parameter_code": code, "location": loc,
            "violation_label": "COMPARISON_IMPOSSIBLE" if files_exist else "MISSING_DOCUMENT",
            "pd_value": what(other) if other_stage == "PD" else None,
            "rd_value": what(other) if other_stage == "RD" else None,
            "evidence": [_ev(other_stage, other)],
            "note": (f"в {st} комплект ЭОМ есть, но в тексте это не найдено (сканы или только чертежи)" if files_exist
                     else f"в {st} нет комплекта ЭОМ / ИОС1")}


def cable_checks(object_id, pd, rd):
    if not pd and not rd:
        return []
    for stage, have, other_stage, other in (("RD", rd, "PD", pd), ("PD", pd, "RD", rd)):
        if not have:
            files = bool(eom_files(object_id, stage))
            fr = [r for r in other if "FR" in r["attrs"]]
            return [_missing(FIRE_CODE, FIRE_LOC, stage, other_stage, fr or other, files, lambda rs: _execs(rs) if fr else None),
                    _missing(CABLE_CODE, CABLE_LOC, stage, other_stage, other, files, _execs)]
    checks = []
    # PPM-109: огнестойкие кабели систем противопожарной защиты
    pd_fr = [r for r in pd if "FR" in r["attrs"]]
    rd_fr = [r for r in rd if "FR" in r["attrs"]]
    fire_first = lambda r: (not r["fire"], r["page"])            # строка про СПЗ — лучшее доказательство
    if pd_fr and rd_fr:
        label, note = "NO_VIOLATION", ("огнестойкие кабели для систем противопожарной защиты есть в ПД и в РД "
                                       f"(ПД: {_execs(pd_fr)}; РД: {_execs(rd_fr)})")
        ev = [_ev("PD", pd_fr, fire_first), _ev("RD", rd_fr, fire_first)]
    elif pd_fr:
        label, note = "VIOLATION_PRESENT", (f"в ПД огнестойкие кабели ({_execs(pd_fr)}), в РД огнестойких кабелей нет вовсе, "
                                            f"только {_execs(rd)} — замена FRLS на обычный (триггер матрицы); кандидат")
        ev = [_ev("PD", pd_fr, fire_first), _ev("RD", rd, fire_first)]
    else:
        label, note = "COMPARISON_IMPOSSIBLE", ("в тексте ПД огнестойкие кабели (FRLS / FRHF) не названы — требование к "
                                                f"кабелям СПЗ не найдено (ПД: {_execs(pd)}; РД: {_execs(rd)})")
        ev = [_ev("PD", pd, fire_first), _ev("RD", rd_fr or rd, fire_first)]
    checks.append({"parameter_code": FIRE_CODE, "location": FIRE_LOC, "violation_label": label,
                   "pd_value": _execs(pd_fr) or None, "rd_value": _execs(rd_fr) or None, "evidence": ev, "note": note})
    # IOS1-069: признаки исполнения распределительных кабелей
    pa = set().union(*(r["attrs"] for r in pd))
    ra = set().union(*(r["attrs"] for r in rd))
    lost = [a for a in ATTR_ORDER if a in pa - ra]
    plain = lambda r: ("FR" in r["attrs"], r["fire"], r["page"])  # строка про обычную распределительную сеть
    if not lost:
        label = "NO_VIOLATION"
        note = ("исполнение кабелей ПД сохранено в РД (" + ", ".join(ATTR_NAME[a] for a in ATTR_ORDER if a in pa) +
                "); сечения жил между стадиями не сопоставляются")
    else:
        label = "COMPARISON_IMPOSSIBLE"
        note = ("в РД не найдено исполнение, которое есть в ПД: " + ", ".join(ATTR_NAME[a] for a in lost) +
                f" (ПД: {_execs(pd)}; РД: {_execs(rd)}) — сверить по кабельному журналу")
    checks.append({"parameter_code": CABLE_CODE, "location": CABLE_LOC, "violation_label": label,
                   "pd_value": _execs(pd), "rd_value": _execs(rd),
                   "evidence": [_ev("PD", pd, plain), _ev("RD", rd, plain)], "note": note})
    return checks


def _kinds(recs):
    order = [k for k, _ in LIGHTS]
    return ", ".join(sorted({r["kind"] for r in recs}, key=order.index))


def light_checks(object_id, pd, rd):
    if not pd and not rd:
        return []
    for stage, have, other_stage, other in (("RD", rd, "PD", pd), ("PD", pd, "RD", rd)):
        if not have:
            return [_missing(LIGHT_CODE, LIGHT_LOC, stage, other_stage, other, bool(eom_files(object_id, stage)), _kinds)]
    P, R = {r["kind"] for r in pd}, {r["kind"] for r in rd}
    # доказательство — строка про светильник («Светильник светодиодный …»), а не про уличный прожектор или табло
    led_first = lambda r: (r["kind"] != "LED", "светильник" not in r["line"].lower(), r["page"])
    old_first = lambda r: (r["kind"] not in OLD_LIGHT - P, r["page"])
    new_old = (R & OLD_LIGHT) - P
    if "LED" not in P:
        label = "COMPARISON_IMPOSSIBLE"
        note = f"светодиодные светильники в тексте ПД не названы (ПД: {_kinds(pd)}; РД: {_kinds(rd)})"
        ev = [_ev("PD", pd), _ev("RD", rd, led_first)]
    elif "LED" not in R and R & OLD_LIGHT:
        bad = min((r for r in rd if r["kind"] in OLD_LIGHT), key=lambda r: r["page"])
        label = "VIOLATION_PRESENT"
        note = (f"в ПД светодиодные светильники, в РД светодиодных нет, есть {', '.join(sorted(R & OLD_LIGHT))} "
                f"(«{bad['line'][:90]}») — замена LED (триггер матрицы); кандидат")
        ev = [_ev("PD", pd, led_first), _ev("RD", rd, old_first)]
    elif not new_old:
        label = "NO_VIOLATION"
        note = ("светодиодные светильники из ПД в РД сохранены" +
                (f"; {', '.join(sorted(R & OLD_LIGHT))} — как и в ПД" if R & OLD_LIGHT else "") )
        ev = [_ev("PD", pd, led_first), _ev("RD", rd, led_first)]
    else:
        bad = min((r for r in rd if r["kind"] in new_old), key=lambda r: r["page"])
        label = "COMPARISON_IMPOSSIBLE"
        note = (f"в РД кроме светодиодных появились {', '.join(sorted(new_old))} («{bad['line'][:90]}»), в ПД их нет — "
                f"проверить, для какого освещения")
        ev = [_ev("PD", pd, led_first), _ev("RD", rd, old_first)]
    return [{"parameter_code": LIGHT_CODE, "location": LIGHT_LOC, "violation_label": label,
             "pd_value": _kinds(pd), "rd_value": _kinds(rd), "evidence": ev, "note": note}]


def power_records(object_id, stage):
    """[{kind: total|limit, kw, …}] — мощность объекта целиком по тексту ЭОМ (и ПЗ в ПД)."""
    fids = list(eom_files(object_id, stage))
    if stage == "PD":
        fids += [f for f in registry.files(object_id, stage, "PZ") if f not in fids]
    out = []
    for fid in fids:
        try:
            doc = pymupdf.open(file_path(fid))
        except Exception:
            continue
        for pno, page in enumerate(doc, 1):
            txt = page.get_text()
            if "кВт" not in txt or not (POWER_TOTAL_RE.search(" ".join(txt.split())) or
                                        POWER_LIMIT_RE.search(" ".join(txt.split()))):
                continue
            lines = page_lines(page)
            for i, (text, bbox) in enumerate(lines):
                for kind, rx in (("limit", POWER_LIMIT_RE), ("total", POWER_TOTAL_RE)):
                    m = rx.search(text)
                    if not m:
                        continue
                    # значение — первое «N кВт» после фразы: в той же строке или в начале следующей
                    tail = (text[m.end():] + " " + (lines[i + 1][0] if i + 1 < len(lines) else ""))[:160]
                    v = KW_RE.search(tail)
                    if not v or PART_RE.search(tail[:v.start()]):
                        continue                    # «расчётная нагрузка потребителей щита ЩР1 = 12 кВт» — не объект
                    kw = float(re.sub(r"[  ]", "", v.group(1)).replace(",", "."))
                    out.append({"kind": kind, "kw": kw, "file_id": fid, "page": pno, "line": text.strip()[:160],
                                "bbox": list(bbox)})
                    break
    return out


def _kw(x):
    return f"{x:g}".replace(".", ",") + " кВт"


def power_checks(object_id):
    pd, rd = power_records(object_id, "PD"), power_records(object_id, "RD")
    if not pd:
        return []                                   # в ПД мощность объекта не найдена — остаётся заглушка
    pd_tot = [r for r in pd if r["kind"] == "total"]
    tot_max = max((r["kw"] for r in pd_tot), default=None)
    # лимит по ТУ не может быть меньше расчётной мощности ПД: иначе число прочитано неверно («0,524 кВт» = 524 кВт,
    # «1,1 кВт» у жилого дома — мегаватты). Без расчётной мощности ПД лимит сверить не с чем — не используется
    limit = [r for r in pd if r["kind"] == "limit" and tot_max is not None and r["kw"] >= tot_max]
    if not limit and not pd_tot:
        return []
    base = max(limit or pd_tot, key=lambda r: r["kw"])
    pd_value = (f"лимит по ТУ {_kw(base['kw'])}" if limit else f"Рр {_kw(base['kw'])}")
    rd_tot = [r for r in rd if r["kind"] == "total"]
    if not rd_tot:
        files = bool(eom_files(object_id, "RD"))
        return [{"parameter_code": POWER_CODE, "location": POWER_LOC,
                 "violation_label": "COMPARISON_IMPOSSIBLE" if files else "MISSING_DOCUMENT",
                 "pd_value": pd_value, "rd_value": None, "evidence": [_ev("PD", [base])],
                 "note": ("в РД общая расчётная мощность объекта не найдена (в тексте ЭОМ — только нагрузки отдельных щитов "
                          "и потребителей) — сверить по расчёту нагрузок РД") if files else "в РД нет комплекта ЭОМ / ИОС1"}]
    rd_top = max(rd_tot, key=lambda r: r["kw"])
    if rd_top["kw"] <= base["kw"]:
        label = "NO_VIOLATION"
        note = f"расчётная мощность по РД {_kw(rd_top['kw'])} не превышает {'лимит по ТУ' if limit else 'расчётную по ПД'} {_kw(base['kw'])}"
    elif limit:
        label = "VIOLATION_PRESENT"
        note = (f"расчётная мощность по РД {_kw(rd_top['kw'])} превышает лимит по ТУ из ПД {_kw(base['kw'])} "
                f"(триггер матрицы); кандидат")
    else:
        label = "COMPARISON_IMPOSSIBLE"
        note = (f"расчётная мощность по РД {_kw(rd_top['kw'])} больше расчётной по ПД {_kw(base['kw'])}, а лимит по ТУ в "
                f"тексте ПД не найден — сверить с техническими условиями")
    return [{"parameter_code": POWER_CODE, "location": POWER_LOC, "violation_label": label,
             "pd_value": pd_value, "rd_value": f"Рр {_kw(rd_top['kw'])}",
             "evidence": [_ev("PD", [base]), _ev("RD", [rd_top])], "note": note}]


def compare(object_id):
    pd_c, pd_l = stage_records(object_id, "PD")
    rd_c, rd_l = stage_records(object_id, "RD")
    return cable_checks(object_id, pd_c, rd_c) + light_checks(object_id, pd_l, rd_l) + power_checks(object_id)


def main(argv):
    obj = argv[0] if argv else "OBJ-PILOT-POL16"
    for stage in ("PD", "RD"):
        cables, lights = stage_records(obj, stage)
        print(f"{stage}: файлов ЭОМ {len(eom_files(obj, stage))}, кабелей {len(cables)}, строк о свете {len(lights)}")
        print(f"   исполнение: {_execs(cables)}")
        print(f"   огнестойкие в строках про СПЗ: {_execs([c for c in cables if c['fire'] and 'FR' in c['attrs']])}")
        print(f"   свет: {_kinds(lights)}")
        for r in {(r['kind'], r['line']): r for r in lights if r["kind"] != "LED"}.values():
            print(f"        {r['file_id']}:{r['page']} [{r['kind']}] {r['line'][:100]}")
    for c in compare(obj):
        ev = ", ".join(f"{e['stage']} {e['file_id']}:{e['pdf_page_number']}" for e in c["evidence"])
        print(f"{c['parameter_code']:9} {c['violation_label']:22} {c['location']:32} ПД {c['pd_value']} | РД {c['rd_value']}"
              f" [{ev}]\n      {c['note']}")


if __name__ == "__main__":
    main(sys.argv[1:])
