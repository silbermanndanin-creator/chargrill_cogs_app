"""Aggregation helpers for the dashboard. Pure functions over the invoices DataFrame."""
import re
import json
import datetime as dt
import pandas as pd
import config

UNIT_MAP = {
    "each": "ea", "unit": "ea", "units": "ea", "ea.": "ea",
    "ctn": "carton", "ctns": "carton", "cartons": "carton",
    "kgs": "kg", "kilo": "kg", "kilos": "kg", "kilogram": "kg", "kilograms": "kg",
    "box": "box", "boxes": "box", "cases": "case", "trays": "tray",
    "bags": "bag", "litres": "litre", "l": "litre", "doz": "dozen",
}


def norm_unit(u):
    if u is None or (isinstance(u, float) and pd.isna(u)):
        return None
    s = str(u).strip().lower()
    if not s:
        return None
    return UNIT_MAP.get(s, s)


def explode_lines(df: pd.DataFrame) -> pd.DataFrame:
    """One row per invoice line, with supplier/period carried down."""
    recs = []
    for _, r in df.iterrows():
        raw = r.get("line_items")
        if not isinstance(raw, str) or not raw.strip():
            continue
        try:
            items = json.loads(raw)
        except Exception:
            continue
        for it in items:
            if not isinstance(it, dict):
                continue
            recs.append({
                "supplier": r["supplier"], "invoice_date": r.get("invoice_date"),
                "iso_week": r["iso_week"], "month": r["month"],
                "description": it.get("description"),
                "quantity": it.get("quantity"),
                "unit": norm_unit(it.get("unit")),
                "unit_price": it.get("unit_price"),  # printed per-unit price (per-kg when shown)
                "amount": it.get("amount"),
                # stored override (from review screen) else detect from description
                "tub_type": it.get("tub_type") or config.tub_type(it.get("description")),
            })
    cols = ["supplier", "invoice_date", "iso_week", "month", "description",
            "quantity", "unit", "unit_price", "amount", "tub_type"]
    return pd.DataFrame(recs, columns=cols)


def spend_and_deliveries(df, period_col, period_key):
    sub = df[df[period_col] == period_key]
    spend = sub.groupby("supplier")["total_ex_gst"].sum()
    deliveries = sub.groupby("supplier").size()
    return spend, deliveries


def qty_by_supplier_unit(lines, period_col, period_key):
    """{supplier: {unit: {'qty':, 'amount':, 'per_unit':}}} for one period."""
    out = {}
    if lines.empty:
        return out
    sub = lines[(lines[period_col] == period_key)
                & lines["quantity"].notna() & lines["unit"].notna()]
    for (sup, unit), g in sub.groupby(["supplier", "unit"]):
        q = float(g["quantity"].sum())
        amt = float(pd.to_numeric(g["amount"], errors="coerce").fillna(0).sum())
        out.setdefault(sup, {})[unit] = {"qty": q, "amount": amt,
                                         "per_unit": (amt / q if q else None)}
    return out


def recent_periods(df, period_col, n=8):
    vals = sorted(v for v in df[period_col].dropna().unique())  # "YYYY-Www"/"YYYY-MM" sort chronologically
    return vals[-n:]


def weekly_supplier_spend(df, period_col, periods):
    sub = df[df[period_col].isin(periods)]
    if sub.empty:
        return pd.DataFrame(columns=["Period", "Supplier", "Spend"])
    g = sub.groupby([period_col, "supplier"])["total_ex_gst"].sum().reset_index()
    g.columns = ["Period", "Supplier", "Spend"]
    return g


def cogs_pct_trend(df, rev_map, period_col, periods):
    rows = []
    for p in periods:
        rev = rev_map.get(p)
        if not rev:
            continue
        cogs = df[df[period_col] == p]["total_ex_gst"].sum()
        rows.append({"Period": p, "COGS %": round(cogs / rev * 100, 1),
                     "Target 40%": 40.0, "Red 42%": 42.0})
    return pd.DataFrame(rows)


def food_cogs_for_period(df, period_col, period_key):
    """Food-COGS spend (is_cogs categories only) for one period — matches the
    dashboard's headline Total COGS, so prime cost is consistent with it."""
    if df.empty:
        return 0.0
    sub = df[df[period_col] == period_key]
    spend = sub.groupby("supplier")["total_ex_gst"].sum()
    return float(sum(v for s, v in spend.items() if config.is_cogs(s)))


def labour_prime_trend(df, rev_map, labour_cost_map, period_col, periods):
    """Per-period Labour % and Prime cost % ((food COGS + labour) / revenue),
    for periods that have BOTH a revenue and a labour figure logged."""
    rows = []
    for p in periods:
        rev = rev_map.get(p)
        lab = labour_cost_map.get(p)
        if not rev or not lab:
            continue
        cogs = food_cogs_for_period(df, period_col, p)
        rows.append({"Period": p,
                     "Labour %": round(lab / rev * 100, 1),
                     "Prime %": round((cogs + lab) / rev * 100, 1)})
    return pd.DataFrame(rows)


def _f(v):
    """Float or 0.0 (NaN/None/blank-safe) — POS values may be CSV strings or DB numbers."""
    try:
        x = float(v)
        return 0.0 if x != x else x  # NaN -> 0
    except (TypeError, ValueError):
        return 0.0


def delivery_keep_map(payouts_df, pos_df):
    """{(platform_key, iso_week): keep_fraction} where keep = actual_net / gross — the real
    share of delivery sales the venue keeps that week, from the platforms' weekly payment
    summaries. Uber's gross comes from its own payout; DoorDash's email has no gross, so its
    gross is taken from the POS slips for that week (the app already records DoorDash gross
    there). Used by pos_revenue_map to replace the flat config.DELIVERY_COMMISSION estimate
    for any week a real payout has landed. Empty -> nothing to override."""
    out = {}
    if payouts_df is None or payouts_df.empty:
        return out
    # POS gross per (platform_key, iso_week), to back the platforms whose email omits gross.
    pos_gross = {}
    if pos_df is not None and not pos_df.empty and "iso_week" in pos_df.columns:
        for pk in ("ubereats", "doordash"):
            if pk in pos_df.columns:
                g = (pd.to_numeric(pos_df[pk], errors="coerce").fillna(0)
                     .groupby(pos_df["iso_week"].astype(str)).sum())
                for wk, v in g.items():
                    pos_gross[(pk, str(wk))] = float(v)
    for _, r in payouts_df.iterrows():
        pk = str(r.get("platform_key") or "").strip()
        wk = str(r.get("iso_week") or "").strip()
        if not pk or not wk:
            continue
        net = _f(r.get("net_payout"))
        gross = _f(r.get("gross_incl_gst"))
        if gross <= 0:  # email carried no gross (DoorDash) -> use POS gross for that week
            gross = pos_gross.get((pk, wk), 0.0)
        if net > 0 and gross > 0:
            out[(pk, wk)] = max(0.0, min(net / gross, 1.0))  # clamp to a sane 0..1 keep
    return out


def pos_revenue_map(pos_df, period_col, keep_map=None):
    """{period_key: net ex-GST revenue} from daily POS slips.

    Without keep_map: sums the stored adjusted_ex_gst (the flat-commission estimate) —
    unchanged behaviour. With keep_map (from delivery_keep_map): recomputes each day's net,
    replacing the flat config.DELIVERY_COMMISSION cut on Uber/DoorDash sales with the ACTUAL
    keep fraction for that day's ISO week wherever a real payout exists — so revenue (and
    COGS %) is true, not assumed. Works in Week and Month views alike (each POS day carries
    its own iso_week, so the right week's actual rate is applied even when bucketing by month)."""
    if pos_df.empty:
        return {}
    if not keep_map:
        g = pos_df.groupby(period_col)["adjusted_ex_gst"].sum()
        return {k: float(v) for k, v in g.items()}
    default_keep = 1.0 - config.DELIVERY_COMMISSION
    out = {}
    for _, r in pos_df.iterrows():
        wk = str(r.get("iso_week") or "")
        tot, dd, ue = _f(r.get("total_incl_gst")), _f(r.get("doordash")), _f(r.get("ubereats"))
        k_ue = keep_map.get(("ubereats", wk), default_keep)
        k_dd = keep_map.get(("doordash", wk), default_keep)
        adj_incl = tot - ue - dd + ue * k_ue + dd * k_dd   # non-delivery + actual delivery net
        key = r.get(period_col)
        out[key] = out.get(key, 0.0) + adj_incl / (1.0 + config.GST_RATE)
    return {k: float(v) for k, v in out.items() if k is not None and str(k) != "nan"}


def pos_breakdown(pos_df, period_col, period_key):
    """Period revenue detail from POS slips, for the sidebar/transparency."""
    sub = pos_df[pos_df[period_col] == period_key] if not pos_df.empty else pos_df
    num = lambda c: float(pd.to_numeric(sub[c], errors="coerce").fillna(0).sum()) if not sub.empty else 0.0
    return {
        "days": int(len(sub)),
        "gross_incl": num("total_incl_gst"),
        "delivery_gross": num("doordash") + num("ubereats"),
        "adjusted_incl": num("adjusted_incl_gst"),
        "adjusted_ex": num("adjusted_ex_gst"),
    }


def _group_price(g):
    """Per-group (item × date) price summary as a Series {amount, qty, unit_price}.

    Prefers the PRINTED per-line unit_price (quantity-weighted) whenever any line in
    the group carries one — that is the authoritative figure for per-kg items, where
    sum(amount)/sum(quantity) would otherwise give a misleading $/carton. Falls back
    to sum(amount)/sum(quantity) when no printed price is present (e.g. older invoices
    saved before unit_price was captured), so historical data is unchanged."""
    q = pd.to_numeric(g["quantity"], errors="coerce")
    a = pd.to_numeric(g["amount"], errors="coerce")
    u = pd.to_numeric(g.get("unit_price"), errors="coerce") if "unit_price" in g \
        else pd.Series(index=g.index, dtype=float)
    tq = float(q[q > 0].sum()) if q.notna().any() else 0.0
    ta = float(a.fillna(0).sum())
    printed = u.notna() & (u > 0) & q.notna() & (q > 0)
    if printed.any():
        w = q[printed]
        up = float((u[printed] * w).sum() / w.sum())  # qty-weighted printed price
    else:
        up = ta / tq if tq > 0 else ta  # legacy: derive from line total ÷ quantity
    return pd.Series({"amount": ta, "qty": tq, "unit_price": up})


def veggie_prices(lines):
    """Long df [item, date, unit_price] — unit price per tracked item per date, from
    Veggies-supplier invoice lines. Uses the printed per-unit (per-kg) price when shown,
    else sum(amount)/sum(quantity). See _group_price."""
    cols = ["item", "date", "unit_price"]
    if lines.empty:
        return pd.DataFrame(columns=cols)
    sub = lines[lines["supplier"] == config.VEGGIES_SUPPLIER].copy()
    if sub.empty:
        return pd.DataFrame(columns=cols)
    sub["item"] = sub["description"].map(config.veggie_item)
    sub = sub[sub["item"].notna()].copy()
    if sub.empty:
        return pd.DataFrame(columns=cols)
    g = (sub.groupby(["item", "invoice_date"])[["quantity", "amount", "unit_price"]]
         .apply(_group_price).reset_index())
    g = g.rename(columns={"invoice_date": "date"})
    return g[cols].sort_values(["item", "date"]).reset_index(drop=True)


def veggie_flux_table(lines):
    """One row per tracked item: latest unit price, daily Δ% (vs previous purchase date),
    weekly Δ% (this ISO-week avg vs prior week avg)."""
    g = veggie_prices(lines)
    rows = []
    for item in config.TRACKED_VEGGIE_ITEMS:
        sub = g[g["item"] == item].sort_values("date")
        if sub.empty:
            rows.append({"Item": item, "Latest $/unit": None, "As of": "—",
                         "Daily Δ": "—", "Weekly Δ": "—"})
            continue
        price = float(sub.iloc[-1]["unit_price"])
        date = str(sub.iloc[-1]["date"])
        daily = "—"
        if len(sub) >= 2:
            prev = float(sub.iloc[-2]["unit_price"])
            if prev:
                daily = f"{(price - prev) / prev * 100:+.1f}%"
        wk = sub.assign(week=pd.to_datetime(sub["date"]).dt.strftime("%G-W%V"))
        wkavg = wk.groupby("week")["unit_price"].mean().sort_index()
        weekly = "—"
        if len(wkavg) >= 2 and wkavg.iloc[-2]:
            weekly = f"{(wkavg.iloc[-1] - wkavg.iloc[-2]) / wkavg.iloc[-2] * 100:+.1f}%"
        rows.append({"Item": item, "Latest $/unit": round(price, 2), "As of": date,
                     "Daily Δ": daily, "Weekly Δ": weekly})
    return pd.DataFrame(rows)


def veggie_increases(lines, min_pct=0.5):
    """Tracked veggies whose latest unit price ROSE. Returns (daily_ups, weekly_ups),
    each a list of (item, pct) sorted biggest-first. daily = latest vs previous
    purchase date; weekly = this ISO week's avg vs the prior week's avg."""
    daily_ups, weekly_ups = [], []
    g = veggie_prices(lines)
    if g.empty:
        return daily_ups, weekly_ups
    for item in config.TRACKED_VEGGIE_ITEMS:
        sub = g[g["item"] == item].sort_values("date")
        if sub.empty:
            continue
        price = float(sub.iloc[-1]["unit_price"])
        if len(sub) >= 2:
            prev = float(sub.iloc[-2]["unit_price"])
            if prev and (price - prev) / prev * 100 >= min_pct:
                daily_ups.append((item, (price - prev) / prev * 100))
        wk = sub.assign(week=pd.to_datetime(sub["date"]).dt.strftime("%G-W%V"))
        wkavg = wk.groupby("week")["unit_price"].mean().sort_index()
        if len(wkavg) >= 2 and wkavg.iloc[-2] and \
                (wkavg.iloc[-1] - wkavg.iloc[-2]) / wkavg.iloc[-2] * 100 >= min_pct:
            weekly_ups.append((item, (wkavg.iloc[-1] - wkavg.iloc[-2]) / wkavg.iloc[-2] * 100))
    daily_ups.sort(key=lambda x: -x[1])
    weekly_ups.sort(key=lambda x: -x[1])
    return daily_ups, weekly_ups


def _item_key(desc):
    """Normalise a line description so the SAME product across invoices groups together
    (lowercase, collapse whitespace, drop trailing pack sizes like '10kg'/'x12')."""
    s = re.sub(r"\s+", " ", str(desc or "").strip().lower())
    return s or None


def item_price_history(lines):
    """Long df [supplier, item, description, date, unit_price, qty, amount]: one row per
    (supplier, normalised item, invoice_date) with the weighted PER-UNIT price. Covers
    every supplier/item. Only lines with a real quantity (>0) are included, so the unit
    price is genuinely per-unit and comparable across deliveries (lines that carry an
    amount but no quantity can't be priced per-unit and are skipped here)."""
    cols = ["supplier", "item", "description", "date", "unit_price", "qty", "amount"]
    if lines.empty:
        return pd.DataFrame(columns=cols)
    sub = lines.copy()
    sub["item"] = sub["description"].map(_item_key)
    sub["qnum"] = pd.to_numeric(sub["quantity"], errors="coerce")
    sub["anum"] = pd.to_numeric(sub["amount"], errors="coerce")
    sub = sub[sub["item"].notna() & sub["anum"].notna() & sub["invoice_date"].notna()
              & (sub["qnum"] > 0)]
    if sub.empty:
        return pd.DataFrame(columns=cols)
    desc = (sub.groupby(["supplier", "item", "invoice_date"])["description"]
            .first().reset_index())
    g = (sub.groupby(["supplier", "item", "invoice_date"])[["quantity", "amount", "unit_price"]]
         .apply(_group_price).reset_index())  # prefers printed per-unit (per-kg) price
    g = g.merge(desc, on=["supplier", "item", "invoice_date"])
    g = g[g["qty"] > 0]
    g = g.rename(columns={"invoice_date": "date"})
    return g[cols].sort_values(["supplier", "item", "date"]).reset_index(drop=True)


def price_anomalies(lines, min_pct=8.0):
    """Items whose latest unit price ROSE >= min_pct vs the previous purchase of the same
    item from the same supplier. df [Supplier, Item, Was, Now, Change, _pct, Last buy, Prev buy],
    biggest rise first — catches silent supplier price creep across the whole catalogue."""
    cols = ["Supplier", "Item", "Was", "Now", "Change", "_pct", "Last buy", "Prev buy"]
    h = item_price_history(lines)
    if h.empty:
        return pd.DataFrame(columns=cols)
    noise = ("rounding", "deposit", "freight", "delivery fee", "surcharge",
             "fee", "credit", "rebate", "adjustment")
    rows = []
    for (sup, _item), sub in h.groupby(["supplier", "item"]):
        if any(k in str(_item).lower() for k in noise):
            continue  # not a product line — skip rounding/deposit/freight etc.
        sub = sub.sort_values("date")
        if len(sub) < 2:
            continue
        now = float(sub.iloc[-1]["unit_price"])
        prev = float(sub.iloc[-2]["unit_price"])
        if prev <= 0 or now < 1.0 or (now - prev) < 0.5:
            continue  # ignore trivially-priced lines and sub-50c moves (% noise)
        pct = (now - prev) / prev * 100
        if pct >= min_pct:
            rows.append({"Supplier": sup, "Item": str(sub.iloc[-1]["description"]),
                         "Was": round(prev, 2), "Now": round(now, 2),
                         "Change": f"+{pct:.0f}%", "_pct": pct,
                         "Last buy": str(sub.iloc[-1]["date"]), "Prev buy": str(sub.iloc[-2]["date"])})
    df = pd.DataFrame(rows, columns=cols)
    return df.sort_values("_pct", ascending=False).reset_index(drop=True) if not df.empty else df


def true_cogs(purchases, opening_stock, closing_stock):
    """Actual food used = opening stock + purchases - closing stock. This is the real
    COGS; invoice spend alone only measures PURCHASES (distorted by over/under-buying)."""
    return float(opening_stock) + float(purchases) - float(closing_stock)


def bas_summary(pos_df, inv_df, months):
    """GST summary for a list of 'YYYY-MM' months (one BAS quarter).
    sales_incl from POS takings (GST on sales = incl/11); gst_credits_est from invoice
    spend × GST_RATE — an ESTIMATE, since GST-free items (fresh produce/meat/etc.)
    overstate it. {sales_incl, gst_on_sales, purchases_ex, gst_credits_est, net_gst}."""
    sales_incl = 0.0
    if pos_df is not None and not pos_df.empty and "month" in pos_df:
        sub = pos_df[pos_df["month"].isin(months)]
        sales_incl = float(pd.to_numeric(sub["total_incl_gst"], errors="coerce").fillna(0).sum())
    purchases_ex = 0.0
    if inv_df is not None and not inv_df.empty and "month" in inv_df:
        sub = inv_df[inv_df["month"].isin(months)]
        purchases_ex = float(pd.to_numeric(sub["total_ex_gst"], errors="coerce").fillna(0).sum())
    gst_on_sales = sales_incl / (1 + config.GST_RATE) * config.GST_RATE
    gst_credits_est = purchases_ex * config.GST_RATE
    return {"sales_incl": sales_incl, "gst_on_sales": gst_on_sales,
            "purchases_ex": purchases_ex, "gst_credits_est": gst_credits_est,
            "net_gst": gst_on_sales - gst_credits_est}


def suggest_stock_items(lines, suppliers=None, top_n=60):
    """[{item, supplier, unit, unit_price}] — most-purchased products with their last
    per-unit price + invoice unit, to pre-fill the stocktake list. If `suppliers` is
    given, only items from those categories are returned (the stocktake tracks only
    Baida/Veggies/Blueseas). Owner then adjusts units/prices (e.g. salmon -> kg/$37.25)."""
    if lines is None or lines.empty:
        return []
    sub = lines.copy()
    if suppliers:
        sub = sub[sub["supplier"].isin(list(suppliers))]
    if sub.empty:
        return []
    sub["k"] = sub["description"].map(_item_key)
    sub["q"] = pd.to_numeric(sub["quantity"], errors="coerce")
    sub["a"] = pd.to_numeric(sub["amount"], errors="coerce")
    sub = sub[sub["k"].notna() & sub["a"].notna()]
    rows = []
    for _k, g in sub.groupby("k"):
        g = g.sort_values("invoice_date")
        last = g.iloc[-1]
        spend = float(g["a"].fillna(0).sum())
        gq = g[g["q"] > 0]
        if not gq.empty:
            ll = gq.iloc[-1]
            up = float(ll["a"]) / float(ll["q"])
            unit = ll.get("unit")
        else:
            up = float(last["a"])
            unit = last.get("unit")
        unit = unit if isinstance(unit, str) and unit else "ea"
        rows.append({"item": str(last["description"]), "supplier": str(last["supplier"]),
                     "unit": unit, "unit_price": round(up, 2), "_spend": spend})
    rows.sort(key=lambda r: -r["_spend"])
    return [{"item": r["item"], "supplier": r["supplier"], "unit": r["unit"],
             "unit_price": r["unit_price"]} for r in rows[:top_n]]


def order_pad(lines, supplier):
    """Order-sheet basis for a supplier: df [Item, Unit, Last $/unit, Avg qty/order,
    Last bought] — one row per item they buy, pre-filled with the last-paid unit price
    and the average quantity per past delivery (a suggested order qty)."""
    cols = ["Item", "Unit", "Last $/unit", "Avg qty/order", "Last bought"]
    if lines.empty:
        return pd.DataFrame(columns=cols)
    sub = lines[(lines["supplier"] == supplier) & lines["description"].notna()].copy()
    if sub.empty:
        return pd.DataFrame(columns=cols)
    sub["item"] = sub["description"].map(_item_key)
    sub["qnum"] = pd.to_numeric(sub["quantity"], errors="coerce")
    sub["anum"] = pd.to_numeric(sub["amount"], errors="coerce")
    rows = []
    for _item, g in sub.groupby("item"):
        g = g.sort_values("invoice_date")
        last = g.iloc[-1]
        gld = g[g["invoice_date"] == last["invoice_date"]]
        amt = float(gld["anum"].fillna(0).sum())
        qty = float(gld["qnum"].fillna(0).sum())
        up = amt / qty if qty > 0 else amt
        avgq = g["qnum"].dropna()
        _u = last.get("unit")
        unit = "" if (_u is None or (isinstance(_u, float) and pd.isna(_u))) else str(_u)
        rows.append({"Item": str(last["description"]), "Unit": unit,
                     "Last $/unit": round(up, 2),
                     "Avg qty/order": round(float(avgq.mean()), 1) if len(avgq) else None,
                     "Last bought": str(last["invoice_date"])})
    return pd.DataFrame(rows, columns=cols).sort_values("Item").reset_index(drop=True)


def pace_projection(p_start, p_end, today, cogs_to_date, rev_to_date, green_pct):
    """Linear end-of-period projection of food spend vs target. Returns None when the
    period hasn't started, is already complete (actuals stand), or has no revenue yet.
    {elapsed,total,frac,proj_rev,proj_cogs,proj_pct,target_cogs,delta}."""
    total = (p_end - p_start).days + 1
    if total <= 0 or today < p_start:
        return None
    elapsed = (min(today, p_end) - p_start).days + 1
    if elapsed >= total:
        return None  # period finished — the dashboard's actuals are the real number
    if not rev_to_date or rev_to_date <= 0:
        return None
    frac = elapsed / total
    proj_rev = rev_to_date / frac
    proj_cogs = cogs_to_date / frac
    proj_pct = (proj_cogs / proj_rev) if proj_rev else None
    target_cogs = proj_rev * green_pct
    return {"elapsed": elapsed, "total": total, "frac": frac, "proj_rev": proj_rev,
            "proj_cogs": proj_cogs, "proj_pct": proj_pct, "target_cogs": target_cogs,
            "delta": proj_cogs - target_cogs}


def baida_tubs(lines, period_col, period_key):
    """Tub + chicken counts for Baida in one period. Quantity = individual chickens,
    so tubs = chickens / per_tub. Also returns the invoice 'TUB DEPOSIT' count as a check.
    {'RSPCA': {'tubs':, 'chickens':}, 'Split': {...}, 'total_tubs':, 'total_chickens':, 'tub_deposit':}"""
    out = {t: {"tubs": 0.0, "chickens": 0.0} for t in config.TUB_TYPES}
    deposit = 0.0
    if not lines.empty:
        sub = lines[(lines["supplier"] == config.BAIDA_SUPPLIER)
                    & (lines[period_col] == period_key)]
        for t, cfg in config.TUB_TYPES.items():
            chickens = float(pd.to_numeric(sub[sub["tub_type"] == t]["quantity"],
                                           errors="coerce").fillna(0).sum())
            out[t] = {"chickens": chickens,
                      "tubs": chickens / cfg["per_tub"] if cfg["per_tub"] else 0.0}
        dep = sub[sub["description"].astype(str).str.lower()
                  .str.contains(config.DEPOSIT_KEYWORD, na=False)]
        deposit = float(pd.to_numeric(dep["quantity"], errors="coerce").fillna(0).sum())
    out["total_tubs"] = sum(out[t]["tubs"] for t in config.TUB_TYPES)
    out["total_chickens"] = sum(out[t]["chickens"] for t in config.TUB_TYPES)
    out["tub_deposit"] = deposit
    return out


# ============ Invoice completeness tracker (learned supplier cadence) ============
# These power the owner's weekly "have all this week's invoices been uploaded?" check.
# The cadence is LEARNED from the invoice history: how often each supplier delivers,
# on which weekdays, and how regular they are — so a week with a missing delivery from
# a normally-weekly supplier gets flagged, while genuinely occasional suppliers don't nag.
WEEKDAY_ABBR = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _week_to_monday(iso_week):
    """'YYYY-Www' -> the date of that ISO week's Monday, or None."""
    try:
        y, w = str(iso_week).split("-W")
        return dt.date.fromisocalendar(int(y), int(w), 1)
    except Exception:
        return None


def _iso_week_str(d):
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def weekdays_label(weekdays):
    """[0,2,4] -> 'Mon, Wed, Fri'."""
    wds = [w for w in (weekdays or []) if isinstance(w, int) and 0 <= w <= 6]
    return ", ".join(WEEKDAY_ABBR[w] for w in sorted(wds)) if wds else "—"


def supplier_cadence(df, recent_weeks=12, today=None):
    """Learn each supplier's delivery pattern from invoice history.

    Returns {supplier: {per_week, weekdays, weeks_active, span_weeks, presence,
                        recent_presence, regular, last_date, last_week, n_invoices}}.
      per_week        — typical invoices per active week (>=1, rounded)
      weekdays        — list of weekday ints (0=Mon) they usually deliver on
      recent_presence — fraction of the last `recent_weeks` completed weeks with a delivery
      regular         — True when they normally deliver every week (so a gap = 'missing')
    """
    today = today or dt.date.today()
    out = {}
    if df is None or df.empty:
        return out
    d = df.copy()
    d["_date"] = pd.to_datetime(d["invoice_date"], errors="coerce")
    d = d[d["_date"].notna()]
    if d.empty:
        return out
    cur_monday = today - dt.timedelta(days=today.weekday())
    recent_keys = {_iso_week_str(cur_monday - dt.timedelta(weeks=i))
                   for i in range(1, recent_weeks + 1)}
    for sup, g in d.groupby("supplier"):
        weeks = sorted({w for w in g["iso_week"].dropna().astype(str)})
        if not weeks:
            continue
        weeks_active = len(weeks)
        n_invoices = int(len(g))
        first_mon, last_mon = _week_to_monday(weeks[0]), _week_to_monday(weeks[-1])
        if first_mon and last_mon:
            span_weeks = int((last_mon - first_mon).days // 7) + 1
        else:
            span_weeks = weeks_active
        presence = weeks_active / span_weeks if span_weeks else 0.0
        recent_hits = len(recent_keys & set(weeks))
        recent_presence = recent_hits / recent_weeks if recent_weeks else 0.0
        # Typical weekdays: those carrying a meaningful share of this supplier's deliveries.
        wd_counts = g["_date"].dt.weekday.value_counts().to_dict()
        thresh = max(1, 0.3 * weeks_active)
        typical_wd = sorted([int(wd) for wd, c in wd_counts.items() if c >= thresh])
        if not typical_wd and wd_counts:
            typical_wd = [int(max(wd_counts, key=wd_counts.get))]
        # Regular = delivers most recent weeks, or a strong all-time cadence with history.
        regular = recent_presence >= 0.5 or (presence >= 0.6 and weeks_active >= 3)
        out[sup] = {
            "per_week": max(1, round(n_invoices / weeks_active)),
            "weekdays": typical_wd,
            "weeks_active": weeks_active,
            "span_weeks": span_weeks,
            "presence": round(presence, 2),
            "recent_presence": round(recent_presence, 2),
            "regular": bool(regular),
            "last_date": str(g["_date"].max().date()),
            "last_week": weeks[-1],
            "n_invoices": n_invoices,
        }
    return out


def weekly_invoice_status(df, iso_week, today=None, cadence=None):
    """Per-supplier upload status for one ISO week, used by the Invoice tracker.

    For each supplier with history (plus any that delivered this week), returns the
    expected vs received count and an AUTO status:
      recorded — received at least as many invoices as expected (and >=1)
      partial  — some received but fewer than the supplier's usual count
      missing  — a normally-regular supplier with nothing this week, past their usual day
      due      — regular supplier, none yet, but their usual delivery day hasn't passed
      upcoming — a future week
      none     — occasional supplier with nothing this week (not flagged)
    The owner's manual tick ('confirmed'/'skipped') is applied in the UI on top of this.
    Rows are returned unsorted; the caller orders them.
    """
    today = today or dt.date.today()
    cad = cadence if cadence is not None else supplier_cadence(df, today=today)
    monday = _week_to_monday(iso_week)
    sunday = (monday + dt.timedelta(days=6)) if monday else None
    cur_monday = today - dt.timedelta(days=today.weekday())

    recv_count, recv_amt = {}, {}
    if df is not None and not df.empty:
        wk = df[df["iso_week"].astype(str) == str(iso_week)]
        if not wk.empty:
            recv_count = wk.groupby("supplier").size().to_dict()
            recv_amt = (wk.assign(_t=pd.to_numeric(wk["total_ex_gst"], errors="coerce"))
                        .groupby("supplier")["_t"].sum().to_dict())

    week_in_future = bool(monday and monday > cur_monday)
    week_past = bool(sunday and sunday < today)
    rows = []
    for sup in sorted(set(cad) | set(recv_count)):
        c = cad.get(sup, {})
        regular = bool(c.get("regular"))
        expected = int(c.get("per_week", 1)) if regular else 0
        received = int(recv_count.get(sup, 0))
        amount = float(recv_amt.get(sup, 0.0) or 0.0)
        weekdays = c.get("weekdays", [])
        last_wd = max(weekdays) if weekdays else 6
        if received >= max(1, expected):
            status = "recorded"
        elif received > 0:
            status = "partial"
        elif regular:
            if week_in_future:
                status = "upcoming"
            elif week_past or today.weekday() > last_wd:
                status = "missing"
            else:
                status = "due"
        else:
            status = "none"
        rows.append({
            "supplier": sup, "expected": expected, "received": received,
            "amount": round(amount, 2), "regular": regular,
            "weekdays": weekdays, "weekdays_label": weekdays_label(weekdays),
            "last_date": c.get("last_date"), "status": status,
        })
    return rows
