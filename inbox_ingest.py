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
  - SENDER GATE FIRST (config.supplier_for_sender): the upload name carries the sender
    email, so mail from anyone who isn't a known supplier is moved to review/ UNREAD —
    we never pay Claude to read a newsletter. Only our suppliers' mail reaches the triage.
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


# Catering-platform orders (Hampr / Eat First / Yordar / Online Catering) have their
# OWN pipeline (catering_ingest.py -> the Catering tab) and must never be counted as
# supplier invoices or clutter review/ — any that stray into the invoices mailbox are
# parked in ignored/. 'yorder' covers a common misspelling on order documents.
CATERING_KEYWORDS = ("hampr", "eat first", "eatfirst", "yordar", "yorder", "online catering")


def _is_catering(text) -> bool:
    t = (text or "").lower()
    return any(k in t for k in CATERING_KEYWORDS)


def review_label(document_type, supplier_name):
    """Short human tag for WHY a file goes to review — e.g. 'Statement · Bidfood' or
    'Invoice · Joe's Produce (unrecognised supplier)'. storage.inbox_review stitches it
    into the stored filename so the app's review queue (and any download) is
    identifiable without opening the PDF."""
    doc = (document_type or "").strip().lower() or "unknown"
    sup = (supplier_name or "").strip() or "unknown supplier"
    if doc == "invoice":
        return f"Invoice · {sup} (unrecognised supplier)"
    return f"{doc.replace('_', ' ').title()} · {sup}"


def _route(document_type, supplier_name):
    """Decide what to do with a file from its triage. Returns (action, reason, label)
    where action is 'save' (a real COGS supplier invoice), 'ignore' (a catering-platform
    document — the catering pipeline's job, parked unprocessed) or 'review'
    (everything else — statements, credit notes, unrecognised / non-COGS suppliers).
    label (review only, else None) is the filename tag from review_label.

    Pure logic (no I/O) so it's unit-testable without calling Claude."""
    if _is_catering(supplier_name):
        return ("ignore",
                f"catering platform {supplier_name!r} — handled by the catering pipeline",
                None)
    dt = (document_type or "").strip().lower()
    if dt != "invoice":
        return ("review", f"not an invoice (document_type={dt or 'unknown'})",
                review_label(dt, supplier_name))
    if canonicalize(supplier_name) == config.FALLBACK_SUPPLIER:
        return ("review", f"unrecognised / non-COGS supplier: {supplier_name!r}",
                review_label(dt, supplier_name))
    return ("save", "", None)


def process_one(name, media_type, client, raw=None):
    """Triage one inbox file, then save it if it's a real COGS invoice.
    Returns (status, supplier, total, conf, reason, label) with status in
    'saved' | 'duplicate' | 'review'; label is the review filename tag (or None)."""
    if raw is None:
        raw = storage.inbox_download(name)

    # 1) Cheap triage FIRST — skip statements / non-COGS before paying for a full read.
    triage = extract.classify_document(raw, media_type, client=client)
    action, reason, label = _route(triage.document_type, triage.supplier_name)
    if action != "save":
        return (action, triage.supplier_name, 0.0, triage.confidence, reason, label)

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
    action2, reason2, label2 = _route("invoice", supplier_raw)
    if action2 != "save":
        return (action2, supplier_raw, total, conf, reason2, label2)

    # 4) Skip an invoice we already have (re-sent email), matching the app's dedup rule.
    if storage.find_duplicate(canonicalize(supplier_raw), invoice_date, total):
        return ("duplicate", supplier_raw, total, conf, "", None)

    # source_file=name stamps the bucket key onto the row and upserts on it, so if this
    # file's move to processed/ fails and the next run re-reads it, it overwrites its own
    # row instead of inserting a duplicate — the durable backstop behind find_duplicate,
    # whose content match can miss when Claude re-reads the same scan slightly differently.
    row = storage.save_invoice(supplier_raw, invoice_date, total, inv["line_items"],
                               source_file=name)
    storage.save_invoice_image(row["saved_at"], raw, media_type)
    return ("saved", supplier_raw, total, conf, "", None)


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
    saved = dups = reviewed = failed = ignored = nonsupplier = notpdf = 0
    # Exact byte-duplicates (the same attachment uploaded under several names —
    # the backlog from before the flows used deterministic filenames) are detected
    # by hash BEFORE the Claude read, so each unique document is paid for once.
    seen_hashes = {}  # sha256 -> first file name with that content
    for name, mt in files:
        disp = storage.display_name(name)  # original attachment name for the log
        try:
            raw = storage.inbox_download(name)
        except Exception as e:
            failed += 1
            print(f"[inbox] FAILED {disp}: {e!r} — left in inbox for retry")
            continue
        # Content gate BEFORE any paid read: Power Automate names every attachment '.pdf',
        # so non-PDF junk (inline images, email banners, calendar .ics) rides in as a fake
        # PDF and slips past the extension sweep. A real PDF starts with the '%PDF' magic
        # bytes — anything else is moved to ignored/ unread, so we never pay Claude for junk.
        if b"%PDF" not in raw[:1024]:
            storage.inbox_ignore(name)
            notpdf += 1
            print(f"[inbox] ignored {disp}: not a real PDF (no %PDF header) -> ignored/ (no read)")
            continue
        digest = hashlib.sha256(raw).hexdigest()
        first = seen_hashes.get(digest)
        if first:
            storage.inbox_ignore(name)
            ignored += 1
            print(f"[inbox] ignored {disp}: exact copy of {storage.display_name(first)} -> ignored/")
            continue
        seen_hashes[digest] = name
        # Sender gate BEFORE any Claude read: the upload name carries the sender email, so
        # mail from anyone who isn't a known supplier (newsletters, the odd PDF on a normal
        # email) goes straight to review/ unread — we only pay to extract our suppliers'
        # invoices. A human can still rescue a real invoice from review/. Older uploads with
        # no encoded sender fall through to the full triage, so nothing is missed.
        sender = storage.sender_of(name)
        if sender and config.supplier_for_sender(sender) is None:
            storage.inbox_review(name, label=f"Non-supplier sender · {storage.sender_name(sender)}")
            nonsupplier += 1
            print(f"[inbox] review {disp}: {sender} is not a known supplier -> review/ (no read)")
            continue
        try:
            status, supplier, total, conf, reason, label = process_one(name, mt, client, raw=raw)
        except Exception as e:
            # Leave the file in the inbox (don't archive) so the next run retries it.
            failed += 1
            print(f"[inbox] FAILED {disp}: {e!r} — left in inbox for retry")
            continue
        if status == "ignore":
            storage.inbox_ignore(name)
            ignored += 1
            print(f"[inbox] ignored {disp}: {supplier} — {reason} -> ignored/")
            continue
        if status == "review":
            storage.inbox_review(name, label=label)
            reviewed += 1
            print(f"[inbox] review {disp}: {supplier} — {reason} -> review/ as {label!r}")
            continue
        storage.inbox_archive(name)
        if status == "saved":
            saved += 1
            print(f"[inbox] saved {disp}: {supplier} ${total:,.2f} (confidence {conf})")
        else:
            dups += 1
            print(f"[inbox] duplicate {disp}: {supplier} ${total:,.2f} — skipped")

    print(f"[inbox] done: {saved} saved, {dups} duplicate(s), {reviewed} for review, "
          f"{nonsupplier} non-supplier sender(s) -> review (unread), "
          f"{notpdf} non-PDF junk ignored (no read), "
          f"{ignored} exact cop(ies) ignored, {failed} failed")


if __name__ == "__main__":
    main()
