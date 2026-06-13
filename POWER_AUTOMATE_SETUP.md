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
| **URI** | the expression in the block below (copy it exactly) |
| **Headers** | `apikey` = `<YOUR SUPABASE SERVICE_ROLE KEY>`<br>`Authorization` = `Bearer <YOUR SUPABASE SERVICE_ROLE KEY>`<br>`Content-Type` = `application/octet-stream`<br>`x-upsert` = `true` |
| **Body** | expression: `base64ToBinary(items('Apply_to_each')?['ContentBytes'])` |

```
https://zelbbsvthqbxelraogac.supabase.co/storage/v1/object/invoice_inbox/@{replace(replace(replace(base64(triggerOutputs()?['body/internetMessageId']), '+', '-'), '/', '-'), '=', '')}_b64_@{replace(replace(replace(base64(concat(triggerOutputs()?['body/from'], '|', items('Apply_to_each')?['Name'])), '+', '-'), '/', '_'), '=', '')}.pdf
```

- The **service_role key** is in Supabase dashboard → **Settings** → **API** →
  `service_role` (the secret one). It's the same key as `SUPABASE_KEY`. **Never** paste
  it into a file that gets committed to GitHub — only into the Power Automate header box.
- The URI has two base64 parts split by `_b64_`. The part **after** it packs
  `<sender email>|<attachment name>` (Supabase rejects keys with special characters); the
  app decodes it and shows **sender — attachment.pdf** everywhere (review queue, downloads,
  logs) — e.g. `bidfood — Invoice 12345.pdf`. The part **before** it is the email's
  `internetMessageId`, base64'd and made underscore-free.
- **Why the message id, not `ticks(utcNow())`?** The key is now *deterministic*: if the
  flow fires more than once for the same email (retries, re-delivery), every upload lands
  on the **same** object name, so `x-upsert: true` overwrites it instead of dropping
  another copy. With the old `ticks(utcNow())` prefix each fire made a new file, so the
  same invoice piled up dozens of times in the bucket. (A genuinely re-sent invoice in a
  *new* email is still caught later by the content dedupe, so it's never double-counted.)

**Already have the flow running?** Only the **URI** changed — open the flow, edit the
HTTP action, and replace the URI with the value above (everything else stays the same).
The `x-upsert` header must be `true` (it already is in the table above). Files uploaded
before the change keep working; new uploads simply stop duplicating.

**Save**, then send a test email with a PDF to that inbox. Within ~15 min (or after you
hit **Run workflow** on the GitHub Action) it appears in the app.

---

## Drive Catering folder → app (keeps receivables current)

Every invoice you file in the Google Drive **Catering** folder gets mirrored into the
app: platform invoices (Hampr / Yordar / Eat First — read from the bill-to INSIDE the
PDF, so a typo'd filename still counts) are recorded so the 💰 payments & outstanding
table can flag delivered orders with **no invoice raised yet**; direct-customer
invoices (OLSH, UNSW, Swans…) are ignored automatically.

The mirror is a small **Google Apps Script** running inside the Google account that
owns the folder (Power Automate's Google Drive trigger isn't available on all
tenants, and this needs no premium connector at all):

1. Go to **script.google.com** (signed in as the Drive account) → **New project**.
2. Replace the editor contents with the script below.
3. **Project Settings (⚙) → Script properties → Add script property**:
   name `SUPABASE_KEY`, value = the Supabase service_role key (kept out of the code).
4. Back in the editor: **Run** once → grant the Drive + external-request permissions
   it asks for → check the log says `uploaded N new file(s)`.
5. **Triggers (⏰) → Add trigger**: function `syncCateringFolder`, event source
   *Time-driven*, *Hour timer*, *Every hour* → **Save**.

```javascript
// Mirrors new PDFs from the Drive "Catering" folder into the Supabase bucket
// folder invoices/drive_invoices/, where the app's "Drive invoice ingest"
// GitHub Action picks them up. Uploaded file ids are remembered in Script
// Properties so each file is sent once; x-upsert makes any repeat harmless.
const FOLDER_ID = '1k9hW0r-9XqhIKykG5vfIe5_5oGD0Xyjd';   // the Catering folder
const SUPABASE_URL = 'https://zelbbsvthqbxelraogac.supabase.co';

function syncCateringFolder() {
  const props = PropertiesService.getScriptProperties();
  const key = props.getProperty('SUPABASE_KEY');
  if (!key) throw new Error('Add SUPABASE_KEY under Project Settings -> Script properties');
  const doneSet = new Set(JSON.parse(props.getProperty('UPLOADED_IDS') || '[]'));
  const files = DriveApp.getFolderById(FOLDER_ID).getFiles();  // top level only
  let uploaded = 0;
  while (files.hasNext()) {
    const f = files.next();
    if (doneSet.has(f.getId())) continue;
    if (f.getMimeType() !== 'application/pdf') { doneSet.add(f.getId()); continue; }
    const resp = UrlFetchApp.fetch(
      SUPABASE_URL + '/storage/v1/object/invoices/drive_invoices/' +
        encodeURIComponent(f.getName()),
      { method: 'post',
        contentType: 'application/octet-stream',
        headers: { apikey: key, Authorization: 'Bearer ' + key, 'x-upsert': 'true' },
        payload: f.getBlob().getBytes(),
        muteHttpExceptions: true });
    if (resp.getResponseCode() < 300) { doneSet.add(f.getId()); uploaded++; }
    else console.error(f.getName() + ': HTTP ' + resp.getResponseCode() + ' ' +
                       resp.getContentText());
  }
  props.setProperty('UPLOADED_IDS', JSON.stringify([...doneSet]));
  console.log('uploaded ' + uploaded + ' new file(s)');
}
```

Notes:
- The FIRST run uploads every PDF already at the top level of the folder (subfolders
  like paid/ are not touched). The ingest dedupes against the backfill, so nothing
  double-counts — but move known duplicate PDFs (e.g. an invoice you've voided) into
  the paid/ subfolder first so they aren't recorded.
- The "Drive invoice ingest" GitHub Action (every ~6 h, or run on demand) reads each
  new PDF, records platform invoices, and parks everything else in
  `drive_invoices/ignored/`.

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
