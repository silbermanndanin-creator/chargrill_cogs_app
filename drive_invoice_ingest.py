"""Drive Catering-folder invoice ingest — runs headless (GitHub Actions cron).

A Power Automate flow watches the Google Drive "Catering" folder (where the owner
files every invoice raised to a catering platform) and copies each new PDF into
  drive_invoices/<file>
of the catering Storage bucket. This script reads each new file with Claude and:

  1. NON-PLATFORM invoices (OLSH, UNSW, Swans… — billed direct, paid direct) are
     moved to drive_invoices/ignored/ unrecorded — the owner's rule: only invoices
     billed to Hampr / Yordar / Eat First belong in the platform receivables.
     The platform is read from INSIDE the PDF (the bill-to), so a typo'd filename
     like "Hampt Rokt …" still lands with Hampr.
  2. Platform invoices are recorded in the drive_invoices table — the app uses it
     to flag delivered platform orders that have NO invoice raised yet.
  3. An invoice matching NO captured catering order (same platform, same inc-GST
     total ±2c, delivered within ±7 days) also creates a catering_orders row, the
     same 'driveback/INV<no>' keyspace the one-off backfill used — so an order
     that predates the order feed (or that the feed missed) still gets counted as
     a receivable, and a re-upload of a backfilled invoice just overwrites its row.

A file that errors is LEFT in place so the next run retries it; handled files move
to drive_invoices/done/. Re-running is safe (everything upserts on source_file).

Requires env / repo secrets: SUPABASE_URL, SUPABASE_KEY, ANTHROPIC_API_KEY.
Run locally:  python drive_invoice_ingest.py
"""
import base64
import datetime as date_mod
import json
import os

import anthropic
import pandas as pd
from pydantic import BaseModel, ValidationError
from supabase import create_client

import storage
from extract import _prep_image, _doc_block, MODEL, ESCALATE_MODEL

BUCKET = os.environ.get("SUPABASE_BUCKET") or "invoices"
FOLDER = "drive_invoices"

# bill-to text (lowercased, substring match) -> the app's canonical platform name
PLATFORMS = {"hampr": "Hampr", "hampt": "Hampr", "yordar": "Yordar",
             "eat first": "Eat First", "eatfirst": "Eat First", "order in": "Eat First",
             "order-in": "Eat First"}

SYSTEM = """You read TAX INVOICES issued by Chargrill Charlies Coogee (an Australian \
venue) for catering it supplied. They all use the venue's one template: "Tax Invoice" \
header, the venue's details top-left, the BILL-TO name on the right (a catering \
platform like "Hampr" / "Yordar" / "Eat First / Order In", or a direct customer like \
"UNSW"), an invoice number, a date (Australian day-first), one or a few description \
lines naming the end customer (e.g. "Rokt", "Maddox", "DHL"), and a TOTAL (inc-GST) / \
BALANCE DUE.

Return ONLY a single JSON object — no prose, no markdown fences — with EXACTLY these keys:
{
  "invoice_no": "string",      // the invoice number as printed, digits only ("1061")
  "bill_to": "string",         // who the invoice is billed to, exactly as printed
  "company": "string",         // the end customer named in the description lines; "" if none
  "invoice_date": "YYYY-MM-DD",// the invoice date, day-first ("28/05/2026" -> 2026-05-28)
  "total_inc_gst": number,     // TOTAL (inc-GST) / BALANCE DUE, plain decimal
  "confidence": "high" | "medium" | "low"
}
Every field is required: "" for missing text, 0 for missing numbers — never omit one."""


class DriveInvoice(BaseModel):
    invoice_no: str
    bill_to: str
    company: str = ""
    invoice_date: str = ""
    total_inc_gst: float = 0
    confidence: str = "medium"


def _extract_json(text: str) -> dict:
    """Pull the JSON object out of the model's reply (tolerates ```fences``` / prose)."""
    s = text.strip()
    if "```" in s:
        s = s.split("```", 2)[1] if s.count("```") >= 2 else s
        if s.lstrip().lower().startswith("json"):
            s = s.lstrip()[4:]
    i, j = s.find("{"), s.rfind("}")
    if i != -1 and j != -1 and j > i:
        s = s[i:j + 1]
    return json.loads(s)


def _read(client, model, content) -> DriveInvoice:
    resp = client.messages.create(
        model=model,
        max_tokens=1000,
        system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": content}],
    )
    text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
    return DriveInvoice(**_extract_json(text))


def extract_drive_invoice(raw: bytes, client=None) -> DriveInvoice:
    """One invoice PDF -> DriveInvoice. Sonnet first, Opus when not 'high' confidence
    (same escalation as every other extractor in this app)."""
    client = client or anthropic.Anthropic()
    fb, mt = _prep_image(raw, "application/pdf")
    content = [_doc_block(base64.standard_b64encode(fb).decode("utf-8"), mt),
               {"type": "text", "text": "Extract this invoice."}]
    try:
        data = _read(client, MODEL, content)
    except (json.JSONDecodeError, ValidationError, anthropic.APIError):
        data = None
    if data is None or data.confidence != "high":
        try:
            data = _read(client, ESCALATE_MODEL, content)
        except Exception:
            if data is None:
                raise
    return data


def platform_of(bill_to: str):
    """Canonical platform for a bill-to name, or None for a direct customer."""
    t = (bill_to or "").lower()
    for key, name in PLATFORMS.items():
        if key in t:
            return name
    return None


def order_exists(orders_df, platform, total, date_iso) -> bool:
    """True if a captured catering order already covers this invoice: same platform,
    same inc-GST total (±2c — Hampr remittances showed 1c rounding), delivered within
    ±7 days (invoices are usually raised on, or days after, the delivery)."""
    if orders_df is None or orders_df.empty or not date_iso:
        return False
    try:
        d = date_mod.date.fromisoformat(str(date_iso)[:10])
    except ValueError:
        return False
    for _, r in orders_df[orders_df["platform"] == platform].iterrows():
        try:
            tot = float(r["items_total"] or 0)
            dd = date_mod.date.fromisoformat(str(r["deliver_date"])[:10])
        except (TypeError, ValueError):
            continue
        if abs(tot - float(total)) <= 0.02 and abs((dd - d).days) <= 7:
            return True
    return False


def main():
    url, key = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_KEY")
    if not (url and key):
        raise SystemExit("[drive-inv] SUPABASE_URL / SUPABASE_KEY not set — nothing to do.")
    sb = create_client(url, key)
    bucket = sb.storage.from_(BUCKET)

    try:
        entries = bucket.list(FOLDER, {"limit": 1000,
                                       "sortBy": {"column": "created_at", "order": "asc"}}) or []
    except Exception as e:
        print(f"[drive-inv] list {FOLDER}/ failed (folder may not exist yet): {e}")
        return
    names = [e["name"] for e in entries
             if isinstance(e, dict) and e.get("name") and e.get("id") is not None]
    if not names:
        print("[drive-inv] nothing to process")
        return

    print(f"[drive-inv] {len(names)} file(s) to process")
    client = anthropic.Anthropic()
    orders_df = storage.load_catering_orders()
    saved = ignored = added = failed = 0
    for name in names:
        path = f"{FOLDER}/{name}"
        if not name.lower().endswith(".pdf"):
            bucket.move(path, f"{FOLDER}/ignored/{name}")
            ignored += 1
            print(f"[drive-inv] ignored {name}: not a PDF -> ignored/")
            continue
        if storage.drive_invoice_already_ingested(path):
            bucket.move(path, f"{FOLDER}/done/{name}")
            print(f"[drive-inv] skip   {name}: already recorded -> done/")
            continue
        try:
            raw = bucket.download(path)
            inv = extract_drive_invoice(raw, client=client)
            platform = platform_of(inv.bill_to)
            if platform is None:
                # Billed direct (OLSH, UNSW, Swans…) — paid direct, not a platform
                # receivable. Parked unrecorded, recoverable from the bucket.
                bucket.move(path, f"{FOLDER}/ignored/{name}")
                ignored += 1
                print(f"[drive-inv] ignored {name}: billed to {inv.bill_to!r} "
                      "(direct customer) -> ignored/")
                continue
            storage.save_drive_invoice({
                "invoice_no": inv.invoice_no, "platform": platform,
                "company": inv.company, "invoice_date": inv.invoice_date,
                "total_inc_gst": inv.total_inc_gst, "confidence": inv.confidence,
            }, source_file=path)
            saved += 1
            line = (f"INV{inv.invoice_no} {platform} {inv.company} "
                    f"{inv.invoice_date} ${inv.total_inc_gst:,.2f}")
            if not order_exists(orders_df, platform, inv.total_inc_gst, inv.invoice_date):
                row = storage.save_catering_order({
                    "platform": platform, "order_type": "delivery",
                    "company": inv.company, "deliver_date": inv.invoice_date,
                    "order_ref": "", "line_items": [],
                    "items_total": inv.total_inc_gst, "confidence": inv.confidence,
                }, source_file=f"driveback/INV{inv.invoice_no or name}")
                orders_df = pd.concat([orders_df, pd.DataFrame([row])], ignore_index=True)
                added += 1
                print(f"[drive-inv] saved  {line} — no captured order, added as receivable")
            else:
                print(f"[drive-inv] saved  {line} — matches a captured order")
            bucket.move(path, f"{FOLDER}/done/{name}")
        except Exception as e:
            failed += 1
            print(f"[drive-inv] FAILED {name}: {e!r} — left in place for retry")

    print(f"[drive-inv] done: {saved} recorded ({added} new receivable(s)), "
          f"{ignored} ignored, {failed} failed")


if __name__ == "__main__":
    main()
