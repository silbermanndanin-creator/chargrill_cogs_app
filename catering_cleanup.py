"""One-off cleanup: purge catering-platform items (Hampr / Eat First / Yordar /
Online Catering) from the supplier-invoice world. Catering orders have their OWN
pipeline (catering_ingest.py -> the Catering tab) and must never count toward COGS.

  1. invoices table: any saved invoice whose supplier name matches a catering
     platform is DELETED (with its stored PDF/photo) — each row is logged first.
  2. invoice_inbox bucket: files at the root and in review/ whose NAME matches a
     catering platform are moved to ignored/ (kept, never read, never shown).

Going forward the ingest's catering gate (inbox_ingest.CATERING_KEYWORDS) keeps
new ones out automatically — this script just clears what's already there.

Requires env: SUPABASE_URL, SUPABASE_KEY.
Run locally with those set:  python catering_cleanup.py
"""
import storage
from inbox_ingest import _is_catering  # same keyword list as the ingest gate


def main():
    # 1) Saved invoices from catering platforms -> delete (logged line by line).
    df = storage.load_invoices()
    purged = 0
    for _, r in df.iterrows():
        if _is_catering(str(r.get("supplier_raw"))):
            print(f"[cleanup] deleting invoice {r['invoice_date']} · {r['supplier_raw']} "
                  f"· ${float(r['total_ex_gst']):,.2f} (saved {r['saved_at']})")
            storage.delete_invoice(str(r["saved_at"]))
            purged += 1
    print(f"[cleanup] {purged} catering invoice row(s) deleted from the app")

    # 2) Bucket: catering-named files at the root and in review/ -> ignored/.
    bucket = storage._client().storage.from_(storage.INBOX_BUCKET)
    moved = 0
    for prefix in ("", "review"):
        items = bucket.list(prefix, storage._LIST_OPTS) or []
        for it in items:
            if not isinstance(it, dict) or not it.get("name") or it.get("id") is None:
                continue
            name = it["name"]
            if _is_catering(name):
                path = f"{prefix}/{name}" if prefix else name
                storage._inbox_move(path, "ignored")
                moved += 1
                print(f"[cleanup] moved {path} -> ignored/")
    print(f"[cleanup] {moved} catering-named bucket file(s) moved to ignored/")


if __name__ == "__main__":
    main()
