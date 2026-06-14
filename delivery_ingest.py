"""Delivery payout ingest — runs headless (GitHub Actions cron, inside ingest.yml).

Power Automate drops each weekly delivery PAYMENT summary into the Supabase Storage bucket
under
  delivery/<platform>/<file>
where <platform> is one of: ubereats, doordash
and <file> is the email body saved as .html (Uber) or .txt (DoorDash). This script reads
any new files, runs Claude extraction, writes a row to the `delivery_payouts` table, and
moves the file to delivery/done/<platform>/ so it isn't processed twice. Re-running is safe
(the row is upserted on source_file).

The app uses each payout's ACTUAL net for the matching ISO week instead of the flat
40%-commission estimate, so revenue and COGS % become true rather than assumed.

A file that yields no usable payout (wrong attachment, blank/garbled) is moved to
delivery/review/<platform>/ instead — captured for a human, but not re-read (and re-charged
to Claude) every run. A file that hits a transient error (network / rate limit) is LEFT in
place so the next run retries it — a blip never loses a real payout.

Environment / GitHub repo secrets:
  SUPABASE_URL, SUPABASE_KEY      (same as the app + digest)
  ANTHROPIC_API_KEY              (for Claude extraction)
  SUPABASE_BUCKET                (optional; the Storage bucket name, default "invoices")

Run locally:  python delivery_ingest.py
"""
import os

from supabase import create_client

import storage
from delivery_extract import extract_delivery_payout

# `or` (not a default arg): the GitHub workflow passes SUPABASE_BUCKET even when the secret
# is unset, which arrives as an empty string — that must still fall back to "invoices".
BUCKET = os.environ.get("SUPABASE_BUCKET") or "invoices"
PLATFORMS = {
    "ubereats": "Uber Eats",
    "doordash": "DoorDash",
}
TEXT_EXTS = (".html", ".htm", ".txt")
IMG_MEDIA = {".pdf": "application/pdf", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
             ".png": "image/png", ".webp": "image/webp"}


def _client():
    url, key = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_KEY")
    if not (url and key):
        raise SystemExit("[delivery] SUPABASE_URL / SUPABASE_KEY not set — nothing to do.")
    return create_client(url, key)


def _ext(name: str) -> str:
    return os.path.splitext(name.lower())[1]


def _archive(sb, src: str, dest: str):
    """Move a handled file out of the inbox folder (into done/ or review/)."""
    sb.storage.from_(BUCKET).move(src, dest)


def process_one(sb, folder: str, name: str):
    """Extract + save one file, then archive it. Returns (status, detail) where status
    is 'saved' | 'skip' | 'review'."""
    prefix = f"delivery/{folder}"
    path = f"{prefix}/{name}"
    hint = PLATFORMS[folder]

    if storage.delivery_payout_already_ingested(path):
        _archive(sb, path, f"delivery/done/{folder}/{name}")
        return ("skip", f"already ingested {path}")

    raw = sb.storage.from_(BUCKET).download(path)
    ext = _ext(name)
    if ext in TEXT_EXTS:
        doc = extract_delivery_payout(text=raw.decode("utf-8", "ignore"), platform_hint=hint)
    else:
        doc = extract_delivery_payout(file_bytes=raw,
                                      media_type=IMG_MEDIA.get(ext, "application/pdf"),
                                      platform_hint=hint)

    # Nothing usable came back (wrong attachment, blank or garbled) — set it aside in
    # review/ rather than re-reading (and re-paying for Claude) every run. A genuine payout
    # always has at least a net amount and a pay-week start date.
    if not doc.net_payout and not doc.period_start:
        _archive(sb, path, f"delivery/review/{folder}/{name}")
        return ("review", f"no payout found -> review/ [{path}]")

    storage.save_delivery_payout(doc, source_file=path)
    _archive(sb, path, f"delivery/done/{folder}/{name}")
    return ("saved", f"{hint} · {doc.period_start}–{doc.period_end} · "
                     f"net ${doc.net_payout:,.2f} [{path}]")


def main():
    sb = _client()
    saved = reviewed = failed = 0
    for folder in PLATFORMS:
        prefix = f"delivery/{folder}"
        try:
            entries = sb.storage.from_(BUCKET).list(prefix) or []
        except Exception as e:
            print(f"[delivery] list {prefix} failed (folder may not exist yet): {e}")
            continue
        for f in entries:
            name = f.get("name")
            # Skip Supabase's folder placeholder and any nested "folder" entries (id is None).
            if not name or name == ".emptyFolderPlaceholder" or f.get("id") is None:
                continue
            try:
                status, detail = process_one(sb, folder, name)
            except Exception as e:
                # Transient glitch (network / rate limit): leave the file in place so the
                # next run retries it — a blip never loses a real payout.
                failed += 1
                print(f"[delivery] FAILED {prefix}/{name}: {e!r} — left for retry")
                continue
            if status == "review":
                reviewed += 1
            elif status == "saved":
                saved += 1
            print(f"[delivery] {status}: {detail}")
    print(f"[delivery] done — {saved} saved, {reviewed} for review, {failed} failed.")


if __name__ == "__main__":
    main()
