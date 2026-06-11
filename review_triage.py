"""One-shot triage of the review/ queue (run on demand via GitHub Actions).

Before the PDF-only + deterministic-filename fixes, weeks of email junk piled up
in review/: the same statement uploaded under many different GUID names, plus
stray signature images. This script tidies that up and reports on what's left,
so the owner can fast-triage the app's review queue with full information:

  1. Non-PDFs in review/ are moved to ignored/         (no AI — pure cleanup;
     they were never shown in the app anyway, this just unclutters the folder)
  2. EXACT byte-duplicate PDFs (same sha256) are collapsed — the oldest copy
     stays in review/, the extra copies move to ignored/
  3. Each remaining unique PDF is classified with Claude and printed as one
     line (supplier · document type · confidence), grouped into:
       - invoices from recognised COGS suppliers  -> worth Accepting in the app
       - invoices from unrecognised suppliers     -> owner's call
       - statements / credit notes / orders / other -> usually Dismiss

NOTHING is accepted automatically and nothing is deleted — every move goes to
ignored/, which is never read and never shown, so it's all recoverable.

Requires env: SUPABASE_URL, SUPABASE_KEY, ANTHROPIC_API_KEY.
Run locally with those set:  python review_triage.py
"""
import hashlib

import anthropic

import config
import extract
import storage
from config import canonicalize


def _review_items_all():
    """Every entry in review/ (PDF or not), oldest first: [(name, created_at)]."""
    items = (storage._client().storage.from_(storage.INBOX_BUCKET)
             .list("review", storage._LIST_OPTS)) or []
    out = []
    for it in items:
        if isinstance(it, dict) and it.get("name") and it.get("id") is not None:
            out.append((it["name"], str(it.get("created_at") or "")))
    return out


def main():
    items = _review_items_all()
    if not items:
        print("[triage] review/ is empty — nothing to do")
        return

    print(f"[triage] {len(items)} item(s) in review/")

    # 1) Park non-PDFs (legacy signature images etc.) — never shown in the app.
    pdfs = []
    swept = 0
    for name, created in items:
        if storage._is_pdf(name):
            pdfs.append((name, created))
        else:
            storage._inbox_move(f"review/{name}", "ignored")
            swept += 1
            print(f"[triage] swept  {name}: not a PDF -> ignored/")

    # 2) Collapse exact duplicates: same bytes = same document, keep the oldest.
    seen = {}   # sha256 -> first (oldest) name kept in review/
    unique = []  # (name, raw_bytes)
    deduped = 0
    for name, _created in pdfs:  # _LIST_OPTS sorts oldest first
        try:
            raw = storage.review_download(name)
        except Exception as e:
            print(f"[triage] SKIP   {name}: download failed ({e!r}) — left in review/")
            continue
        digest = hashlib.sha256(raw).hexdigest()
        if digest in seen:
            storage._inbox_move(f"review/{name}", "ignored")
            deduped += 1
            print(f"[triage] dupe   {name}: identical to {seen[digest]} -> ignored/")
        else:
            seen[digest] = name
            unique.append((name, raw))

    # 3) Classify what's left so the owner can triage the app queue quickly.
    client = anthropic.Anthropic()
    groups = {"accept": [], "maybe": [], "dismiss": [], "unknown": []}
    for name, raw in unique:
        try:
            c = extract.classify_document(raw, "application/pdf", client=client)
        except Exception as e:
            groups["unknown"].append((name, f"classification failed: {e!r}"))
            continue
        dt = (c.document_type or "?").strip().lower()
        line = (f"{storage.display_name(name)}  ·  {c.supplier_name}  ·  {dt}"
                f"  ·  confidence {c.confidence}")
        if dt == "invoice" and canonicalize(c.supplier_name) != config.FALLBACK_SUPPLIER:
            groups["accept"].append((name, line))
        elif dt == "invoice":
            groups["maybe"].append((name, line))
        else:
            groups["dismiss"].append((name, line))

    print()
    print("=" * 72)
    print(f"[triage] summary: {swept} non-PDF swept, {deduped} duplicate cop(ies) "
          f"collapsed, {len(unique)} unique PDF(s) left in review/")
    print("=" * 72)
    for key, title in (("accept", "INVOICES from recognised suppliers — Accept in the app"),
                       ("maybe", "INVOICES from unrecognised suppliers — your call"),
                       ("dismiss", "Statements / credit notes / orders / other — usually Dismiss"),
                       ("unknown", "Couldn't classify — open in the app and decide")):
        rows = groups[key]
        print(f"\n--- {title} ({len(rows)}) ---")
        for _name, line in rows:
            print("  " + line)


if __name__ == "__main__":
    main()
