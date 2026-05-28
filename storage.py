"""Invoice + revenue store.

Two backends, same interface:
  - Local CSV (default) — zero setup, used for local/trial runs.
  - Supabase (Postgres) — used automatically when SUPABASE_URL + SUPABASE_KEY are
    set (e.g. on Streamlit Cloud), giving durable storage that survives redeploys.

The backend is chosen per call from env vars, so nothing else in the app changes.
"""
import os
import json
import datetime as dt
import pandas as pd
import config
from config import canonicalize

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
CSV_PATH = os.path.join(DATA_DIR, "invoices.csv")
REV_PATH = os.path.join(DATA_DIR, "revenue.csv")
POS_PATH = os.path.join(DATA_DIR, "pos_days.csv")
COLUMNS = ["saved_at", "supplier_raw", "supplier", "invoice_date",
           "total_ex_gst", "iso_week", "month", "line_items"]
REV_COLUMNS = ["period_type", "period_key", "revenue", "updated_at"]
POS_COLUMNS = ["date", "iso_week", "month", "total_incl_gst", "doordash", "ubereats",
               "adjusted_incl_gst", "adjusted_ex_gst", "saved_at"]


def iso_week_of(d: dt.date) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


# ---------- backend selection ----------
def _use_supabase() -> bool:
    return bool(os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_KEY"))


_sb = None


def _client():
    global _sb
    if _sb is None:
        from supabase import create_client  # lazy: only needed in cloud mode
        _sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])
    return _sb


def _ensure_csv(path, columns):
    os.makedirs(DATA_DIR, exist_ok=True)
    if not os.path.exists(path):
        pd.DataFrame(columns=columns).to_csv(path, index=False)


# ---------- invoices ----------
def save_invoice(supplier_raw, invoice_date, total_ex_gst, line_items):
    d = pd.to_datetime(invoice_date).date()
    iso_year, iso_week, _ = d.isocalendar()
    row = {
        "saved_at": dt.datetime.now().isoformat(timespec="seconds"),
        "supplier_raw": supplier_raw,
        "supplier": canonicalize(supplier_raw),
        "invoice_date": d.isoformat(),
        "total_ex_gst": round(float(total_ex_gst), 2),
        "iso_week": f"{iso_year}-W{iso_week:02d}",
        "month": d.strftime("%Y-%m"),
        "line_items": json.dumps([li if isinstance(li, dict) else li.model_dump()
                                  for li in line_items]),
    }
    if _use_supabase():
        _client().table("invoices").insert(row).execute()
    else:
        _ensure_csv(CSV_PATH, COLUMNS)
        pd.DataFrame([row]).to_csv(CSV_PATH, mode="a", header=False, index=False)
    return row


def load_invoices() -> pd.DataFrame:
    if _use_supabase():
        data = _client().table("invoices").select("*").execute().data
        df = pd.DataFrame(data, columns=COLUMNS) if data else pd.DataFrame(columns=COLUMNS)
    else:
        _ensure_csv(CSV_PATH, COLUMNS)
        df = pd.read_csv(CSV_PATH)
    if df.empty:
        return pd.DataFrame(columns=COLUMNS)
    # Re-derive canonical supplier from the raw name so config/label changes
    # (e.g. renaming a category) apply to already-saved rows without migration.
    df["supplier"] = df["supplier_raw"].map(canonicalize)
    return df


# ---------- revenue (so COGS% can trend across periods) ----------
def set_revenue(period_type: str, period_key: str, revenue: float):
    row = {"period_type": period_type, "period_key": period_key,
           "revenue": round(float(revenue), 2),
           "updated_at": dt.datetime.now().isoformat(timespec="seconds")}
    if _use_supabase():
        _client().table("revenue").upsert(row, on_conflict="period_type,period_key").execute()
    else:
        _ensure_csv(REV_PATH, REV_COLUMNS)
        df = pd.read_csv(REV_PATH)
        df = df[~((df["period_type"] == period_type) & (df["period_key"] == period_key))]
        df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
        df.to_csv(REV_PATH, index=False)


def revenue_map(period_type: str) -> dict:
    if _use_supabase():
        data = (_client().table("revenue").select("period_key,revenue")
                .eq("period_type", period_type).execute().data)
        return {r["period_key"]: r["revenue"] for r in data}
    _ensure_csv(REV_PATH, REV_COLUMNS)
    df = pd.read_csv(REV_PATH)
    if df.empty:
        return {}
    df = df[df["period_type"] == period_type]
    return dict(zip(df["period_key"], df["revenue"]))


# ---------- POS daily takings (one finalised end-of-day slip per date) ----------
def save_pos_day(date, total_incl_gst, doordash, ubereats):
    d = pd.to_datetime(date).date()
    iso_y, iso_w, _ = d.isocalendar()
    adj_incl, adj_ex = config.delivery_adjust(total_incl_gst, doordash, ubereats)
    row = {
        "date": d.isoformat(),
        "iso_week": f"{iso_y}-W{iso_w:02d}",
        "month": d.strftime("%Y-%m"),
        "total_incl_gst": round(float(total_incl_gst), 2),
        "doordash": round(float(doordash or 0), 2),
        "ubereats": round(float(ubereats or 0), 2),
        "adjusted_incl_gst": adj_incl,
        "adjusted_ex_gst": adj_ex,
        "saved_at": dt.datetime.now().isoformat(timespec="seconds"),
    }
    if _use_supabase():
        _client().table("pos_days").upsert(row, on_conflict="date").execute()
    else:
        _ensure_csv(POS_PATH, POS_COLUMNS)
        df = pd.read_csv(POS_PATH)
        df = df[df["date"] != row["date"]]  # one slip per day
        df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
        df.to_csv(POS_PATH, index=False)
    return row


def load_pos_days() -> pd.DataFrame:
    if _use_supabase():
        data = _client().table("pos_days").select("*").execute().data
        return pd.DataFrame(data, columns=POS_COLUMNS) if data else pd.DataFrame(columns=POS_COLUMNS)
    _ensure_csv(POS_PATH, POS_COLUMNS)
    df = pd.read_csv(POS_PATH)
    return df if not df.empty else pd.DataFrame(columns=POS_COLUMNS)
