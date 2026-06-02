"""Aggregation helpers for the dashboard. Pure functions over the invoices DataFrame."""
import re
import json
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
                "amount": it.get("amount"),
                # stored override (from review screen) else detect from description
                "tub_type": it.get("tub_type") or config.tub_type(it.get("description")),
            })
    cols = ["supplier", "invoice_date", "iso_week", "month", "description",
            "quantity", "unit", "amount", "tub_type"]
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


def pos_revenue_map(pos_df, period_col):
    """{period_key: net ex-GST revenue} summed from daily POS slips."""
    if pos_df.empty:
        return {}
    g = pos_df.groupby(period_col)["adjusted_ex_gst"].sum()
    return {k: float(v) for k, v in g.items()}


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


def veggie_prices(lines):
    """Long df [item, date, unit_price] — weighted unit price per tracked item per date,
    from Veggies-supplier invoice lines. unit_price = sum(amount)/sum(quantity)."""
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
    sub["qty"] = pd.to_numeric(sub["quantity"], errors="coerce")
    sub["amt"] = pd.to_numeric(sub["amount"], errors="coerce")
    g = sub.groupby(["item", "invoice_date"]).agg(amt=("amt", "sum"), qty=("qty", "sum")).reset_index()
    g["unit_price"] = g.apply(
        lambda r: r["amt"] / r["qty"] if r["qty"] and r["qty"] > 0 else r["amt"], axis=1)
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
    g = (sub.groupby(["supplier", "item", "invoice_date"])
         .agg(amount=("anum", "sum"), qty=("qnum", "sum"),
              description=("description", "first")).reset_index())
    g = g[g["qty"] > 0]
    g["unit_price"] = g["amount"] / g["qty"]
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
