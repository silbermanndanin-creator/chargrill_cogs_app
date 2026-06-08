# Auto-capture emailed invoices (Power Automate → app)

Goal: every supplier invoice that arrives by email is read and saved into the app
automatically — nothing to photograph, nothing to type.

```
Supplier emails a PDF ──► Power Automate (cloud flow, runs 24/7)
                              │  saves the attachment into Supabase Storage
                              ▼
                     bucket: invoice_inbox
                              │
                              ▼
        GitHub Action "Invoice inbox" (every ~15 min)  ── inbox_ingest.py
            reads each file with Claude Vision, saves the invoice + photo
                              │
                              ▼
                 Invoice shows up in the app on your phone
```

Paper invoices handed over at delivery: email a phone photo to the **same** inbox
address and it flows through the identical pipeline.

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

**Inside the loop, add:** *HTTP* (the premium one). Fill it in:

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

## Recommended once it's working: ignore email-signature logos
Email signatures sometimes ride along as tiny image "attachments" and would be read as
junk invoices. Add a **Condition** inside the *Apply to each*, before the HTTP action:

- `length(items('Apply_to_each')?['ContentBytes'])` **is greater than** `40000`

(Real invoices are well above this; logos are below it.) Put the HTTP action in the
**If yes** branch. Junk that still slips through can be deleted in the app's **Invoices**
tab.

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
