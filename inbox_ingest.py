"""Headless invoice inbox -> read -> save. Runs on a GitHub Actions cron.

Power Automate watches the invoices mailbox and HTTP-POSTs each email attachment
into the Supabase Storage bucket 'invoice_inbox'. This script (every ~15 min, via
.github/workflows/inbox.yml) reads each new file with the SAME Claude Vision
pipeline the app uses, saves the invoice + its original image to Supabase, then
moves the file into processed/ so it's never double-counted. Nothing to click —
emailed invoices appear in the app on their own.

Design choices for "never miss / 100% accurate":
  - Reuses extract.extract_invoice (Sonnet first read, auto-escalates the shaky
    ones to Opus) + correct_mispriced_lines, so accuracy == the manual upload path.
  - Duplicates (a re-sent email) are detected with the app's own find_duplicate and
    skipped, so the same invoice can't be counted twice.
  - A file that errors (transient network/limit) is LEFT in the inbox so the next
    run retries it — a transient failure never silently drops an invoice.

Requires env / GitHub repo secrets:
  SUPABASE_URL, SUPABASE_KEY (service_role), ANTHROPIC_API_KEY
Run locally against the cloud inbox with:  python inbox_ingest.py
"""
import anthropic

import extract
import storage
from config import canonicalize


def process_one(name, media_type, client):
    """Read one inbox file and save it. Returns ('saved'|'duplicate', supplier, total, conf)."""
    raw = storage.inbox_download(name)
    data = extract.extract_invoice(raw, media_type, client=client)
    inv = data.model_dump()
    # Second pass on any line that doesn't multiply out (price x qty = amount), when
    # this build of extract.py has the correction agent. extract_invoice already
    # escalates shaky reads to Opus, so older builds without it still read accurately.
    if hasattr(extract, "correct_mispriced_lines"):
        inv = extract.correct_mispriced_lines(raw, inv, media_type=media_type, client=client)

    supplier_raw = inv["supplier_name"]
    invoice_date = inv["invoice_date"]
    total = float(inv["total_ex_gst"])
    conf = inv.get("confidence")

    # Skip an invoice we already have (re-sent email), matching the app's dedup rule.
    if storage.find_duplicate(canonicalize(supplier_raw), invoice_date, total):
        return ("duplicate", supplier_raw, total, conf)

    row = storage.save_invoice(supplier_raw, invoice_date, total, inv["line_items"])
    storage.save_invoice_image(row["saved_at"], raw, media_type)
    return ("saved", supplier_raw, total, conf)


def main():
    files = storage.inbox_list()
    if not files:
        print("[inbox] nothing to process")
        return

    print(f"[inbox] {len(files)} file(s) to process")
    client = anthropic.Anthropic()
    saved = dups = failed = 0
    for name, mt in files:
        try:
            status, supplier, total, conf = process_one(name, mt, client)
        except Exception as e:
            # Leave the file in the inbox (don't archive) so the next run retries it.
            failed += 1
            print(f"[inbox] FAILED {name}: {e!r} — left in inbox for retry")
            continue
        storage.inbox_archive(name)
        if status == "saved":
            saved += 1
            print(f"[inbox] saved {name}: {supplier} ${total:,.2f} (confidence {conf})")
        else:
            dups += 1
            print(f"[inbox] duplicate {name}: {supplier} ${total:,.2f} — skipped")

    print(f"[inbox] done: {saved} saved, {dups} duplicate(s), {failed} failed")


if __name__ == "__main__":
    main()
