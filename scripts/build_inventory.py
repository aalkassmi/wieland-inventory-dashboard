"""Build data/inventory.json for the Wieland Regional Inventory Performance dashboard.

Usage:  python3 scripts/build_inventory.py --end YYYY-MM-DD [--today YYYY-MM-DD] [--raw raw]

Reads raw pulls from the --raw folder (not committed):
  motive.json        {"955": [[stock, year, make, model, condition, sale_price], ...], ...}   listed units now
  act_<id>.json      {"A": {stock: n}, "B": {...}, "C": {...}, "D": {...}, "E": {...}}          per store, 90-day window
                     A=page views, B=page views with utm_medium=cpc, C=Check Availability uniques,
                     D=Photo Request uniques, E=Click Phone Number total
  names_<id>.json    {stock: ["New"|"Used", "2022 FREIGHTLINER Cascadia"]}                       from Mixpanel Vehicle Name
  age_<id>.json      {stock: days_on_lot_as_of_END}                                              from Vehicle Inventory Age

Compares against the current data/inventory.json for sanity checks and to carry forward the true regional
age of trucks transferred between stores (Mixpanel's store-level age restarts at 0 on a transfer).
Exits non-zero and writes nothing if a check fails.
"""
import argparse, datetime as dt, json, os, sys

STORES = [(955, "Chattanooga"), (966, "Knoxville"), (967, "La Crosse"), (979, "Roberts"), (959, "Eau Claire")]
MAKES = {"FREIGHTLINER": "Freightliner", "WESTERN STAR": "Western Star", "KENWORTH": "Kenworth",
         "INTERNATIONAL": "International", "PETERBILT": "Peterbilt", "MACK TRUCKS": "Mack", "MACK": "Mack",
         "WALKER": "Walker", "VOLVO": "Volvo", "INTERNATIONAL TRUCKS": "International"}
OUT = "data/inventory.json"


def clean_name(raw):
    raw = raw.replace("®", "").strip()
    yr, rest = raw.split(" ", 1)
    make = next((m for m in sorted(MAKES, key=len, reverse=True) if rest.upper().startswith(m)), None)
    if make:
        model = rest[len(make):].strip()
        mk = MAKES[make]
    else:
        mk, model = rest.split(" ", 1) if " " in rest else (rest, "")
        mk = mk.title()
    if model[:-1].isdigit() and model[-1:] in ("x", "X"):
        model = model.upper()
    return f"{yr} {mk} {model}".strip()


def label(start, end):
    if start.year == end.year:
        return f"{start.strftime('%B')} {start.day} to {end.strftime('%b')} {end.day}, {end.year}"
    return f"{start.strftime('%B')} {start.day}, {start.year} to {end.strftime('%b')} {end.day}, {end.year}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--end", required=True)
    ap.add_argument("--today", default=dt.date.today().isoformat())
    ap.add_argument("--raw", default="raw")
    a = ap.parse_args()
    end = dt.date.fromisoformat(a.end); start = end - dt.timedelta(days=90); today = dt.date.fromisoformat(a.today)
    R = lambda f: json.load(open(os.path.join(a.raw, f)))

    prev = json.load(open(OUT)) if os.path.exists(OUT) else None
    prev_units = {u["s"]: u for u in prev["units"]} if prev else {}
    prev_asof = None
    if prev:
        prev_asof = dt.datetime.strptime(f"{prev['age_as_of']} {prev['updated'][-4:]}", "%b %d %Y").date()
        if prev_asof > end:
            prev_asof = prev_asof.replace(year=prev_asof.year - 1)

    motive = R("motive.json")
    act, names, ages = {}, {}, {}
    for d, _ in STORES:
        for k, m in R(f"act_{d}.json").items():
            for s, v in m.items():
                act.setdefault(s, {}).setdefault(d, {})[k] = int(v)
        for s, (c, n) in R(f"names_{d}.json").items():
            names.setdefault(s, (c, clean_name(n)))
        ages[d] = {s: int(v) for s, v in R(f"age_{d}.json").items()}

    listed = {}
    for d, _ in STORES:
        for s, yr, mk, model, cond, price in motive[str(d)]:
            listed[s] = dict(d=d, n=clean_name(f"{yr} {mk} {model}"),
                             c="New" if str(cond).lower() == "new" else "Used", p=int(price or 0))

    name_of = dict(STORES)
    units = []
    for s in sorted(set(act) | set(listed)):
        per = act.get(s, {})
        t = {k: sum(per[d].get(k, 0) for d in per) for k in "ABCDE"}
        transferred = False
        if s in listed:
            L = listed[s]; d = L["d"]; on = True
            c = L["c"]; n = names[s][1] if s in names else L["n"]; p = L["p"]
            age = ages[d].get(s)
            pu = prev_units.get(s)
            if pu and pu.get("on") and pu.get("d") is not None and prev_asof:
                carried = pu["d"] + (end - prev_asof).days
                moved = pu["loc"] != name_of[d]
                if (age is None or age < pu["d"]) and (moved or pu.get("t")):
                    age, transferred = carried, True
                elif pu.get("t") and age is not None and age < carried:
                    age, transferred = carried, True
        else:
            if not per:
                continue
            d = max(per, key=lambda x: per[x].get("A", 0)); on = False
            c, n = names.get(s, ("Used", s)); p = None; age = None
        v = t["A"]
        units.append({"s": s, "loc": name_of[d], "c": c, "n": n, "v": v,
                      "ad": round(100 * t["B"] / v) if v else None,
                      "l": t["C"] + t["D"], "k": t["E"], "p": p, "d": age, "on": on, "t": transferred})
    units.sort(key=lambda u: (-(u["l"] + u["k"]), -u["v"]))

    # ---- sanity checks ----
    problems = []
    for d, nm in STORES:
        if not any(u["on"] and u["loc"] == nm for u in units):
            problems.append(f"{nm}: no listed units from Motive")
        if sum(u["v"] for u in units if u["loc"] == nm) == 0:
            problems.append(f"{nm}: zero views")
    V = sum(u["v"] for u in units)
    if prev:
        PV = sum(u["v"] for u in prev["units"])
        if PV and V < 0.25 * PV:
            problems.append(f"total views {V} is below 25% of last file ({PV})")
    if problems:
        print("NOT WRITTEN:\n  " + "\n  ".join(problems)); sys.exit(1)

    out = {"region": "Wieland Region", "stores": [n for _, n in STORES],
           "dealer_ids": {n: d for d, n in STORES},
           "window_label": label(start, end), "age_as_of": f"{end.strftime('%b')} {end.day}",
           "updated": f"{today.strftime('%b')} {today.day}, {today.year}", "units": units}
    json.dump(out, open(OUT, "w"), indent=1)

    # ---- report ----
    print(f"window {out['window_label']} | units {len(units)} | listed {sum(u['on'] for u in units)} | views {V}"
          + (f" (last file {PV})" if prev else ""))
    for _, nm in STORES:
        U = [u for u in units if u["loc"] == nm]; on = [u for u in U if u["on"]]
        ag = [u["d"] for u in on if u["d"] is not None]
        print(f"  {nm:12} listed new {sum(u['c']=='New' for u in on):2} used {sum(u['c']=='Used' for u in on):2} | "
              f"views {sum(u['v'] for u in U):6} leads {sum(u['l'] for u in U):4} calls {sum(u['k'] for u in U):3} | "
              f"avg age {round(sum(ag)/len(ag)) if ag else '-'} | no price {sum(u['p']==0 for u in on)} | "
              f"listed w/ 0 views {sum(u['v']==0 for u in on)}")
    top = sorted([u for u in units if u["on"]], key=lambda u: (-(u["l"] + u["k"]), -u["v"]))[:3]
    print("  top listed: " + "; ".join(f"{u['s']} {u['loc']} {u['n']} ({u['l']} leads, {u['k']} calls)" for u in top))
    old = [u for u in units if u["on"] and u["c"] == "New" and (u["d"] or 0) >= 365]
    print(f"  new units 365+ days: {len(old)} " + ", ".join(f"{u['s']} {u['loc']} {u['d']}d" for u in old))
    tr = [u for u in units if u["t"]]
    if tr:
        print("  transfers (regional age carried): " + ", ".join(f"{u['s']} {u['loc']} {u['d']}d" for u in tr))


if __name__ == "__main__":
    main()
