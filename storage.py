"""Invoice + revenue store.

Two backends, same interface:
  - Local CSV (default) — zero setup, used for local/trial runs.
  - Supabase (Postgres) — used automatically when SUPABASE_URL + SUPABASE_KEY are
    set (e.g. on Streamlit Cloud), giving durable storage that survives redeploys.

The backend is chosen per call from env vars, so nothing else in the app changes.
"""
import os
import json
import base64
import datetime as dt
import pandas as pd
import config
from config import canonicalize

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
CSV_PATH = os.path.join(DATA_DIR, "invoices.csv")
REV_PATH = os.path.join(DATA_DIR, "revenue.csv")
POS_PATH = os.path.join(DATA_DIR, "pos_days.csv")
LABOUR_PATH = os.path.join(DATA_DIR, "labour.csv")
PAYROLL_SETUP_PATH = os.path.join(DATA_DIR, "payroll_setup.xlsx")
COLUMNS = ["saved_at", "supplier_raw", "supplier", "invoice_date",
           "total_ex_gst", "iso_week", "month", "line_items"]
REV_COLUMNS = ["period_type", "period_key", "revenue", "updated_at"]
LABOUR_COLUMNS = ["period_type", "period_key", "labour_cost", "hours", "updated_at"]
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


def delete_invoice(saved_at):
    """Permanently delete the invoice(s) with this saved_at timestamp."""
    if _use_supabase():
        _client().table("invoices").delete().eq("saved_at", str(saved_at)).execute()
    else:
        _ensure_csv(CSV_PATH, COLUMNS)
        df = pd.read_csv(CSV_PATH)
        df = df[df["saved_at"].astype(str) != str(saved_at)]
        df.to_csv(CSV_PATH, index=False)


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


# ---------- labour (gross wages per period, for labour % + prime cost %) ----------
def set_labour(period_type: str, period_key: str, labour_cost: float, hours: float = 0.0):
    row = {"period_type": period_type, "period_key": period_key,
           "labour_cost": round(float(labour_cost), 2),
           "hours": round(float(hours or 0), 2),
           "updated_at": dt.datetime.now().isoformat(timespec="seconds")}
    if _use_supabase():
        _client().table("labour").upsert(row, on_conflict="period_type,period_key").execute()
    else:
        _ensure_csv(LABOUR_PATH, LABOUR_COLUMNS)
        df = pd.read_csv(LABOUR_PATH)
        df = df[~((df["period_type"] == period_type) & (df["period_key"] == period_key))]
        df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
        df.to_csv(LABOUR_PATH, index=False)
    return row


def labour_map(period_type: str) -> dict:
    """{period_key: {'cost': float, 'hours': float}} for the given period type."""
    if _use_supabase():
        try:
            data = (_client().table("labour").select("period_key,labour_cost,hours")
                    .eq("period_type", period_type).execute().data)
            rows = data or []
        except Exception:
            return {}  # labour table not created in Supabase yet -> degrade, don't crash
    else:
        _ensure_csv(LABOUR_PATH, LABOUR_COLUMNS)
        df = pd.read_csv(LABOUR_PATH)
        if df.empty:
            return {}
        rows = df[df["period_type"] == period_type].to_dict("records")
    out = {}
    for r in rows:
        try:
            cost = float(r.get("labour_cost") or 0)
        except (TypeError, ValueError):
            cost = 0.0
        try:
            hrs = float(r.get("hours") or 0)
        except (TypeError, ValueError):
            hrs = 0.0
        out[r["period_key"]] = {"cost": cost, "hours": hrs}
    return out


# Labour is stored at WEEK grain (period_type='week', key='YYYY-Www'), populated by
# the weekly Tanda-CSV payroll run (or a manual override). Month figures are derived
# by summing the weeks that fall in the month.
def _iso_week_month(iso_week: str):
    """Map 'YYYY-Www' to the 'YYYY-MM' of that ISO week's Thursday (ISO convention)."""
    try:
        y, w = iso_week.split("-W")
        return dt.date.fromisocalendar(int(y), int(w), 4).strftime("%Y-%m")
    except Exception:
        return None


def labour_for_period(mode: str, period_key: str):
    """(cost, hours) for the selected period. Week = direct lookup;
    Month = sum of the weekly rows whose ISO week falls in that month."""
    wk = labour_map("week")
    if mode == "Week":
        v = wk.get(period_key, {})
        return float(v.get("cost", 0.0)), float(v.get("hours", 0.0))
    cost = hours = 0.0
    for iso_week, v in wk.items():
        if _iso_week_month(iso_week) == period_key:
            cost += v["cost"]
            hours += v["hours"]
    return cost, hours


def labour_cost_map_for(mode: str) -> dict:
    """{period_key: cost} across periods, for the COGS%/prime trend in the current mode."""
    wk = labour_map("week")
    if mode == "Week":
        return {k: v["cost"] for k, v in wk.items()}
    out = {}
    for iso_week, v in wk.items():
        m = _iso_week_month(iso_week)
        if m:
            out[m] = out.get(m, 0.0) + v["cost"]
    return out


# ---------- payroll setup (Payroll Setup.xlsx, stored privately for the labour calc) ----------
def save_payroll_setup(filename: str, file_bytes: bytes):
    if _use_supabase():
        row = {"id": 1, "filename": filename,
               "file_b64": base64.b64encode(file_bytes).decode("ascii"),
               "uploaded_at": dt.datetime.now().isoformat(timespec="seconds")}
        _client().table("payroll_setup").upsert(row, on_conflict="id").execute()
    else:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(PAYROLL_SETUP_PATH, "wb") as f:
            f.write(file_bytes)


def load_payroll_setup():
    """Return (filename, file_bytes, uploaded_at) or None if no setup uploaded yet."""
    if _use_supabase():
        try:
            data = _client().table("payroll_setup").select("*").eq("id", 1).execute().data
        except Exception:
            return None  # payroll_setup table not created yet -> degrade
        if not data:
            return None
        r = data[0]
        try:
            b = base64.b64decode(r["file_b64"])
        except Exception:
            return None
        return (r.get("filename") or "Payroll Setup.xlsx", b, r.get("uploaded_at"))
    if not os.path.exists(PAYROLL_SETUP_PATH):
        return None
    with open(PAYROLL_SETUP_PATH, "rb") as f:
        b = f.read()
    ts = dt.datetime.fromtimestamp(os.path.getmtime(PAYROLL_SETUP_PATH)).isoformat(timespec="seconds")
    return ("payroll_setup.xlsx", b, ts)


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
