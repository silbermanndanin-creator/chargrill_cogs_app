"""Headless invoice inbox -> read -> save. Runs on a GitHub Actions cron.

Power Automate watches the invoices mailbox and HTTP-POSTs each email attachment
into the Supabase Storage bucket 'invoice_inbox'. This script (every ~15 min, via
.github/workflows/inbox.yml) reads each new file with the SAME Claude Vision
pipeline the app uses, saves the invoice + its original image to Supabase, then
moves the file into processed/ so it's never double-counted. Nothing to click —
emailed invoices appear in the app on their own.

Design choices for "never miss / 100% accurate":
  - PDF-ONLY: real supplier invoices arrive as PDF attachments. Anything else that
    rides along on an email (signature logos, inline images, calendar invites) is swept
    into ignored/ unread — so only PDFs ever reach processed/ or review/.
  - TRIAGE FIRST (extract.classify_document): suppliers also email statements (which
    sum a whole month into one big total), credit notes and the odd non-COGS expense.
    Only a single invoice from a recognised COGS supplier (config.SUPPLIERS) is saved;
    everything else is moved to review/ in the bucket — captured, never counted.
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
import hashlib

import anthropic

import config
import extract
import storage
from config import canonicalize


def _route(document_type, supplier_name):
    """Decide what to do with a file from its triage. Returns (action, reason) where
    action is 'save' (a real COGS supplier invoice) or 'review' (everything else —
    statements, credit notes, and unrecognised / non-COGS suppliers go to review/).

    Pure logic (no I/O) so it's unit-testable without calling Claude."""
    dt = (document_type or "").strip().lower()
    if dt != "invoice":
        return ("review", f"not an invoice (document_type={dt or 'unknown'})")
    if canonicalize(supplier_name) == config.FALLBACK_SUPPLIER:
        return ("review", f"unrecognised / non-COGS supplier: {supplier_name!r}")
    return ("save", "")


def process_one(name, media_type, client, raw=None):
    """Triage one inbox file, then save it if it's a real COGS invoice.
    Returns (status, supplier, total, conf, reason) with status in
    'saved' | 'duplicate' | 'review'."""
    if raw is None:
        raw = storage.inbox_download(name)

    # 1) Cheap triage FIRST — skip statements / non-COGS before paying for a full read.
    triage = extract.classify_document(raw, media_type, client=client)
    action, reason = _route(triage.document_type, triage.supplier_name)
    if action == "review":
        return ("review", triage.supplier_name, 0.0, triage.confidence, reason)

    # 2) Full Claude Vision extraction (same pipeline as the app's manual upload).
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

    # 3) Re-check the COGS gate against the FULL read's supplier name (more reliable than
    # the triage read) — catches a non-COGS invoice the triage let through.
    action2, reason2 = _route("invoice", supplier_raw)
    if action2 == "review":
        return ("review", supplier_raw, total, conf, reason2)

    # 4) Skip an invoice we already have (re-sent email), matching the app's dedup rule.
    if storage.find_duplicate(canonicalize(supplier_raw), invoice_date, total):
        return ("duplicate", supplier_raw, total, conf, "")

    row = storage.save_invoice(supplier_raw, invoice_date, total, inv["line_items"])
    storage.save_invoice_image(row["saved_at"], raw, media_type)
    return ("saved", supplier_raw, total, conf, "")


def main():
    # Sweep non-PDF junk (signature logos, inline images…) into ignored/ unread —
    # only PDF invoices are ever processed, so review/ and processed/ stay PDF-only.
    for name in storage.inbox_list_other():
        storage.inbox_ignore(name)
        print(f"[inbox] ignored {name}: not a PDF -> ignored/")

    files = storage.inbox_list()
    if not files:
        print("[inbox] nothing to process")
        return

    print(f"[inbox] {len(files)} file(s) to process")
    client = anthropic.Anthropic()
    saved = dups = reviewed = failed = ignored = 0
    # Exact byte-duplicates (the same attachment uploaded under several names —
    # the backlog from before the flows used deterministic filenames) are detected
    # by hash BEFORE the Claude read, so each unique document is paid for once.
    seen_hashes = {}  # sha256 -> first file name with that content
    for name, mt in files:
        try:
            raw = storage.inbox_download(name)
        except Exception as e:
            failed += 1
            print(f"[inbox] FAILED {name}: {e!r} — left in inbox for retry")
            continue
        digest = hashlib.sha256(raw).hexdigest()
        first = seen_hashes.get(digest)
        if first:
            storage.inbox_ignore(name)
            ignored += 1
            print(f"[inbox] ignored {name}: exact copy of {first} -> ignored/")
            continue
        seen_hashes[digest] = name
        try:
            status, supplier, total, conf, reason = process_one(name, mt, client, raw=raw)
        except Exception as e:
            # Leave the file in the inbox (don't archive) so the next run retries it.
            failed += 1
            print(f"[inbox] FAILED {name}: {e!r} — left in inbox for retry")
            continue
        if status == "review":
            storage.inbox_review(name)
            reviewed += 1
            print(f"[inbox] review {name}: {supplier} — {reason} -> review/")
            continue
        storage.inbox_archive(name)
        if status == "saved":
            saved += 1
            print(f"[inbox] saved {name}: {supplier} ${total:,.2f} (confidence {conf})")
        else:
            dups += 1
            print(f"[inbox] duplicate {name}: {supplier} ${total:,.2f} — skipped")

    print(f"[inbox] done: {saved} saved, {dups} duplicate(s), {reviewed} for review, "
          f"{ignored} exact cop(ies) ignored, {failed} failed")


if __name__ == "__main__":
    main()
