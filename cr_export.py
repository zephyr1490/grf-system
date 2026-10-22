"""
cr_export.py — Detail-Excel zur CR-Berechnung (Admin Panel).

Zweck: nachvollziehen, WO die Zeiten "brechen": pro Location, Stage und Auto
alle Kennzahlen, die in die CR einfließen (oder zum Streichen einer Stage
führen), plus Matrizen (Faktor / N / Referenzzeit) und die Rohdaten.

Wird von admin_api.py (/cr/export-xlsx) aufgerufen. Die Rechenfunktionen
werden übergeben (kein Import von admin_api → keine Zirkularität) und sind
exakt dieselben wie bei der CR-Berechnung selbst, damit das Excel nie von
den angezeigten CR-Werten abweichen kann.

Blätter:
  Einstellungen · Uebersicht · Faktor-Matrix · N-Matrix · Referenzzeit-Matrix ·
  Details <Location> (je Location eins) · Uebersprungen · Rohdaten
"""
import io
import re
from datetime import datetime

from openpyxl import Workbook
from openpyxl.formatting.rule import ColorScaleRule, CellIsRule
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

# Perzentile (in % von N des Autos), deren Zeit pro Stage/Auto ausgewiesen wird
PERCENTILES = (1, 2, 4, 8, 18, 25, 40, 50)

# Rohdaten: ab dieser Gesamtzahl Zeilen wird pro Stage/Auto gekappt
RAW_LIMIT = 60000
RAW_CAP_PER_CAR_STAGE = 300

HDR_FILL   = PatternFill("solid", fgColor="1F2937")
HDR_FONT   = Font(bold=True, color="FFFFFF")
RED_FILL   = PatternFill("solid", fgColor="F8B4B4")
GREY_FILL  = PatternFill("solid", fgColor="D9D9D9")
GREEN_FILL = PatternFill("solid", fgColor="CFE8CF")


def _fmt_ms(ms):
    if ms is None:
        return ""
    ms = int(round(ms))
    return f"{ms // 60000:02d}:{(ms % 60000) / 1000:06.3f}"


def _rank_for(p, n):
    return max(1, min(round(p / 100 * n), n))


def _safe_sheet_name(name, used):
    name = re.sub(r'[\[\]\*\?/\\:]', " ", str(name)).strip() or "Sheet"
    name = name[:31]
    base, i = name, 2
    while name.lower() in used:
        suffix = f" ({i})"
        name = base[:31 - len(suffix)] + suffix
        i += 1
    used.add(name.lower())
    return name


def _style_header(ws, row=1):
    for c in ws[row]:
        c.fill, c.font = HDR_FILL, HDR_FONT
        c.alignment = Alignment(wrap_text=True, vertical="center")


def _autowidth(ws, max_w=42, min_w=8):
    widths = {}
    for row in ws.iter_rows():
        for c in row:
            if c.value is None:
                continue
            L = len(str(c.value))
            widths[c.column_letter] = max(widths.get(c.column_letter, 0), L)
    for col, w in widths.items():
        ws.column_dimensions[col].width = max(min_w, min(max_w, w + 2))


def build_workbook(run, route_meta, class_meta, results, fns):
    """
    run:        {"leaderboards": {(route,class): [entries]}, "params": {...}}
    route_meta: {route_id(int): {"loc": str, "name": str}}
    class_meta: {class_id(int): str}
    results:    die CR-Ergebnisliste ([{vehicle, cr, n, stages, n_min}]) der Berechnung
    fns:        {"parse_time_ms", "is_dnf_ms", "compute_stage_factors"}
    Gibt Bytes der .xlsx zurück.
    """
    parse_ms = fns["parse_time_ms"]
    is_dnf   = fns["is_dnf_ms"]
    stage_fn = fns["compute_stage_factors"]
    P        = run["params"]
    top_pct, min_n, min_rank, exponent = P["top_pct"], P["min_n"], P.get("min_rank", 1), P["exponent"]

    # ── Daten pro Strecke zusammenführen (wie _compute_cr), Klasse am Eintrag merken
    by_route = {}
    incomplete = set()
    for (rid, cid), entries in run["leaderboards"].items():
        bucket = by_route.setdefault(rid, [])
        if not entries:
            incomplete.add(rid)
            continue
        for e in entries:
            bucket.append((cid, e))

    def meta(rid):
        m = route_meta.get(rid) or route_meta.get(str(rid)) or {}
        return m.get("loc") or "Location ?", m.get("name") or f"Route {rid}"

    routes_sorted = sorted(by_route, key=lambda r: (meta(r)[0], meta(r)[1], r))

    # ── Pro Strecke auswerten ─────────────────────────────────────────────────
    stage_rows = []        # Detailzeilen
    stage_info = {}        # rid → {status, reason, n_total, factors, ...}
    car_stage  = {}        # (rid, car) → Kennzahlen (für Matrizen)
    car_classes = {}
    raw_rows = []
    raw_keys = []
    wtot_car = {}

    for rid in routes_sorted:
        loc, sname = meta(rid)
        pairs   = by_route[rid]
        entries = [e for _, e in pairs]
        stage_ms = [t for t in (parse_ms(e.get("time", "")) for e in entries) if t]

        per_car = {}
        for cid, e in pairs:
            car = e.get("vehicle", "Unknown")
            d = per_car.setdefault(car, {"raw": 0, "bad": 0, "dnf": 0, "times": [], "classes": set(), "rows": []})
            d["raw"] += 1
            d["classes"].add(class_meta.get(cid) or class_meta.get(str(cid)) or str(cid))
            ms = parse_ms(e.get("time", ""))
            if not ms:
                d["bad"] += 1
                continue
            if is_dnf(ms, stage_ms):
                d["dnf"] += 1
                continue
            d["times"].append(ms)
            d["rows"].append((cid, e, ms))
            for k in e.keys():
                if k not in raw_keys:
                    raw_keys.append(k)
        for d in per_car.values():
            d["times"].sort()

        if rid in incomplete:
            factors, n_total, diag = None, 0, {"reason": "no_data", "cars": {}}
        else:
            factors, n_total, diag = stage_fn(entries, top_pct, min_n, min_rank)

        used = factors is not None
        if used:
            n_by_car = diag
            reason = ""
        else:
            reason = diag.get("reason", "unknown")
            culprits = ", ".join(f"{c} (N={n})" for c, n in diag.get("cars", {}).items())
            reason_txt = {
                "min_n": f"unter min N={min_n}: {culprits}",
                "too_few_cars": "weniger als 2 Autos mit gültigen Zeiten",
                "no_data": "für mindestens eine Klasse kam kein Leaderboard",
                "invalid_times": "ungültige Zeiten",
            }.get(reason, reason)
            reason = reason_txt

        # Referenz je Auto (identisch zu _compute_stage_factors)
        refs = {}
        for car, d in per_car.items():
            n = len(d["times"])
            if n:
                refs[car] = d["times"][max(1, min(max(min_rank, round(top_pct / 100 * n)), n)) - 1]
        best_ref = min(refs.values()) if refs else None

        stage_info[rid] = {"used": used, "reason": reason, "n_total": n_total,
                           "loc": loc, "stage": sname}
        for car, d in per_car.items():
            n = len(d["times"])
            row = {
                "loc": loc, "stage": sname, "rid": rid, "car": car,
                "klasse": ", ".join(sorted(d["classes"])),
                "n_raw": d["raw"], "n_bad": d["bad"], "n_dnf": d["dnf"], "n": n,
                "best": d["times"][0] if n else None,
                "perc": [d["times"][_rank_for(p, n) - 1] if n else None for p in PERCENTILES],
                "median": d["times"][(n - 1) // 2] if n else None,
                "ref_rank": (max(1, min(max(min_rank, round(top_pct / 100 * n)), n)) if n else None),
                "ref": refs.get(car),
                "factor": (factors or {}).get(car) if used else None,
                "weight": n_total if used else None,
                "status": "verwendet" if used else "gestrichen",
                "reason": "" if used else stage_info[rid]["reason"],
                "below_min": n < min_n,
                "ref_gap_stage": ((refs[car] / best_ref - 1) * 100) if (car in refs and best_ref) else None,
            }
            car_stage[(rid, car)] = row
            stage_rows.append(row)
            car_classes.setdefault(car, set()).update(d["classes"])
            if used and factors and car in factors:
                wtot_car[car] = wtot_car.get(car, 0) + n_total
            # Rohdaten
            for rank, (cid, e, ms) in enumerate(sorted(d["rows"], key=lambda t: t[2]), 1):
                raw_rows.append((loc, sname, rid, car,
                                 class_meta.get(cid) or class_meta.get(str(cid)) or str(cid),
                                 rank, ms, e))

    cars = sorted({r["car"] for r in stage_rows},
                  key=lambda c: next((x["cr"] for x in results if x["vehicle"] == c), 9e9))

    # Gewichtsanteil je (Stage, Auto)
    for r in stage_rows:
        if r["status"] == "verwendet" and wtot_car.get(r["car"]):
            r["share"] = r["weight"] / wtot_car[r["car"]] * 100
        else:
            r["share"] = None

    wb = Workbook()
    used_names = set()

    # ── Einstellungen ─────────────────────────────────────────────────────────
    ws = wb.active
    ws.title = _safe_sheet_name("Einstellungen", used_names)
    ws.append(["Parameter", "Wert"])
    for k, v in [
        ("Erstellt", datetime.now().strftime("%Y-%m-%d %H:%M")),
        ("Top % (Threshold)", top_pct),
        ("Min N pro Auto und Stage", min_n),
        ("Min Referenzrang", min_rank),
        ("Nur Einträge ohne Assists", "ja" if P.get("no_assists") else "nein"),
        ("Exponent", exponent),
        ("Stages ausgewählt", len(routes_sorted)),
        ("Stages verwendet", sum(1 for i in stage_info.values() if i["used"])),
        ("Stages gestrichen", sum(1 for i in stage_info.values() if not i["used"])),
        ("Klassen", ", ".join(sorted({c for cs in car_classes.values() for c in cs}))),
        ("", ""),
        ("Lesehilfe", ""),
        ("Referenzzeit", "Zeit des Autos auf Rang max(Min-Referenzrang, round(Top%/100*N)) seiner eigenen sortierten Zeiten"),
        ("Faktor", "Referenzzeit / beste Referenzzeit aller Autos auf dieser Stage (1.0 = schnellstes Auto der Stage)"),
        ("Gewicht", "Teilnehmerzahl der Stage (alle Autos); gewichtet den Durchschnitt über die Stages"),
        ("Gewichtsanteil", "Anteil der Stage am gewichteten Durchschnitt dieses Autos"),
        ("CR", "(gewichteter Faktor / bester gewichteter Faktor) ^ Exponent, danach erneut auf das beste Auto normiert"),
        ("Zeitrückstand %", "CR^(1/Exponent) − 1"),
        ("Rot in N-Matrix", "N unter Min N → die Stage wird für ALLE Autos gestrichen"),
    ]:
        ws.append([k, v])
    _style_header(ws)
    _autowidth(ws, max_w=110)

    # ── Übersicht ─────────────────────────────────────────────────────────────
    ws = wb.create_sheet(_safe_sheet_name("Uebersicht", used_names))
    ws.append(["Rang", "Auto", "Klasse", "CR", "Zeitrückstand %", "Stages verwendet",
               "Stages vorhanden", "N gesamt (verwendet)", "N min", "N roh gesamt",
               "DNF/ungültig entfernt"])
    res_by_car = {r["vehicle"]: r for r in results}
    for i, car in enumerate(cars, 1):
        r = res_by_car.get(car)
        rows = [x for x in stage_rows if x["car"] == car]
        cr = r["cr"] if r else None
        ws.append([
            i, car, ", ".join(sorted(car_classes.get(car, []))), cr,
            (cr ** (1 / exponent) - 1) * 100 if cr else None,
            r["stages"] if r else 0,
            sum(1 for x in rows if x["n"] > 0),
            r["n"] if r else 0,
            r["n_min"] if r else min((x["n"] for x in rows), default=0),
            sum(x["n_raw"] for x in rows),
            sum(x["n_bad"] + x["n_dnf"] for x in rows),
        ])
    _style_header(ws)
    for row in ws.iter_rows(min_row=2):
        row[3].number_format = "0.0000"
        row[4].number_format = "0.00"
    ws.freeze_panes = "C2"
    _autowidth(ws)

    # ── Matrizen ──────────────────────────────────────────────────────────────
    def matrix(title, value_fn, number_format, colour=None, skip_fill=True):
        w = wb.create_sheet(_safe_sheet_name(title, used_names))
        w.append(["Location", "Stage", "Status", "Gewicht"] + cars)
        for rid in routes_sorted:
            info = stage_info[rid]
            line = [info["loc"], info["stage"], "verwendet" if info["used"] else "GESTRICHEN: " + info["reason"],
                    info["n_total"] if info["used"] else None]
            for car in cars:
                r = car_stage.get((rid, car))
                line.append(value_fn(r) if r else None)
            w.append(line)
        _style_header(w)
        last = w.max_row
        for row in w.iter_rows(min_row=2, max_row=last, min_col=5):
            for c in row:
                c.number_format = number_format
        for i, rid in enumerate(routes_sorted, 2):
            if not stage_info[rid]["used"] and skip_fill:
                for c in w[i][:4]:
                    c.fill = GREY_FILL
        if colour and last >= 2 and cars:
            rng = f"E2:{get_column_letter(4 + len(cars))}{last}"
            w.conditional_formatting.add(rng, colour)
        w.freeze_panes = "E2"
        _autowidth(w)
        w.column_dimensions["C"].width = 34
        return w

    matrix("Faktor-Matrix", lambda r: r["factor"], "0.0000",
           ColorScaleRule(start_type="min", start_color="63BE7B", mid_type="percentile", mid_value=50,
                          mid_color="FFEB84", end_type="max", end_color="F8696B"))
    wn = matrix("N-Matrix", lambda r: r["n"], "0")
    if cars and wn.max_row >= 2:
        wn.conditional_formatting.add(
            f"E2:{get_column_letter(4 + len(cars))}{wn.max_row}",
            CellIsRule(operator="lessThan", formula=[str(min_n)], fill=RED_FILL))
    matrix("Referenzzeit-Matrix", lambda r: (r["ref"] / 1000 if r["ref"] else None), "0.000",
           ColorScaleRule(start_type="min", start_color="63BE7B", end_type="max", end_color="F8696B"))
    matrix("Rueckstand Ref %", lambda r: r["ref_gap_stage"], "0.00",
           ColorScaleRule(start_type="num", start_value=0, start_color="63BE7B",
                          end_type="max", end_color="F8696B"))

    # ── Detail je Location ────────────────────────────────────────────────────
    head = (["Stage", "Auto", "Klasse", "N roh", "ungültig", "DNF entfernt", "N gültig", "Bestzeit"]
            + [f"Zeit @Top {p}%" for p in PERCENTILES] + ["Median"]
            + ["Ref-Rang", "Referenzzeit", "Referenzzeit (s)", "Rückstand Ref %", "Faktor",
               "Gewicht", "Gewichtsanteil %", "Status", "Grund"])
    by_loc = {}
    for r in stage_rows:
        by_loc.setdefault(r["loc"], []).append(r)
    for loc in sorted(by_loc):
        w = wb.create_sheet(_safe_sheet_name(f"D {loc}", used_names))
        w.append(head)
        rows = sorted(by_loc[loc], key=lambda x: (x["stage"], x["ref"] if x["ref"] else 9e12))
        for r in rows:
            w.append([r["stage"], r["car"], r["klasse"], r["n_raw"], r["n_bad"], r["n_dnf"], r["n"],
                      _fmt_ms(r["best"])] + [_fmt_ms(t) for t in r["perc"]] + [_fmt_ms(r["median"])]
                     + [r["ref_rank"], _fmt_ms(r["ref"]), (r["ref"] / 1000 if r["ref"] else None),
                        r["ref_gap_stage"], r["factor"], r["weight"], r["share"], r["status"], r["reason"]])
            if r["below_min"]:
                w.cell(row=w.max_row, column=7).fill = RED_FILL
            if r["status"] != "verwendet":
                w.cell(row=w.max_row, column=head.index("Status") + 1).fill = GREY_FILL
        _style_header(w)
        col = {h: i + 1 for i, h in enumerate(head)}
        for row in w.iter_rows(min_row=2):
            row[col["Referenzzeit (s)"] - 1].number_format = "0.000"
            row[col["Rückstand Ref %"] - 1].number_format = "0.00"
            row[col["Faktor"] - 1].number_format = "0.0000"
            row[col["Gewichtsanteil %"] - 1].number_format = "0.0"
        w.freeze_panes = "C2"
        w.auto_filter.ref = w.dimensions
        _autowidth(w)

    # ── Paarvergleich über gemeinsame Fahrer ──────────────────────────────────
    # Fahrer (ssid), die auf derselben Stage in zwei Autos eine gültige Zeit
    # haben: direkter Vergleich ohne Fahrerpool-Effekt. Gleiche-Klasse-Paare
    # gibt es nicht (ein Fahrer steht pro Klasse nur einmal im Leaderboard).
    def _is_clean(e):
        f = e.get("assistFlags")
        if isinstance(f, str):
            try:
                import json as _j
                f = _j.loads(f)
            except Exception:
                return None
        if not isinstance(f, (list, tuple)):
            return None
        return all(int(x) == 0 for x in f)

    per_stage_car = {}   # (rid, car) -> {ssid: (ms, clean)}
    for rid in routes_sorted:
        for cid, e in by_route[rid]:
            ss = e.get("ssid")
            ms = parse_ms(e.get("time", ""))
            if not ss or not ms:
                continue
            car = e.get("vehicle", "Unknown")
            per_stage_car.setdefault((rid, car), {})[ss] = (ms, _is_clean(e))

    import statistics as _st
    pair_rows = []
    for i, a in enumerate(cars):
        for b in cars[i + 1:]:
            diffs, clean, per_stage = [], [], {}
            for rid in routes_sorted:
                da, db = per_stage_car.get((rid, a)), per_stage_car.get((rid, b))
                if not da or not db:
                    continue
                common = set(da) & set(db)
                ds = [(da[x][0] / db[x][0] - 1) * 100 for x in common]
                per_stage[rid] = ds
                diffs += ds
                clean += [(da[x][0] / db[x][0] - 1) * 100 for x in common
                          if da[x][1] and db[x][1]]
            if len(diffs) >= 20:
                pair_rows.append((a, b, len(diffs), _st.median(diffs), _st.mean(diffs),
                                  len(clean), _st.median(clean) if clean else None,
                                  {r: (len(v), _st.median(v)) for r, v in per_stage.items() if v}))
    ws = wb.create_sheet(_safe_sheet_name("Paarvergleich", used_names))
    ws.append(["Auto A", "Auto B", "Gemeinsame Fahrer (alle Stages)", "Median A gegen B %",
               "Mittel A gegen B %", "davon ohne Assists (n)", "Median ohne Assists %",
               "Schneller", "Pro Stage: n / Median %"])
    for a, b, n, med, mean, nc, medc, ps in sorted(pair_rows, key=lambda r: -r[2]):
        ws.append([a, b, n, round(med, 3), round(mean, 3), nc,
                   (round(medc, 3) if medc is not None else None),
                   (a if med < 0 else b) if abs(med) >= 0.05 else "gleich",
                   "; ".join(f"{stage_info[r]['stage']}: {c} / {m:.2f}" for r, (c, m) in ps.items())])
    if len(ws["A"]) == 1:
        ws.append(["–", "keine Paare mit mind. 20 gemeinsamen Fahrern", None])
    ws.insert_rows(1)
    ws["A1"] = ("Direkter Vergleich: Fahrer, die auf derselben Stage in beiden Autos eine Zeit haben. "
                "Negativ = Auto A ist schneller (Median der Zeit A/B − 1). Gleiche Klasse nie vergleichbar.")
    ws["A1"].font = Font(bold=True)
    _style_header(ws, row=2)
    ws.freeze_panes = "A3"
    _autowidth(ws, max_w=70)

    # ── Übersprungen ──────────────────────────────────────────────────────────
    ws = wb.create_sheet(_safe_sheet_name("Uebersprungen", used_names))
    ws.append(["Location", "Stage", "Grund"])
    skipped = [i for i in stage_info.values() if not i["used"]]
    for i in skipped:
        ws.append([i["loc"], i["stage"], i["reason"]])
    if not skipped:
        ws.append(["–", "keine Stage gestrichen", ""])
    _style_header(ws)
    _autowidth(ws, max_w=110)

    # ── Rohdaten ──────────────────────────────────────────────────────────────
    ws = wb.create_sheet(_safe_sheet_name("Rohdaten", used_names))
    capped = len(raw_rows) > RAW_LIMIT
    ws.append(["Location", "Stage", "Route-ID", "Auto", "Klasse", "Rang im Auto", "Zeit", "Zeit (s)"]
              + [f"api:{k}" for k in raw_keys])
    count = {}
    for loc, sname, rid, car, kl, rank, ms, e in raw_rows:
        if capped:
            key = (rid, car)
            count[key] = count.get(key, 0) + 1
            if count[key] > RAW_CAP_PER_CAR_STAGE:
                continue
        ws.append([loc, sname, rid, car, kl, rank, _fmt_ms(ms), ms / 1000]
                  + [("" if e.get(k) is None else (e[k] if isinstance(e[k], (int, float, str)) else str(e[k])))
                     for k in raw_keys])
    if capped:
        ws.insert_rows(1)
        ws["A1"] = (f"Hinweis: {len(raw_rows)} Zeilen insgesamt, je Stage und Auto nur die "
                    f"schnellsten {RAW_CAP_PER_CAR_STAGE} gezeigt.")
        ws["A1"].font = Font(bold=True, color="B00020")
        _style_header(ws, row=2)
        ws.freeze_panes = "A3"
    else:
        _style_header(ws)
        ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    _autowidth(ws, max_w=36)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
