"""Catering ingest — runs headless (GitHub Actions cron).

Power Automate drops each catering order into the Supabase Storage bucket under
  catering/<platform>/<file>
where <platform> is one of: hampr, eatfirst, yordar, online-catering
and <file> is a .pdf (Eat First / Yordar / Online Catering) or .html/.txt (Hampr body,
Online Catering text). This script reads any new files, runs Claude extraction, writes a
row to the `catering_orders` table, and moves the file to catering/done/<platform>/ so it
isn't processed twice. Re-running is safe (the row is upserted on source_file).

A file that yields no usable order (wrong attachment, blank/garbled) is moved to
catering/review/<platform>/ instead — captured for a human to look at, but not re-read
(and re-charged to Claude) on every run. A file that hits a transient error (network /
rate limit) is LEFT in place so the next run retries it — a blip never loses a real order.

Environment / GitHub repo secrets:
  SUPABASE_URL, SUPABASE_KEY      (same as the app + digest)
  ANTHROPIC_API_KEY              (for Claude extraction — add this secret if not present)
  SUPABASE_BUCKET                (optional; the Storage bucket name, default "invoices")

Run locally:  python catering_ingest.py
"""
import os

from supabase import create_client

import storage
from catering_extract import extract_catering

# `or` (not a default arg): the GitHub workflow passes SUPABASE_BUCKET even when the
# secret is unset, which arrives as an empty string — that must still fall back to
# "invoices", or Supabase rejects "" as an invalid bucket name.
BUCKET = os.environ.get("SUPABASE_BUCKET") or "invoices"
PLATFORMS = {
    "hampr": "Hampr",
    "eatfirst": "Eat First",
    "yordar": "Yordar",
    "online-catering": "Online Catering",
}
TEXT_EXTS = (".html", ".htm", ".txt")
IMG_MEDIA = {".pdf": "application/pdf", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
             ".png": "image/png", ".webp": "image/webp"}


def _client():
    url, key = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_KEY")
    if not (url and key):
        raise SystemExit("[catering] SUPABASE_URL / SUPABASE_KEY not set — nothing to do.")
    return create_client(url, key)


def _ext(name: str) -> str:
    return os.path.splitext(name.lower())[1]


def _archive(sb, src: str, dest: str):
    """Move a handled file out of the inbox folder (into done/ or review/)."""
    sb.storage.from_(BUCKET).move(src, dest)


def process_one(sb, folder: str, name: str):
    """Extract + save one file, then archive it. Returns (status, detail) where status
    is 'saved' | 'skip' | 'review'."""
    prefix = f"catering/{folder}"
    path = f"{prefix}/{name}"
    hint = PLATFORMS[folder]

    if storage.catering_already_ingested(path):
        _archive(sb, path, f"catering/done/{folder}/{name}")
        return ("skip", f"already ingested {path}")

    raw = sb.storage.from_(BUCKET).download(path)
    ext = _ext(name)
    if ext in TEXT_EXTS:
        order = extract_catering(text=raw.decode("utf-8", "ignore"), platform_hint=hint)
    else:
        order = extract_catering(file_bytes=raw,
                                 media_type=IMG_MEDIA.get(ext, "application/pdf"),
                                 platform_hint=hint)

    # Nothing usable came back (a wrong attachment, blank or garbled file) — set it aside
    # in review/ instead of re-reading it (and re-paying for Claude) every run. A genuine
    # order always has at least a delivery date or some line items.
    if not order.line_items and not order.deliver_date:
        _archive(sb, path, f"catering/review/{folder}/{name}")
        return ("review", f"no order found -> review/ [{path}]")

    storage.save_catering_order(order, source_file=path)
    _archive(sb, path, f"catering/done/{folder}/{name}")
    n = len(order.line_items)
    return ("saved", f"{hint} · {order.deliver_date} {order.deliver_time or ''} · {n} item(s) [{path}]")


def main():
    sb = _client()
    saved = reviewed = failed = 0
    for folder in PLATFORMS:
        prefix = f"catering/{folder}"
        try:
            entries = sb.storage.from_(BUCKET).list(prefix) or []
        except Exception as e:
            print(f"[catering] list {prefix} failed (folder may not exist yet): {e}")
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
                # next run retries it — a blip never loses a real order.
                failed += 1
                print(f"[catering] FAILED {prefix}/{name}: {e!r} — left for retry")
                continue
            if status == "review":
                reviewed += 1
            elif status == "saved":
                saved += 1
            print(f"[catering] {status}: {detail}")
    print(f"[catering] done — {saved} saved, {reviewed} for review, {failed} failed.")


if __name__ == "__main__":
    main()
