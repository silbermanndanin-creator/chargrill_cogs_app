"""Bulk-dismiss review-queue files (run via the "Review bulk dismiss" Action).

Takes a newline-separated list of file names (as they appear in review/) from the
DISMISS_NAMES env var and moves each to ignored/ — exactly what tapping Dismiss in
the app does, just in bulk. Nothing is deleted; ignored/ keeps everything.

Requires env: SUPABASE_URL, SUPABASE_KEY, DISMISS_NAMES.
"""
import os

import storage


def main():
    names = [n.strip() for n in os.environ.get("DISMISS_NAMES", "").splitlines() if n.strip()]
    if not names:
        print("[dismiss] no names provided — nothing to do")
        return
    in_review = {n for n, _ in storage.review_list()}
    moved = skipped = 0
    for name in names:
        if name not in in_review:
            skipped += 1
            print(f"[dismiss] SKIP {storage.display_name(name)} — not in review/ (already handled?)")
            continue
        storage.review_dismiss(name)
        moved += 1
        print(f"[dismiss] dismissed {storage.display_name(name)} -> ignored/")
    left = len(storage.review_list())
    print(f"[dismiss] done: {moved} dismissed, {skipped} skipped, {left} file(s) left in review/")


if __name__ == "__main__":
    main()
