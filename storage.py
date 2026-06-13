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
import re
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
SHIFT_CSV_PATH = os.path.join(DATA_DIR, "shift_csv.csv")
LETTERS_PATH = os.path.join(DATA_DIR, "letters.csv")
LETTERS_COLUMNS = ["filename", "employee", "label", "file_b64", "saved_at"]
EMP_DETAILS_PATH = os.path.join(DATA_DIR, "emp_details.csv")
EMP_DETAILS_COLUMNS = ["employee", "agreement_date", "address1", "address2", "updated_at"]
COLUMNS = ["saved_at", "supplier_raw", "supplier", "invoice_date",
           "total_ex_gst", "iso_week", "month", "line_items", "source_file"]
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
PACKAGING_PATH = os.path.join(DATA_DIR, "packaging_counts.csv")
PACKAGING_COLUMNS = ["item", "on_hand", "updated_at"]
DRINKS_PATH = os.path.join(DATA_DIR, "drinks_counts.csv")
DRINKS_COLUMNS = ["item", "on_hand", "updated_at"]
CHECKS_PATH = os.path.join(DATA_DIR, "invoice_checks.csv")
CHECKS_COLUMNS = ["period_key", "supplier", "state", "note", "updated_at"]
EMP_OVR_PATH = os.path.join(DATA_DIR, "employee_overrides.csv")
EMP_OVR_COLUMNS = ["employee", "employment_type", "section", "flat_rate", "updated_at"]
CATERING_PATH = os.path.join(DATA_DIR, "catering.csv")
CATERING_COLUMNS = ["saved_at", "platform", "order_type", "company", "deliver_date",
                    "deliver_time", "headcount", "contact_name", "address", "phone",
                    "order_ref", "line_items", "items_total", "confidence", "source_file"]
# Bucket the catering files live in (same default the ingest Action uses). `or` so an
# empty SUPABASE_BUCKET still falls back rather than becoming an invalid "" bucket name.
CATERING_BUCKET = os.environ.get("SUPABASE_BUCKET") or "invoices"
REMIT_PATH = os.path.join(DATA_DIR, "platform_remittances.csv")
REMIT_COLUMNS = ["saved_at", "platform", "doc_ref", "doc_date", "total_paid",
                 "lines", "confidence", "source_file"]
DRIVE_INV_PATH = os.path.join(DATA_DIR, "drive_invoices.csv")
DRIVE_INV_COLUMNS = ["saved_at", "invoice_no", "platform", "company", "invoice_date",
                     "total_inc_gst", "confidence", "source_file"]


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
def save_invoice(supplier_raw, invoice_date, total_ex_gst, line_items, source_file=None):
    """Save one invoice. `source_file` (the inbox bucket key it was read from) is the
    durable dedupe key: when set, the row is UPSERTED on it, so re-reading the same
    bucket file — e.g. the inbox cron retrying a file whose move to processed/ failed —
    overwrites its own row instead of inserting a duplicate. Manual / in-app uploads
    have no bucket file and pass None (a plain insert; find_duplicate guards those)."""
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
        "source_file": source_file,
    }
    if _use_supabase():
        tbl = _client().table("invoices")
        if source_file:
            tbl.upsert(row, on_conflict="source_file").execute()
        else:
            tbl.insert(row).execute()
    else:
        _ensure_csv(CSV_PATH, COLUMNS)
        if source_file:
            df = pd.read_csv(CSV_PATH)
            if "source_file" in df.columns:
                df = df[df["source_file"].astype(str) != str(source_file)]
            df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
            df.to_csv(CSV_PATH, index=False)
        else:
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


# ---------- invoice inbox bucket (emailed invoices land here via Power Automate) ----------
# Power Automate watches the invoices mailbox and HTTP-POSTs each attachment into this
# Supabase Storage bucket; inbox_ingest.py (GitHub Actions cron) then reads + saves them.
# PDF-ONLY by design: real supplier invoices arrive as PDFs, while signature logos,
# inline images and other email junk arrive as images — those are parked in ignored/
# and never read, so only PDF invoices ever reach the app or the review queue.
# Cloud-only by nature — there's no local-CSV equivalent, so these no-op without Supabase.
INBOX_BUCKET = "invoice_inbox"


def _is_pdf(name) -> bool:
    return str(name).lower().endswith(".pdf")


# Freemail domains where the mailbox name (not the domain) identifies the sender.
_FREEMAIL = {"gmail", "googlemail", "hotmail", "outlook", "live", "msn", "yahoo",
             "icloud", "me", "aol", "proton", "protonmail", "bigpond", "optusnet",
             "tpg", "iinet", "westnet"}


def sender_name(addr) -> str:
    """Short human name pulled from an email address: the company domain for business
    senders ('accounts@bidfood.com.au' -> 'bidfood'), or the mailbox name for
    personal/freemail senders ('jo.bloggs@gmail.com' -> 'jo.bloggs')."""
    local, _, domain = str(addr).strip().strip("<>").partition("@")
    root = domain.split(".")[0].strip().lower()
    if not root or root in _FREEMAIL:
        return local.strip() or str(addr).strip()
    return root


def display_name(name) -> str:
    """Human-readable form of a bucket file name.

    Supabase Storage rejects keys with characters outside a small ASCII set (a curly
    apostrophe in an attachment name 400s the upload), so the Power Automate flows
    upload as '<prefix>_b64_<urlsafe-base64-of-original-name>.pdf'. Decode that back to
    the original attachment name for anything a human reads (the app's review queue,
    ingest/triage logs). Plain names (older uploads) pass through unchanged.

    Newer flows prepend the sender's email address inside the encoded name as
    '<sender@addr>|<attachment name>' (see POWER_AUTOMATE_SETUP.md) — rendered here as
    'sender — attachment.pdf' so every file says who emailed it."""
    decoded = _decode_name(name)
    if decoded is None:
        return str(name)
    sender, sep, rest = decoded.partition("|")
    if sep and "@" in sender:
        return f"{sender_name(sender)} — {rest.strip()}"
    return decoded


def _decode_name(name):
    """The decoded '<sender>|<attachment>' (or plain attachment) string packed into a
    '<prefix>_b64_<urlsafe-base64>.pdf' bucket key, or None for a plain/older name.
    The prefix is upload-specific (a per-email message id on current uploads, an epoch
    timestamp on older ones) and is underscore-free, so the first '_b64_' is the real
    delimiter even though the urlsafe token after it may contain underscores."""
    m = re.match(r"^[^_]+_b64_(.+)\.pdf$", str(name), re.IGNORECASE)
    if not m:
        return None
    token = m.group(1).replace("-", "+").replace("_", "/")
    token += "=" * (-len(token) % 4)
    try:
        return base64.b64decode(token).decode("utf-8", "replace")
    except Exception:
        return None


def sender_of(name):
    """The raw sender email packed into a bucket file name ('<sender>|<attachment>'),
    or None for older uploads that don't encode one. Lets the inbox decide whether a
    file is from a known supplier before paying for a Claude read."""
    decoded = _decode_name(name)
    if decoded is None:
        return None
    sender, sep, _ = decoded.partition("|")
    return sender.strip() if (sep and "@" in sender) else None


def encode_name(display, prefix=None) -> str:
    """Inverse of display_name(): pack any human-readable name into the ASCII-safe
    '<prefix>_b64_<urlsafe-base64>.pdf' form that Supabase Storage accepts as a key.
    `prefix` must be underscore-free (it delimits the token); defaults to an epoch
    timestamp for app-side renames, where uniqueness — not idempotency — is wanted."""
    token = base64.urlsafe_b64encode(str(display).encode("utf-8")).decode("ascii").rstrip("=")
    if prefix is None:
        prefix = str(int(dt.datetime.now().timestamp()))
    return f"{prefix}_b64_{token}.pdf"


def relabel(name, label) -> str:
    """Bucket name for `name` with a human-readable label stitched in front of the
    original attachment name, e.g. 'Statement · Bidfood — scan0042.pdf' — so the
    review queue is identifiable without downloading anything. The original upload
    prefix is kept so a rename-in-place doesn't change the file's identity."""
    m = re.match(r"^([^_]+)_b64_", str(name))
    return encode_name(f"{label} — {display_name(name)}", m.group(1) if m else None)


# Supabase Storage list() returns only 100 entries by default — with a backlog in the
# bucket that silently hid everything past the first 100. List big batches, oldest first,
# so the longest-waiting invoices always clear before newer ones.
_LIST_OPTS = {"limit": 1000, "sortBy": {"column": "created_at", "order": "asc"}}


def inbox_list():
    """New PDF invoice files sitting at the root of the inbox bucket, as
    [(name, media_type)], oldest first. Files we've already handled live in the
    processed/ / review/ / ignored/ subfolders and are not returned. Empty when
    Supabase isn't configured (the inbox is a cloud-only feature)."""
    if not _use_supabase():
        return []
    items = _client().storage.from_(INBOX_BUCKET).list("", _LIST_OPTS) or []
    out = []
    for it in items:
        name = it.get("name") if isinstance(it, dict) else None
        if name and _is_pdf(name):
            out.append((name, "application/pdf"))
    return out


def inbox_list_other():
    """Non-PDF files sitting at the root of the inbox bucket (signature logos, inline
    images, calendar invites…), as [name]. The ingest sweeps these into ignored/ so the
    inbox stays clean without ever reading them. Folder pseudo-entries (processed/,
    review/, ignored/) come back from Storage list() with a null id — skipped."""
    if not _use_supabase():
        return []
    items = _client().storage.from_(INBOX_BUCKET).list("", _LIST_OPTS) or []
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        name = it.get("name")
        if name and not _is_pdf(name) and it.get("id") is not None:
            out.append(name)
    return out


def inbox_download(name) -> bytes:
    """Raw bytes of one file in the inbox bucket."""
    return _client().storage.from_(INBOX_BUCKET).download(name)


def _inbox_move(path, folder, new_name=None):
    """Move a handled file (root name or subfolder path like 'review/x.pdf') into a
    subfolder of the inbox bucket so it isn't read again, optionally renaming it.
    Best-effort: on a name clash (same file moved before) suffix with a timestamp."""
    dest = f"{folder}/{new_name or os.path.basename(str(path))}"
    bucket = _client().storage.from_(INBOX_BUCKET)
    try:
        bucket.move(path, dest)
    except Exception:
        try:
            stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
            bucket.move(path, f"{dest}.{stamp}")
        except Exception:
            pass  # leave it in place rather than crash; next run will retry the move


def inbox_archive(name):
    """Move a successfully-saved invoice file into processed/ so it's never read twice."""
    _inbox_move(name, "processed")


def inbox_review(name, label=None):
    """Move a file that ISN'T a savable COGS invoice (a statement, credit note, or an
    unrecognised / non-COGS supplier) into review/ — surfaced in the app's review queue
    for a human to accept or dismiss, but never counted until accepted. When the
    triage knows WHY (a `label` like 'Statement · Bidfood'), it's stitched into the
    filename so the queue is identifiable without downloading anything."""
    _inbox_move(name, "review", new_name=relabel(name, label) if label else None)


def inbox_ignore(name):
    """Move a non-PDF attachment into ignored/ — only PDF invoices are processed;
    everything else is parked unread (kept, in case something ever needs digging out)."""
    _inbox_move(name, "ignored")


# ---------- review queue (review/ subfolder of the inbox bucket, shown in the app) ----------
def review_list():
    """PDFs waiting in review/ of the inbox bucket, newest first, as
    [(name, received 'YYYY-MM-DD HH:MM')]. These are emailed files the ingest didn't
    auto-save (statements, credit notes, unrecognised suppliers) for a human to decide on."""
    if not _use_supabase():
        return []
    items = _client().storage.from_(INBOX_BUCKET).list("review", _LIST_OPTS) or []
    out = []
    for it in items:
        name = it.get("name") if isinstance(it, dict) else None
        if name and _is_pdf(name):
            when = str(it.get("created_at") or "")[:16].replace("T", " ")
            out.append((name, when))
    out.sort(key=lambda t: t[1], reverse=True)
    return out


def review_download(name) -> bytes:
    """Raw bytes of one file in the review queue."""
    return _client().storage.from_(INBOX_BUCKET).download(f"review/{name}")


def review_accept(name):
    """An accepted review file has been saved as an invoice — archive it to processed/
    so the review queue (and the next ingest run) never sees it again."""
    _inbox_move(f"review/{name}", "processed")


def review_relabel(name, label):
    """Rename a file already sitting in review/ so its name carries a classification
    label (see relabel) — used by review_triage.py to make the backlog identifiable."""
    _inbox_move(f"review/{name}", "review", new_name=relabel(name, label))


def review_dismiss(name):
    """A review file the owner doesn't want counted (junk, statement, already entered) —
    park it in ignored/. Nothing is deleted, so it can always be dug out of the bucket."""
    _inbox_move(f"review/{name}", "ignored")


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


# ---------- packaging order pad (on-hand counts, item -> qty on hand) ----------
def load_packaging_counts() -> dict:
    """{item name: on-hand qty} — the last saved packaging stocktake."""
    if _use_supabase():
        try:
            rows = _client().table("packaging_counts").select("*").execute().data or []
        except Exception:
            return {}
    else:
        _ensure_csv(PACKAGING_PATH, PACKAGING_COLUMNS)
        df = pd.read_csv(PACKAGING_PATH)
        rows = df.to_dict("records") if not df.empty else []
    out = {}
    for r in rows:
        it = str(r.get("item") or "").strip()
        if not it:
            continue
        try:
            out[it] = float(r.get("on_hand") or 0)
        except (TypeError, ValueError):
            out[it] = 0.0
    return out


def save_packaging_counts(counts: dict):
    """Replace the whole packaging on-hand map. counts = {item: on_hand}."""
    now = dt.datetime.now().isoformat(timespec="seconds")
    rows, seen = [], set()
    for it, oh in (counts or {}).items():
        name = str(it or "").strip()
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        try:
            val = round(float(oh or 0), 2)
        except (TypeError, ValueError):
            val = 0.0
        rows.append({"item": name, "on_hand": val, "updated_at": now})
    if _use_supabase():
        try:
            _client().table("packaging_counts").delete().neq("item", "").execute()
            if rows:
                _client().table("packaging_counts").insert(rows).execute()
        except Exception:
            pass  # table not created yet -> degrade silently
    else:
        _ensure_csv(PACKAGING_PATH, PACKAGING_COLUMNS)
        pd.DataFrame(rows, columns=PACKAGING_COLUMNS).to_csv(PACKAGING_PATH, index=False)


# ---------- drinks order pad (on-hand counts, item -> qty on hand) ----------
def load_drinks_counts() -> dict:
    """{item name: on-hand qty} — the last saved drinks stocktake."""
    if _use_supabase():
        try:
            rows = _client().table("drinks_counts").select("*").execute().data or []
        except Exception:
            return {}
    else:
        _ensure_csv(DRINKS_PATH, DRINKS_COLUMNS)
        df = pd.read_csv(DRINKS_PATH)
        rows = df.to_dict("records") if not df.empty else []
    out = {}
    for r in rows:
        it = str(r.get("item") or "").strip()
        if not it:
            continue
        try:
            out[it] = float(r.get("on_hand") or 0)
        except (TypeError, ValueError):
            out[it] = 0.0
    return out


def save_drinks_counts(counts: dict):
    """Replace the whole drinks on-hand map. counts = {item: on_hand}."""
    now = dt.datetime.now().isoformat(timespec="seconds")
    rows, seen = [], set()
    for it, oh in (counts or {}).items():
        name = str(it or "").strip()
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        try:
            val = round(float(oh or 0), 2)
        except (TypeError, ValueError):
            val = 0.0
        rows.append({"item": name, "on_hand": val, "updated_at": now})
    if _use_supabase():
        try:
            _client().table("drinks_counts").delete().neq("item", "").execute()
            if rows:
                _client().table("drinks_counts").insert(rows).execute()
        except Exception:
            pass  # table not created yet -> degrade silently
    else:
        _ensure_csv(DRINKS_PATH, DRINKS_COLUMNS)
        pd.DataFrame(rows, columns=DRINKS_COLUMNS).to_csv(DRINKS_PATH, index=False)


# ---------- invoice tracker ticks (owner's weekly "all invoices in" confirmation) ----------
# One row per (ISO week, supplier). state = 'confirmed' (owner ticked this supplier's
# deliveries as all uploaded for the week) or 'skipped' (supplier not coming this week,
# so don't flag it as missing). A falsy state removes the tick (back to auto detection).
def set_invoice_check(period_key: str, supplier: str, state: str, note: str = ""):
    pk, sup = str(period_key), str(supplier)
    if not state:
        return _delete_invoice_check(pk, sup)
    row = {"period_key": pk, "supplier": sup, "state": str(state),
           "note": str(note or ""),
           "updated_at": dt.datetime.now().isoformat(timespec="seconds")}
    if _use_supabase():
        try:
            _client().table("invoice_checks").upsert(
                row, on_conflict="period_key,supplier").execute()
        except Exception:
            pass  # invoice_checks table not created yet -> degrade silently
    else:
        _ensure_csv(CHECKS_PATH, CHECKS_COLUMNS)
        df = pd.read_csv(CHECKS_PATH)
        if not df.empty:
            df = df[~((df["period_key"].astype(str) == pk)
                      & (df["supplier"].astype(str) == sup))]
        df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
        df.to_csv(CHECKS_PATH, index=False)
    return row


def _delete_invoice_check(period_key: str, supplier: str):
    pk, sup = str(period_key), str(supplier)
    if _use_supabase():
        try:
            (_client().table("invoice_checks").delete()
             .eq("period_key", pk).eq("supplier", sup).execute())
        except Exception:
            pass
    elif os.path.exists(CHECKS_PATH):
        df = pd.read_csv(CHECKS_PATH)
        df = df[~((df["period_key"].astype(str) == pk)
                  & (df["supplier"].astype(str) == sup))]
        df.to_csv(CHECKS_PATH, index=False)


def invoice_checks_for(period_key: str) -> dict:
    """{supplier: {'state','note','updated_at'}} of the owner's ticks for one ISO week."""
    pk = str(period_key)
    if _use_supabase():
        try:
            rows = (_client().table("invoice_checks").select("*")
                    .eq("period_key", pk).execute().data) or []
        except Exception:
            return {}
    else:
        _ensure_csv(CHECKS_PATH, CHECKS_COLUMNS)
        df = pd.read_csv(CHECKS_PATH)
        rows = (df[df["period_key"].astype(str) == pk].to_dict("records")
                if not df.empty else [])
    def _txt(v):
        return "" if v is None or (isinstance(v, float) and pd.isna(v)) else str(v)
    out = {}
    for r in rows:
        sup = _txt(r.get("supplier")).strip()
        if sup:
            out[sup] = {"state": _txt(r.get("state")), "note": _txt(r.get("note")),
                        "updated_at": r.get("updated_at")}
    return out


# ---------- employee classification overrides (change FT/PT/Casual without re-uploading setup) ----------
def set_employee_override(employee, employment_type="", section="", flat_rate=None):
    """Override a setup-sheet employee's classification (keyed by display name). One row per
    employee; replaces any existing override."""
    emp = str(employee or "").strip()
    if not emp:
        return
    row = {"employee": emp, "employment_type": str(employment_type or ""),
           "section": str(section or ""),
           # NULL (not "") when absent — flat_rate is a NUMERIC column; "" would be rejected.
           "flat_rate": (None if flat_rate in (None, "") else round(float(flat_rate), 2)),
           "updated_at": dt.datetime.now().isoformat(timespec="seconds")}
    if _use_supabase():
        try:
            _client().table("employee_overrides").upsert(row, on_conflict="employee").execute()
        except Exception as e:
            return str(e)  # surfaced by the caller (missing table, type mismatch, etc.)
    else:
        _ensure_csv(EMP_OVR_PATH, EMP_OVR_COLUMNS)
        df = pd.read_csv(EMP_OVR_PATH)
        if not df.empty:
            df = df[df["employee"].astype(str) != emp]
        df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
        df.to_csv(EMP_OVR_PATH, index=False)
    return None


def delete_employee_override(employee):
    emp = str(employee or "").strip()
    if _use_supabase():
        try:
            _client().table("employee_overrides").delete().eq("employee", emp).execute()
        except Exception:
            pass
    elif os.path.exists(EMP_OVR_PATH):
        df = pd.read_csv(EMP_OVR_PATH)
        df = df[df["employee"].astype(str) != emp]
        df.to_csv(EMP_OVR_PATH, index=False)


def employee_overrides() -> dict:
    """{employee display name: {'employment_type','section','flat_rate'}} — flat_rate float or None."""
    if _use_supabase():
        try:
            rows = _client().table("employee_overrides").select("*").execute().data or []
        except Exception:
            return {}
    else:
        _ensure_csv(EMP_OVR_PATH, EMP_OVR_COLUMNS)
        df = pd.read_csv(EMP_OVR_PATH)
        rows = df.to_dict("records") if not df.empty else []

    def _txt(v):
        return "" if v is None or (isinstance(v, float) and pd.isna(v)) else str(v).strip()
    out = {}
    for r in rows:
        emp = _txt(r.get("employee"))
        if not emp:
            continue
        fr = r.get("flat_rate")
        try:
            fr = None if fr in (None, "") or (isinstance(fr, float) and pd.isna(fr)) else float(fr)
        except (TypeError, ValueError):
            fr = None
        out[emp] = {"employment_type": _txt(r.get("employment_type")),
                    "section": _txt(r.get("section")), "flat_rate": fr}
    return out


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


# ---------- latest weekly Tanda shift CSV (so Variations reuses the Labour upload) ----------
# Single latest row (id=1), like payroll_setup. Persisted so the Variations tab can reuse
# the shift CSV uploaded in the Labour tab even after a redeploy/new session. Personal data
# — lives only in the DB / local data dir, never in git.
def save_shift_csv(filename: str, csv_bytes: bytes, week_ending: str = ""):
    if _use_supabase():
        row = {"id": 1, "filename": str(filename or "shift.csv"),
               "csv_b64": base64.b64encode(csv_bytes).decode("ascii"),
               "week_ending": str(week_ending or ""),
               "uploaded_at": dt.datetime.now().isoformat(timespec="seconds")}
        try:
            _client().table("shift_csv").upsert(row, on_conflict="id").execute()
        except Exception:
            pass  # shift_csv table not created yet -> degrade (session reuse still works)
    else:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(SHIFT_CSV_PATH, "wb") as f:
            f.write(csv_bytes)


def load_shift_csv():
    """Return (filename, csv_bytes, week_ending, uploaded_at) or None if none saved yet."""
    if _use_supabase():
        try:
            data = _client().table("shift_csv").select("*").eq("id", 1).execute().data
        except Exception:
            return None  # table not created yet -> degrade
        if not data:
            return None
        r = data[0]
        try:
            b = base64.b64decode(r["csv_b64"])
        except Exception:
            return None
        return (r.get("filename") or "shift.csv", b, r.get("week_ending") or "", r.get("uploaded_at"))
    if not os.path.exists(SHIFT_CSV_PATH):
        return None
    with open(SHIFT_CSV_PATH, "rb") as f:
        b = f.read()
    ts = dt.datetime.fromtimestamp(os.path.getmtime(SHIFT_CSV_PATH)).isoformat(timespec="seconds")
    return ("shift.csv", b, "", ts)


# ---------- generated variation letters, kept IN THE APP (download anytime) ----------
# Stored as base64 .docx, one row per filename (re-saving the same letter updates it).
def save_letter(filename: str, employee: str, data: bytes, label: str = "") -> bool:
    row = {"filename": str(filename), "employee": str(employee or ""), "label": str(label or ""),
           "file_b64": base64.b64encode(data).decode("ascii"),
           "saved_at": dt.datetime.now().isoformat(timespec="seconds")}
    if _use_supabase():
        try:
            _client().table("letters").upsert(row, on_conflict="filename").execute()
        except Exception:
            return False  # letters table not created yet
    else:
        _ensure_csv(LETTERS_PATH, LETTERS_COLUMNS)
        df = pd.read_csv(LETTERS_PATH)
        if not df.empty:
            df = df[df["filename"].astype(str) != str(filename)]
        df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
        df.to_csv(LETTERS_PATH, index=False)
    return True


def list_letters() -> list:
    """[{filename, employee, label, saved_at}] newest first — metadata only (no file blob)."""
    if _use_supabase():
        try:
            rows = (_client().table("letters").select("filename,employee,label,saved_at")
                    .execute().data) or []
        except Exception:
            return []
    else:
        _ensure_csv(LETTERS_PATH, LETTERS_COLUMNS)
        df = pd.read_csv(LETTERS_PATH)
        rows = (df.drop(columns=[c for c in ["file_b64"] if c in df.columns])
                .to_dict("records") if not df.empty else [])

    def _txt(v):
        return "" if v is None or (isinstance(v, float) and pd.isna(v)) else str(v)
    out = [{"filename": _txt(r.get("filename")), "employee": _txt(r.get("employee")),
            "label": _txt(r.get("label")), "saved_at": _txt(r.get("saved_at"))}
           for r in rows if _txt(r.get("filename"))]
    out.sort(key=lambda r: r["saved_at"], reverse=True)
    return out


def load_letter(filename: str):
    """The .docx bytes for one saved letter, or None."""
    if _use_supabase():
        try:
            data = (_client().table("letters").select("file_b64")
                    .eq("filename", str(filename)).execute().data)
        except Exception:
            return None
        if not data:
            return None
        b = data[0].get("file_b64")
    else:
        if not os.path.exists(LETTERS_PATH):
            return None
        df = pd.read_csv(LETTERS_PATH)
        m = df[df["filename"].astype(str) == str(filename)]
        if m.empty:
            return None
        b = m.iloc[0].get("file_b64")
    try:
        return base64.b64decode(b)
    except Exception:
        return None


def delete_letter(filename: str):
    if _use_supabase():
        try:
            _client().table("letters").delete().eq("filename", str(filename)).execute()
        except Exception:
            pass
    elif os.path.exists(LETTERS_PATH):
        df = pd.read_csv(LETTERS_PATH)
        df = df[df["filename"].astype(str) != str(filename)]
        df.to_csv(LETTERS_PATH, index=False)


# ---------- per-employee letter details (agreement date + address), kept in the DB ----------
def save_emp_detail(employee, agreement_date="", address1="", address2=""):
    emp = str(employee or "").strip()
    if not emp:
        return
    row = {"employee": emp, "agreement_date": str(agreement_date or ""),
           "address1": str(address1 or ""), "address2": str(address2 or ""),
           "updated_at": dt.datetime.now().isoformat(timespec="seconds")}
    if _use_supabase():
        try:
            _client().table("emp_details").upsert(row, on_conflict="employee").execute()
        except Exception:
            pass  # emp_details table not created yet
    else:
        _ensure_csv(EMP_DETAILS_PATH, EMP_DETAILS_COLUMNS)
        df = pd.read_csv(EMP_DETAILS_PATH)
        if not df.empty:
            df = df[df["employee"].astype(str) != emp]
        df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
        df.to_csv(EMP_DETAILS_PATH, index=False)


def emp_details() -> dict:
    """{employee: {'agreement_date','address1','address2'}} for filling variation letters."""
    if _use_supabase():
        try:
            rows = _client().table("emp_details").select("*").execute().data or []
        except Exception:
            return {}
    else:
        _ensure_csv(EMP_DETAILS_PATH, EMP_DETAILS_COLUMNS)
        df = pd.read_csv(EMP_DETAILS_PATH)
        rows = df.to_dict("records") if not df.empty else []

    def _t(v):
        return "" if v is None or (isinstance(v, float) and pd.isna(v)) else str(v).strip()
    out = {}
    for r in rows:
        emp = _t(r.get("employee"))
        if emp:
            out[emp] = {"agreement_date": _t(r.get("agreement_date")),
                        "address1": _t(r.get("address1")), "address2": _t(r.get("address2"))}
    return out


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


# ---------- catering orders (Hampr / Eat First / Yordar / Online Catering) ----------
def save_catering_order(order, source_file: str):
    """Upsert one catering order, keyed by source_file (the bucket filename) so the
    ingest Action can re-run safely without creating duplicates. `order` may be a
    catering_extract.CateringOrder or a plain dict with the same fields."""
    o = order.model_dump() if hasattr(order, "model_dump") else dict(order)
    items = o.get("line_items") or []
    items = [li if isinstance(li, dict) else li.model_dump() for li in items]
    hc = o.get("headcount")
    try:
        hc = int(hc) if hc not in (None, "") else None
    except (TypeError, ValueError):
        hc = None
    row = {
        "saved_at": dt.datetime.now().isoformat(timespec="seconds"),
        "platform": o.get("platform"),
        "order_type": o.get("order_type"),
        "company": o.get("company"),
        "deliver_date": o.get("deliver_date"),
        "deliver_time": o.get("deliver_time"),
        "headcount": hc,
        "contact_name": o.get("contact_name"),
        "address": o.get("address"),
        "phone": o.get("phone"),
        "order_ref": o.get("order_ref"),
        "line_items": json.dumps(items),
        "items_total": round(float(o.get("items_total") or 0), 2),
        "confidence": o.get("confidence"),
        "source_file": source_file,
    }
    order_ref = str(o.get("order_ref") or "").strip()
    platform = o.get("platform")
    if _use_supabase():
        # A revised order (same platform + order_ref, but arriving as a NEW file) should
        # REPLACE the earlier version rather than pile up beside it — delete the old row(s)
        # first, then upsert the new one. Only when there's an order_ref to match on; an
        # order with no ref falls back to plain per-file dedup so unrelated orders are safe.
        if order_ref:
            try:
                (_client().table("catering_orders").delete()
                 .eq("platform", platform).eq("order_ref", order_ref)
                 .neq("source_file", source_file).execute())
            except Exception:
                pass
        try:
            _client().table("catering_orders").upsert(row, on_conflict="source_file").execute()
        except Exception:
            # Older catering_orders table without order_type/headcount/company -> save the
            # rest, so a missing ALTER degrades instead of crashing.
            slim = {k: v for k, v in row.items()
                    if k not in ("order_type", "headcount", "company")}
            _client().table("catering_orders").upsert(slim, on_conflict="source_file").execute()
    else:
        _ensure_csv(CATERING_PATH, CATERING_COLUMNS)
        df = pd.read_csv(CATERING_PATH)
        df = df[df["source_file"].astype(str) != source_file]  # one row per source file
        if order_ref:  # also drop any earlier version of the same order
            df = df[~((df["order_ref"].astype(str) == order_ref)
                      & (df["platform"].astype(str) == str(platform)))]
        df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
        df.to_csv(CATERING_PATH, index=False)
    return row


def load_catering_orders() -> pd.DataFrame:
    if _use_supabase():
        try:
            data = _client().table("catering_orders").select("*").execute().data
        except Exception:
            return pd.DataFrame(columns=CATERING_COLUMNS)  # table not created yet -> degrade
        return (pd.DataFrame(data, columns=CATERING_COLUMNS) if data
                else pd.DataFrame(columns=CATERING_COLUMNS))
    _ensure_csv(CATERING_PATH, CATERING_COLUMNS)
    df = pd.read_csv(CATERING_PATH)
    return df if not df.empty else pd.DataFrame(columns=CATERING_COLUMNS)


def catering_file_bytes(source_file: str):
    """The original catering file (PDF / HTML email body) from Storage, so the app can
    offer it as a download. After ingest the file is archived under catering/done/<...>;
    try there first, then the pre-ingest path. Returns bytes, or None if unavailable."""
    if not (source_file and _use_supabase()):
        return None
    bucket = _client().storage.from_(CATERING_BUCKET)
    done = source_file.replace("catering/", "catering/done/", 1)
    for path in (done, source_file):
        try:
            data = bucket.download(path)
            if data:
                return data
        except Exception:
            continue
    return None


def catering_already_ingested(source_file: str) -> bool:
    """True if a catering order with this source_file is already saved (lets the ingest
    Action skip work it's already done)."""
    if _use_supabase():
        try:
            data = (_client().table("catering_orders").select("source_file")
                    .eq("source_file", source_file).limit(1).execute().data)
            return bool(data)
        except Exception:
            return False
    if not os.path.exists(CATERING_PATH):
        return False
    df = pd.read_csv(CATERING_PATH)
    return not df.empty and (df["source_file"].astype(str) == source_file).any()


# ---------- our invoices to the catering platforms (mirrored from the Drive folder) ----------
# A Power Automate flow copies each new PDF in the Google Drive "Catering" folder into
# drive_invoices/ of the catering bucket; drive_invoice_ingest.py (GitHub Actions cron)
# reads the platform ones and records them here, so the app can flag delivered platform
# orders that have NO invoice raised yet and keep the receivables complete.
def save_drive_invoice(inv: dict, source_file: str):
    """Upsert one of our platform invoices, keyed by source_file (the bucket path) so
    the ingest Action can re-run safely without creating duplicates."""
    row = {
        "saved_at": dt.datetime.now().isoformat(timespec="seconds"),
        "invoice_no": str(inv.get("invoice_no") or ""),
        "platform": inv.get("platform"),
        "company": inv.get("company") or "",
        "invoice_date": inv.get("invoice_date") or "",
        "total_inc_gst": round(float(inv.get("total_inc_gst") or 0), 2),
        "confidence": inv.get("confidence"),
        "source_file": source_file,
    }
    if _use_supabase():
        _client().table("drive_invoices").upsert(row, on_conflict="source_file").execute()
    else:
        _ensure_csv(DRIVE_INV_PATH, DRIVE_INV_COLUMNS)
        df = pd.read_csv(DRIVE_INV_PATH)
        df = df[df["source_file"].astype(str) != source_file]
        df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
        df.to_csv(DRIVE_INV_PATH, index=False)
    return row


def load_drive_invoices() -> pd.DataFrame:
    if _use_supabase():
        try:
            data = _client().table("drive_invoices").select("*").execute().data
        except Exception:
            return pd.DataFrame(columns=DRIVE_INV_COLUMNS)  # table not created yet -> degrade
        return (pd.DataFrame(data, columns=DRIVE_INV_COLUMNS) if data
                else pd.DataFrame(columns=DRIVE_INV_COLUMNS))
    _ensure_csv(DRIVE_INV_PATH, DRIVE_INV_COLUMNS)
    df = pd.read_csv(DRIVE_INV_PATH)
    return df if not df.empty else pd.DataFrame(columns=DRIVE_INV_COLUMNS)


def drive_invoice_already_ingested(source_file: str) -> bool:
    """True if this bucket file is already recorded (lets the ingest skip re-reads)."""
    if _use_supabase():
        try:
            data = (_client().table("drive_invoices").select("source_file")
                    .eq("source_file", source_file).limit(1).execute().data)
            return bool(data)
        except Exception:
            return False
    if not os.path.exists(DRIVE_INV_PATH):
        return False
    df = pd.read_csv(DRIVE_INV_PATH)
    return not df.empty and (df["source_file"].astype(str) == source_file).any()


# ---------- platform remittances (Hampr remittance advice / Yordar RGI / Eat First RCTI) ----------
def save_platform_remittance(doc, source_file: str):
    """Upsert one platform payment document, keyed by source_file (the bucket filename)
    so the ingest Action can re-run safely without creating duplicates. `doc` may be a
    remittance_extract.RemittanceDoc or a plain dict with the same fields."""
    d = doc.model_dump() if hasattr(doc, "model_dump") else dict(doc)
    lines = d.get("lines") or []
    lines = [li if isinstance(li, dict) else li.model_dump() for li in lines]
    row = {
        "saved_at": dt.datetime.now().isoformat(timespec="seconds"),
        "platform": d.get("platform"),
        "doc_ref": d.get("doc_ref"),
        "doc_date": d.get("doc_date"),
        "total_paid": round(float(d.get("total_paid") or 0), 2),
        "lines": json.dumps(lines),
        "confidence": d.get("confidence"),
        "source_file": source_file,
    }
    doc_ref = str(d.get("doc_ref") or "").strip()
    platform = d.get("platform")
    if _use_supabase():
        # A re-issued document (same platform + doc_ref, but arriving as a NEW file)
        # should REPLACE the earlier version rather than double-count the payment.
        # Only when there's a doc_ref to match on (Hampr remittances have none — those
        # fall back to plain per-file dedup).
        if doc_ref:
            try:
                (_client().table("platform_remittances").delete()
                 .eq("platform", platform).eq("doc_ref", doc_ref)
                 .neq("source_file", source_file).execute())
            except Exception:
                pass
        _client().table("platform_remittances").upsert(
            row, on_conflict="source_file").execute()
    else:
        _ensure_csv(REMIT_PATH, REMIT_COLUMNS)
        df = pd.read_csv(REMIT_PATH)
        df = df[df["source_file"].astype(str) != source_file]  # one row per source file
        if doc_ref:  # also drop any earlier version of the same document
            df = df[~((df["doc_ref"].astype(str) == doc_ref)
                      & (df["platform"].astype(str) == str(platform)))]
        df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
        df.to_csv(REMIT_PATH, index=False)
    return row


def load_platform_remittances() -> pd.DataFrame:
    if _use_supabase():
        try:
            data = _client().table("platform_remittances").select("*").execute().data
        except Exception:
            return pd.DataFrame(columns=REMIT_COLUMNS)  # table not created yet -> degrade
        return (pd.DataFrame(data, columns=REMIT_COLUMNS) if data
                else pd.DataFrame(columns=REMIT_COLUMNS))
    _ensure_csv(REMIT_PATH, REMIT_COLUMNS)
    df = pd.read_csv(REMIT_PATH)
    return df if not df.empty else pd.DataFrame(columns=REMIT_COLUMNS)


def remittance_file_bytes(source_file: str):
    """The original remittance PDF from Storage, so the app can offer it as a download.
    After ingest the file is archived under remittance/done/<...>; try there first, then
    the pre-ingest path. Returns bytes, or None if unavailable."""
    if not (source_file and _use_supabase()):
        return None
    bucket = _client().storage.from_(CATERING_BUCKET)
    done = source_file.replace("remittance/", "remittance/done/", 1)
    for path in (done, source_file):
        try:
            data = bucket.download(path)
            if data:
                return data
        except Exception:
            continue
    return None


def remittance_already_ingested(source_file: str) -> bool:
    """True if a remittance with this source_file is already saved (lets the ingest
    Action skip work it's already done)."""
    if _use_supabase():
        try:
            data = (_client().table("platform_remittances").select("source_file")
                    .eq("source_file", source_file).limit(1).execute().data)
            return bool(data)
        except Exception:
            return False
    if not os.path.exists(REMIT_PATH):
        return False
    df = pd.read_csv(REMIT_PATH)
    return not df.empty and (df["source_file"].astype(str) == source_file).any()
