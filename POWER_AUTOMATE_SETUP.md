# Auto-capture emailed invoices (Power Automate → app)

Goal: every supplier invoice that arrives by email is read and saved into the app
automatically — nothing to photograph, nothing to type.

```
Supplier emails a PDF ──► Power Automate (cloud flow, runs 24/7)
                              │  saves ONLY .pdf attachments into Supabase Storage
                              ▼
                     bucket: invoice_inbox
                              │
                              ▼
        GitHub Action "Invoice inbox" (every ~15 min)  ── inbox_ingest.py
            reads each PDF with Claude Vision, then routes it:
              · a clean supplier invoice  → saved + PDF archived to processed/
              · anything else (statement, credit note,
                unrecognised supplier)    → review/   (shown in the app)
              · a non-PDF that slips in   → ignored/  (never read, never shown)
                              │
                              ▼
                 Invoice shows up in the app on your phone
```

**Only PDFs are processed** — signature logos, inline images and other email junk never
reach the app. The **review/** queue is visible in the app under
**📋 Invoices → 📥 Emailed invoices needing review**: tap **Accept** to read and save one
as a normal invoice (the PDF then moves to processed/), or **Dismiss** to set it aside.

Paper invoices handed over at delivery: photograph them in the app's **📸 Add invoice**
tab — emailed photos are no longer ingested (PDF attachments only).

---

## One-time setup (do these once)

### 1. Create the storage "drop box" in Supabase
Supabase dashboard → **Storage** → **New bucket** → name it exactly **`invoice_inbox`**,
leave **Public** OFF → Create.
*(Or run the updated `supabase_schema.sql` — it now creates this bucket.)*

### 2. Add the Anthropic key as a GitHub repo secret
GitHub repo → **Settings** → **Secrets and variables** → **Actions** → **New repository
secret**:
- Name: `ANTHROPIC_API_KEY`  → Value: your `sk-ant-…` key (the same one in Streamlit
  Cloud secrets).

`SUPABASE_URL` and `SUPABASE_KEY` are already there (the digest uses them). The
"Invoice inbox" workflow is then live and will run every ~15 minutes. You can also run
it on demand: **Actions** tab → **Invoice inbox** → **Run workflow**.

### 3. Build the Power Automate cloud flow
At **make.powerautomate.com** → **Create** → **Automated cloud flow**.

**Trigger:** *When a new email arrives (V3)*
- Folder: the inbox that receives invoices (e.g. a dedicated `invoices@` mailbox)
- **Only with Attachments:** Yes
- **Include Attachments:** Yes

**Action:** *Apply to each* → choose **Attachments** as the input.

**Inside the loop, add a *Condition* first** so only PDF invoices are uploaded
(signature logos and inline images ride along as attachments — skip them here):

- Left box (expression): `endsWith(toLower(items('Apply_to_each')?['Name']), '.pdf')`
- Operator: **is equal to** → Right box: `true`

**In the *If yes* branch, add:** *HTTP* (the premium one). Fill it in:

| Field | Value |
|---|---|
| **Method** | `POST` |
| **URI** | `https://zelbbsvthqbxelraogac.supabase.co/storage/v1/object/invoice_inbox/@{guid()}_@{encodeUriComponent(items('Apply_to_each')?['Name'])}` |
| **Headers** | `apikey` = `<YOUR SUPABASE SERVICE_ROLE KEY>`<br>`Authorization` = `Bearer <YOUR SUPABASE SERVICE_ROLE KEY>`<br>`Content-Type` = `application/octet-stream`<br>`x-upsert` = `true` |
| **Body** | expression: `base64ToBinary(items('Apply_to_each')?['ContentBytes'])` |

- The **service_role key** is in Supabase dashboard → **Settings** → **API** →
  `service_role` (the secret one). It's the same key as `SUPABASE_KEY`. **Never** paste
  it into a file that gets committed to GitHub — only into the Power Automate header box.
- `@{guid()}_…` just gives every file a unique name so two invoices never clash. The
  original filename (and its `.pdf`/`.jpg` extension) is kept on the end, which the
  reader needs.

**Save**, then send a test email with a PDF to that inbox. Within ~15 min (or after you
hit **Run workflow** on the GitHub Action) it appears in the app.

---

## What lands where in the bucket
- **(root)** — new PDFs waiting for the next ingest run.
- **processed/** — PDFs saved as invoices (including review files you accepted).
- **review/** — PDFs the ingest didn't auto-save: statements, credit notes, orders, or an
  invoice from a supplier the app doesn't recognise. The ingest renames each file with
  what it found (e.g. `Statement · Bidfood — <original name>.pdf`), so the queue is
  identifiable at a glance without downloading anything. Handle these in the app:
  **📋 Invoices → 📥 Emailed invoices needing review** → view the PDF, then **Accept**
  (reads + saves it as an invoice and moves the PDF to processed/) or **Dismiss**
  (moves it to ignored/ without counting it). To clear several at once, tick them in
  the **Delete several at once** list and press **🗑 Delete selected**.
- **ignored/** — non-PDF attachments that slipped past the flow's PDF condition, plus
  anything you dismissed. Never read, never shown; kept in case it's ever needed.

---

## How the accuracy is protected
- Each file is read by the **same** Claude Vision pipeline as the manual upload: a fast
  first read, automatically re-read on the strongest model if the numbers don't
  reconcile, plus a per-line `price × qty = amount` correction pass.
- A **re-sent** invoice is detected (same supplier + date + total) and skipped, so
  nothing is double-counted.
- A file that hits a transient error is **left in the inbox** and retried next run — a
  glitch never silently drops an invoice.
- The existing daily digest + price-rise alerts still surface anything that looks off,
  so "review only the exceptions" rather than checking every invoice.

## If the HTTP action is blocked (premium not licensed)
If the test run errors with a licensing/connector message, the premium HTTP connector
isn't enabled on this tenant. Fallback: a **Power Automate *Desktop*** flow on the venue
PC can do the same capture (Email → save attachments → run the local extractor → push to
Supabase). Ask and we'll wire that instead.
