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
LABOUR_COLUMNS = ["period_type", "period_key", "labour_cost", "hours",
                  "foh_hours", "boh_hours", "updated_at"]
POS_COLUMNS = ["date", "iso_week", "month", "total_incl_gst", "doordash", "ubereats",
               "bite", "cash", "adjusted_incl_gst", "adjusted_ex_gst", "saved_at"]
FS_PATH = os.path.join(DATA_DIR, "food_safety.csv")
FS_COLUMNS = ["date", "data", "saved_at"]
STOCK_PATH = os.path.join(DATA_DIR, "stocktake.csv")
STOCK_COLUMNS = ["period_key", "stock_value", "updated_at"]
IMG_PATH = os.path.join(DATA_DIR, "invoice_images.csv")
IMG_COLUMNS = ["saved_at", "media_type", "image_b64"]
VAR_PATH = os.path.join(DATA_DIR, "variation_events.csv")
VAR_COLUMNS = ["employee", "shift_date", "weekday", "actual_start", "actual_finish",
               "contracted_start", "kind", "week_ending", "created_at"]
CONTRACT_PATH = os.path.join(DATA_DIR, "contracts.csv")
CONTRACT_COLUMNS = ["employee", "weekday", "start", "finish"]
STOCK_ITEMS_PATH = os.path.join(DATA_DIR, "stock_items.csv")
STOCK_ITEMS_COLUMNS = ["item", "supplier", "unit", "unit_price"]


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
    supplier = canonicalize(supplier_raw)
    ed = config.effective_date(d, supplier)  # bucket by delivery date (BPL Sat -> Mon)
    iso_year, iso_week, _ = ed.isocalendar()
    row = {
        "saved_at": dt.datetime.now().isoformat(timespec="seconds"),
        "supplier_raw": supplier_raw,
        "supplier": supplier,
        "invoice_date": d.isoformat(),
        "total_ex_gst": round(float(total_ex_gst), 2),
        "iso_week": f"{iso_year}-W{iso_week:02d}",
        "month": ed.strftime("%Y-%m"),
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
    # Re-derive the period from the DELIVERY date (BPL Sat order -> Mon delivery),
    # also on load so it corrects already-saved invoices without a migration.
    def _period(row):
        try:
            d = pd.to_datetime(row["invoice_date"]).date()
        except Exception:
            return pd.Series({"iso_week": row.get("iso_week"), "month": row.get("month")})
        ed = config.effective_date(d, row["supplier"])
        y, w, _ = ed.isocalendar()
        return pd.Series({"iso_week": f"{y}-W{w:02d}", "month": ed.strftime("%Y-%m")})
    df[["iso_week", "month"]] = df.apply(_period, axis=1)
    return df


def find_duplicate(supplier, invoice_date, total_ex_gst):
    """First already-saved invoice matching canonical supplier + invoice_date +
    total (to the cent), else None. Used to warn before saving a re-upload."""
    df = load_invoices()
    if df.empty:
        return None
    try:
        d = pd.to_datetime(invoice_date).date().isoformat()
    except Exception:
        return None
    tot = round(float(total_ex_gst), 2)
    m = df[(df["supplier"] == supplier)
           & (df["invoice_date"].astype(str) == d)
           & (pd.to_numeric(df["total_ex_gst"], errors="coerce").round(2) == tot)]
    return m.iloc[0].to_dict() if not m.empty else None


def duplicate_groups(df):
    """Groups of invoices sharing supplier + invoice_date + total (>1 copy),
    each sorted oldest-first, for the duplicate-cleanup tool."""
    if df.empty:
        return []
    g = df.copy()
    g["_tot"] = pd.to_numeric(g["total_ex_gst"], errors="coerce").round(2)
    out = []
    for _, sub in g.groupby(["supplier", "invoice_date", "_tot"]):
        if len(sub) > 1:
            out.append(sub.sort_values("saved_at"))
    return out


def save_invoice_image(saved_at, pages, media_type="image/jpeg"):
    """Keep the original photo(s)/PDF of an invoice (audit / GST trail), in a SEPARATE
    store keyed by saved_at so load_invoices() stays light.

    `pages` may be a single bytes object (with media_type) or a list of
    (bytes, media_type) tuples — ALL pages are kept under the one saved_at, stored as a
    JSON array in image_b64. Older rows hold a single raw-base64 string; the loaders
    below read both formats, so this needs no database migration."""
    if isinstance(pages, (bytes, bytearray)):
        norm = [(bytes(pages), media_type or "image/jpeg")]
    else:
        norm = [(b, mt or "image/jpeg") for (b, mt) in (pages or []) if b]
    if not norm:
        return
    payload = json.dumps([{"media_type": mt, "b64": base64.b64encode(b).decode("ascii")}
                          for b, mt in norm])
    row = {"saved_at": str(saved_at), "media_type": norm[0][1], "image_b64": payload}
    if _use_supabase():
        try:
            _client().table("invoice_images").upsert(row, on_conflict="saved_at").execute()
        except Exception:
            pass  # invoice_images table not created yet -> degrade (invoice still saved)
    else:
        _ensure_csv(IMG_PATH, IMG_COLUMNS)
        df = pd.read_csv(IMG_PATH)
        df = df[df["saved_at"].astype(str) != str(saved_at)]
        df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
        df.to_csv(IMG_PATH, index=False)


def _image_row(saved_at):
    """Raw stored row dict {media_type, image_b64} for an invoice, or None."""
    if _use_supabase():
        try:
            data = (_client().table("invoice_images").select("*")
                    .eq("saved_at", str(saved_at)).execute().data)
        except Exception:
            return None
        return data[0] if data else None
    if not os.path.exists(IMG_PATH):
        return None
    df = pd.read_csv(IMG_PATH)
    m = df[df["saved_at"].astype(str) == str(saved_at)]
    return m.iloc[0].to_dict() if not m.empty else None


def load_invoice_images(saved_at):
    """All stored pages as a list of (bytes, media_type), or []. Reads both the new
    multi-page JSON format and the old single raw-base64 format (so existing invoices
    keep working)."""
    r = _image_row(saved_at)
    if not r:
        return []
    raw = r.get("image_b64")
    if not isinstance(raw, str) or not raw.strip():
        return []
    s = raw.strip()
    if s.startswith("["):  # new format: JSON array of pages
        try:
            return [(base64.b64decode(p["b64"]), p.get("media_type") or "image/jpeg")
                    for p in json.loads(s) if p.get("b64")]
        except Exception:
            return []
    try:  # old format: a single raw-base64 image
        return [(base64.b64decode(s), r.get("media_type") or "image/jpeg")]
    except Exception:
        return []


def load_invoice_image(saved_at):
    """First stored page as (bytes, media_type), or None (back-compat single-page view)."""
    imgs = load_invoice_images(saved_at)
    return imgs[0] if imgs else None


def _delete_invoice_image(saved_at):
    if _use_supabase():
        try:
            _client().table("invoice_images").delete().eq("saved_at", str(saved_at)).execute()
        except Exception:
            pass
    elif os.path.exists(IMG_PATH):
        df = pd.read_csv(IMG_PATH)
        df = df[df["saved_at"].astype(str) != str(saved_at)]
        df.to_csv(IMG_PATH, index=False)


def update_invoice(old_saved_at, supplier_raw, invoice_date, total_ex_gst, line_items):
    """Correct a mis-scanned invoice: delete the old row and re-save the fixed values,
    re-deriving the category + delivery period. Returns the new row."""
    delete_invoice(old_saved_at)
    return save_invoice(supplier_raw, invoice_date, total_ex_gst, line_items)


def delete_invoice(saved_at):
    """Permanently delete the invoice(s) with this saved_at timestamp + its photo."""
    if _use_supabase():
        _client().table("invoices").delete().eq("saved_at", str(saved_at)).execute()
    else:
        _ensure_csv(CSV_PATH, COLUMNS)
        df = pd.read_csv(CSV_PATH)
        df = df[df["saved_at"].astype(str) != str(saved_at)]
        df.to_csv(CSV_PATH, index=False)
    _delete_invoice_image(saved_at)


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


# ---------- part-time contracts (employee fixed days/hours) ----------
def load_contracts() -> dict:
    """{employee: {weekday: (start, finish)}} from the contracts store."""
    if _use_supabase():
        try:
            rows = _client().table("contracts").select("*").execute().data or []
        except Exception:
            return {}
    else:
        _ensure_csv(CONTRACT_PATH, CONTRACT_COLUMNS)
        df = pd.read_csv(CONTRACT_PATH)
        rows = df.to_dict("records") if not df.empty else []
    out = {}
    for r in rows:
        emp = str(r.get("employee") or "").strip()
        wd = str(r.get("weekday") or "").strip()
        if not emp or not wd:
            continue
        out.setdefault(emp, {})[wd] = (str(r.get("start") or "").strip(),
                                       str(r.get("finish") or "").strip())
    return out


def save_contract(employee, days_dict):
    """Replace all rows for one employee. days_dict = {weekday: (start, finish)}."""
    rows = [{"employee": employee, "weekday": wd, "start": s, "finish": f}
            for wd, (s, f) in days_dict.items()]
    if _use_supabase():
        try:
            _client().table("contracts").delete().eq("employee", employee).execute()
            if rows:
                _client().table("contracts").insert(rows).execute()
        except Exception:
            pass
    else:
        _ensure_csv(CONTRACT_PATH, CONTRACT_COLUMNS)
        df = pd.read_csv(CONTRACT_PATH)
        if not df.empty:
            df = df[df["employee"].astype(str) != str(employee)]
        if rows:
            df = pd.concat([df, pd.DataFrame(rows)], ignore_index=True)
        df.to_csv(CONTRACT_PATH, index=False)


def delete_contract(employee):
    if _use_supabase():
        try:
            _client().table("contracts").delete().eq("employee", employee).execute()
        except Exception:
            pass
    else:
        _ensure_csv(CONTRACT_PATH, CONTRACT_COLUMNS)
        df = pd.read_csv(CONTRACT_PATH)
        df = df[df["employee"].astype(str) != str(employee)]
        df.to_csv(CONTRACT_PATH, index=False)


# ---------- part-time variation events (for variation letters) ----------
def save_variation_events(events_by_emp, week_ending):
    """Upsert this week's detected variation events (one row per employee+shift_date),
    so recurring patterns can later be combined across weeks. events_by_emp:
    {employee: [event dicts from variations.detect_variations]}."""
    we = str(week_ending)
    now = dt.datetime.now().isoformat(timespec="seconds")
    rows = []
    for emp, events in events_by_emp.items():
        for e in events:
            rows.append({
                "employee": emp, "shift_date": str(e["date"]), "weekday": e["weekday"],
                "actual_start": e["actual_start"], "actual_finish": e["actual_finish"],
                "contracted_start": e.get("contracted_start") or "", "kind": e["kind"],
                "week_ending": we, "created_at": now})
    if not rows:
        return 0
    if _use_supabase():
        try:
            _client().table("variation_events").upsert(
                rows, on_conflict="employee,shift_date").execute()
        except Exception:
            pass  # table not created yet -> degrade
    else:
        _ensure_csv(VAR_PATH, VAR_COLUMNS)
        df = pd.read_csv(VAR_PATH)
        new = pd.DataFrame(rows)
        if not df.empty:
            keys = set(zip(new["employee"], new["shift_date"]))
            df = df[~df.apply(lambda r: (r["employee"], str(r["shift_date"])) in keys, axis=1)]
        df = pd.concat([df, new], ignore_index=True)
        df.to_csv(VAR_PATH, index=False)
    return len(rows)


def load_variation_events() -> pd.DataFrame:
    if _use_supabase():
        try:
            data = _client().table("variation_events").select("*").execute().data
        except Exception:
            return pd.DataFrame(columns=VAR_COLUMNS)
        return pd.DataFrame(data, columns=VAR_COLUMNS) if data else pd.DataFrame(columns=VAR_COLUMNS)
    _ensure_csv(VAR_PATH, VAR_COLUMNS)
    df = pd.read_csv(VAR_PATH)
    return df if not df.empty else pd.DataFrame(columns=VAR_COLUMNS)


# ---------- stock items (products counted in the weekly stocktake) ----------
def load_stock_items() -> list:
    """[{item, unit, unit_price}] — the products counted each week, with the price per
    their unit (e.g. salmon unit 'kg', unit_price 37.25 -> $37.25/kg)."""
    if _use_supabase():
        try:
            rows = _client().table("stock_items").select("*").execute().data or []
        except Exception:
            return []
    else:
        _ensure_csv(STOCK_ITEMS_PATH, STOCK_ITEMS_COLUMNS)
        df = pd.read_csv(STOCK_ITEMS_PATH)
        rows = df.to_dict("records") if not df.empty else []
    out = []
    for r in rows:
        it = str(r.get("item") or "").strip()
        if not it:
            continue
        try:
            up = float(r.get("unit_price") or 0)
        except (TypeError, ValueError):
            up = 0.0
        out.append({"item": it, "supplier": str(r.get("supplier") or "").strip(),
                    "unit": str(r.get("unit") or "").strip(), "unit_price": up})
    out.sort(key=lambda r: (r["supplier"], r["item"].lower()))
    return out


def save_stock_items(items):
    """Replace the whole stock-item list. items = iterable of
    {item, supplier, unit, unit_price}."""
    rows, seen = [], set()
    for i in items:
        it = str(i.get("item") or "").strip()
        if not it or it.lower() in seen:
            continue
        seen.add(it.lower())
        try:
            up = round(float(i.get("unit_price") or 0), 2)
        except (TypeError, ValueError):
            up = 0.0
        rows.append({"item": it, "supplier": str(i.get("supplier") or "").strip(),
                     "unit": str(i.get("unit") or "").strip(), "unit_price": up})
    if _use_supabase():
        try:
            _client().table("stock_items").delete().neq("item", "").execute()
            if rows:
                _client().table("stock_items").insert(rows).execute()
        except Exception:
            pass
    else:
        _ensure_csv(STOCK_ITEMS_PATH, STOCK_ITEMS_COLUMNS)
        pd.DataFrame(rows, columns=STOCK_ITEMS_COLUMNS).to_csv(STOCK_ITEMS_PATH, index=False)


# ---------- weekly stocktake (closing stock $ value, for TRUE COGS) ----------
def set_stock_value(period_key: str, stock_value: float):
    """Store the end-of-week stock-on-hand $ value (valued at last-paid prices)."""
    row = {"period_key": period_key, "stock_value": round(float(stock_value), 2),
           "updated_at": dt.datetime.now().isoformat(timespec="seconds")}
    if _use_supabase():
        try:
            _client().table("stocktake").upsert(row, on_conflict="period_key").execute()
        except Exception:
            pass  # stocktake table not created yet -> degrade silently
    else:
        _ensure_csv(STOCK_PATH, STOCK_COLUMNS)
        df = pd.read_csv(STOCK_PATH)
        df = df[df["period_key"].astype(str) != str(period_key)]
        df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
        df.to_csv(STOCK_PATH, index=False)
    return row


def stock_value_map() -> dict:
    """{period_key (ISO week): closing stock $ value}."""
    if _use_supabase():
        try:
            data = _client().table("stocktake").select("*").execute().data or []
        except Exception:
            return {}
        return {r["period_key"]: float(r.get("stock_value") or 0) for r in data}
    _ensure_csv(STOCK_PATH, STOCK_COLUMNS)
    df = pd.read_csv(STOCK_PATH)
    if df.empty:
        return {}
    return {str(k): float(v or 0) for k, v in zip(df["period_key"], df["stock_value"])}


# ---------- labour (gross wages per period, for labour % + prime cost %) ----------
def set_labour(period_type: str, period_key: str, labour_cost: float, hours: float = 0.0,
               foh_hours: float = 0.0, boh_hours: float = 0.0):
    row = {"period_type": period_type, "period_key": period_key,
           "labour_cost": round(float(labour_cost), 2),
           "hours": round(float(hours or 0), 2),
           "foh_hours": round(float(foh_hours or 0), 2),
           "boh_hours": round(float(boh_hours or 0), 2),
           "updated_at": dt.datetime.now().isoformat(timespec="seconds")}
    if _use_supabase():
        try:
            _client().table("labour").upsert(row, on_conflict="period_type,period_key").execute()
        except Exception:
            # Older labour table without foh_hours/boh_hours columns -> still save the
            # core fields so the app doesn't crash. Run the ALTERs in supabase_schema.sql
            # to enable FOH/BOH persistence.
            slim = {k: v for k, v in row.items() if k not in ("foh_hours", "boh_hours")}
            _client().table("labour").upsert(slim, on_conflict="period_type,period_key").execute()
    else:
        _ensure_csv(LABOUR_PATH, LABOUR_COLUMNS)
        df = pd.read_csv(LABOUR_PATH)
        df = df[~((df["period_type"] == period_type) & (df["period_key"] == period_key))]
        df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
        df.to_csv(LABOUR_PATH, index=False)
    return row


def labour_map(period_type: str) -> dict:
    """{period_key: {'cost','hours','foh','boh'}} for the given period type.
    Selects '*' so it works whether or not the foh/boh columns exist yet."""
    if _use_supabase():
        try:
            data = (_client().table("labour").select("*")
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

    def _f(v):
        try:
            return float(v or 0)
        except (TypeError, ValueError):
            return 0.0
    out = {}
    for r in rows:
        out[r["period_key"]] = {"cost": _f(r.get("labour_cost")), "hours": _f(r.get("hours")),
                                "foh": _f(r.get("foh_hours")), "boh": _f(r.get("boh_hours"))}
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
    """(cost, hours, foh_hours, boh_hours) for the selected period. Week = direct
    lookup; Month = sum of the weekly rows whose ISO week falls in that month."""
    wk = labour_map("week")
    if mode == "Week":
        v = wk.get(period_key, {})
        return (v.get("cost", 0.0), v.get("hours", 0.0), v.get("foh", 0.0), v.get("boh", 0.0))
    cost = hours = foh = boh = 0.0
    for iso_week, v in wk.items():
        if _iso_week_month(iso_week) == period_key:
            cost += v["cost"]; hours += v["hours"]; foh += v["foh"]; boh += v["boh"]
    return (cost, hours, foh, boh)


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
def save_pos_day(date, total_incl_gst, doordash, ubereats, bite=0.0, cash=0.0):
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
        "bite": round(float(bite or 0), 2),
        "cash": round(float(cash or 0), 2),
        "adjusted_incl_gst": adj_incl,
        "adjusted_ex_gst": adj_ex,
        "saved_at": dt.datetime.now().isoformat(timespec="seconds"),
    }
    if _use_supabase():
        try:
            _client().table("pos_days").upsert(row, on_conflict="date").execute()
        except Exception:
            # Older pos_days table missing the 'bite'/'cash' columns -> save the rest.
            slim = {k: v for k, v in row.items() if k not in ("bite", "cash")}
            _client().table("pos_days").upsert(slim, on_conflict="date").execute()
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


# ---------- food safety daily temperature records (one record per date) ----------
def save_food_safety(date, data: dict):
    d = pd.to_datetime(date).date()
    row = {"date": d.isoformat(), "data": json.dumps(data),
           "saved_at": dt.datetime.now().isoformat(timespec="seconds")}
    if _use_supabase():
        _client().table("food_safety").upsert(row, on_conflict="date").execute()
    else:
        _ensure_csv(FS_PATH, FS_COLUMNS)
        df = pd.read_csv(FS_PATH)
        df = df[df["date"].astype(str) != row["date"]]  # one record per day
        df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
        df.to_csv(FS_PATH, index=False)
    return row


def load_food_safety() -> pd.DataFrame:
    if _use_supabase():
        try:
            data = _client().table("food_safety").select("*").execute().data
        except Exception:
            return pd.DataFrame(columns=FS_COLUMNS)  # table not created yet -> degrade
        return pd.DataFrame(data, columns=FS_COLUMNS) if data else pd.DataFrame(columns=FS_COLUMNS)
    _ensure_csv(FS_PATH, FS_COLUMNS)
    df = pd.read_csv(FS_PATH)
    return df if not df.empty else pd.DataFrame(columns=FS_COLUMNS)


def food_safety_for(date):
    """Return the saved data dict for a date, or None."""
    try:
        d = pd.to_datetime(date).date().isoformat()
    except Exception:
        return None
    df = load_food_safety()
    if df.empty:
        return None
    m = df[df["date"].astype(str) == d]
    if m.empty:
        return None
    raw = m.iloc[0]["data"]
    try:
        return json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        return None
