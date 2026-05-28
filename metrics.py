"""Aggregation helpers for the dashboard. Pure functions over the invoices DataFrame."""
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
                "supplier": r["supplier"], "iso_week": r["iso_week"], "month": r["month"],
                "description": it.get("description"),
                "quantity": it.get("quantity"),
                "unit": norm_unit(it.get("unit")),
                "amount": it.get("amount"),
                # stored override (from review screen) else detect from description
                "tub_type": it.get("tub_type") or config.tub_type(it.get("description")),
            })
    cols = ["supplier", "iso_week", "month", "description",
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
