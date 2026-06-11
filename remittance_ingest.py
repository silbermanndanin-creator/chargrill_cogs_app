"""Remittance ingest — runs headless (GitHub Actions cron).

Power Automate drops each platform payment document into the Supabase Storage bucket under
  remittance/<platform>/<file>
where <platform> is one of: hampr, eatfirst, yordar
and <file> is a .pdf (Hampr remittance advice, Yordar RGI, Eat First RCTI) or .html/.txt
(an emailed body). This script reads any new files, runs Claude extraction, writes a row
to the `platform_remittances` table, and moves the file to remittance/done/<platform>/ so
it isn't processed twice. Re-running is safe (the row is upserted on source_file).

The app matches each document's order numbers back to catering_orders to show what each
platform still owes.

A file that yields no usable document (wrong attachment, blank/garbled) is moved to
remittance/review/<platform>/ instead — captured for a human to look at, but not re-read
(and re-charged to Claude) on every run. A file that hits a transient error (network /
rate limit) is LEFT in place so the next run retries it.

Environment / GitHub repo secrets:
  SUPABASE_URL, SUPABASE_KEY      (same as the app + digest)
  ANTHROPIC_API_KEY              (for Claude extraction)
  SUPABASE_BUCKET                (optional; the Storage bucket name, default "invoices")

Run locally:  python remittance_ingest.py
"""
import os

from supabase import create_client

import storage
from remittance_extract import extract_remittance

# `or` (not a default arg): the GitHub workflow passes SUPABASE_BUCKET even when the
# secret is unset, which arrives as an empty string — that must still fall back to
# "invoices", or Supabase rejects "" as an invalid bucket name.
BUCKET = os.environ.get("SUPABASE_BUCKET") or "invoices"
PLATFORMS = {
    "hampr": "Hampr",
    "eatfirst": "Eat First",
    "yordar": "Yordar",
}
TEXT_EXTS = (".html", ".htm", ".txt")
IMG_MEDIA = {".pdf": "application/pdf", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
             ".png": "image/png", ".webp": "image/webp"}


def _client():
    url, key = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_KEY")
    if not (url and key):
        raise SystemExit("[remittance] SUPABASE_URL / SUPABASE_KEY not set — nothing to do.")
    return create_client(url, key)


def _ext(name: str) -> str:
    return os.path.splitext(name.lower())[1]


def _archive(sb, src: str, dest: str):
    """Move a handled file out of the inbox folder (into done/ or review/)."""
    sb.storage.from_(BUCKET).move(src, dest)


def process_one(sb, folder: str, name: str):
    """Extract + save one file, then archive it. Returns (status, detail) where status
    is 'saved' | 'skip' | 'review'."""
    prefix = f"remittance/{folder}"
    path = f"{prefix}/{name}"
    hint = PLATFORMS[folder]

    if storage.remittance_already_ingested(path):
        _archive(sb, path, f"remittance/done/{folder}/{name}")
        return ("skip", f"already ingested {path}")

    raw = sb.storage.from_(BUCKET).download(path)
    ext = _ext(name)
    if ext in TEXT_EXTS:
        doc = extract_remittance(text=raw.decode("utf-8", "ignore"), platform_hint=hint)
    else:
        doc = extract_remittance(file_bytes=raw,
                                 media_type=IMG_MEDIA.get(ext, "application/pdf"),
                                 platform_hint=hint)

    # Nothing usable came back (a wrong attachment, blank or garbled file) — set it aside
    # in review/ instead of re-reading it (and re-paying for Claude) every run. A genuine
    # payment document always lists at least one order.
    if not doc.lines:
        _archive(sb, path, f"remittance/review/{folder}/{name}")
        return ("review", f"no payment lines found -> review/ [{path}]")

    storage.save_platform_remittance(doc, source_file=path)
    _archive(sb, path, f"remittance/done/{folder}/{name}")
    return ("saved", f"{hint} · {doc.doc_date} · ${doc.total_paid:,.2f} "
                     f"covering {len(doc.lines)} order(s) [{path}]")


def main():
    sb = _client()
    saved = reviewed = failed = 0
    for folder in PLATFORMS:
        prefix = f"remittance/{folder}"
        try:
            entries = sb.storage.from_(BUCKET).list(prefix) or []
        except Exception as e:
            print(f"[remittance] list {prefix} failed (folder may not exist yet): {e}")
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
                # next run retries it — a blip never loses a real remittance.
                failed += 1
                print(f"[remittance] FAILED {prefix}/{name}: {e!r} — left for retry")
                continue
            if status == "review":
                reviewed += 1
            elif status == "saved":
                saved += 1
            print(f"[remittance] {status}: {detail}")
    print(f"[remittance] done — {saved} saved, {reviewed} for review, {failed} failed.")


if __name__ == "__main__":
    main()
