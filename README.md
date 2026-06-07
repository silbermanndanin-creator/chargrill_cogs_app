# Chargrill COGS Tracker

Mobile-friendly Streamlit app: photograph a supplier invoice → Claude Vision extracts
supplier / date / line items / total (ex-GST) → compares spend against live revenue and
flashes a **High Variance Alert** when COGS drifts past the 40% green / 42% red band.
It also tracks **labour**: upload the weekly **Tanda shift CSV** and the app computes
award-compliant gross wages (Fast Food Industry Award 2020) and the combined **prime
cost %** (COGS + labour) against a 68% green / 72% red band.

An **Invoice tracker** (owner) learns each supplier's delivery cadence from history and
flags any week missing an invoice you'd normally have by now, so the COGS picture stays
complete — tick a supplier **All in**, or **Not coming** when they're not delivering.

Targets are pre-loaded from the Jun 2025–Apr 2026 P&L actuals (see `config.py`).

## Files
- `app.py` — Streamlit dashboard + add-invoice / daily-takings / labour tabs
- `extract.py` — Claude Vision extraction (model `claude-sonnet-4-6`); images + PDFs
- `config.py` — supplier targets, alias mapping, alert thresholds (COGS / labour / prime cost)
- `metrics.py` — dashboard aggregations (spend, deliveries, qty-by-unit, $/unit, labour/prime trends)
- `storage.py` — invoices + revenue + weekly labour + payroll setup; CSV locally, Supabase when configured
- `payroll.py` — Fast Food Award 2020 engine: weekly Tanda shift CSV → award gross wages + Excel report
- `lightspeed.py` — Lightspeed K-Series revenue pull (scaffold; manual entry works today)
- `supabase_schema.sql` — table definitions to paste into the Supabase SQL editor

## Run locally
```bash
cd chargrill_cogs_app
python -m venv .venv && .venv\Scripts\activate      # Windows
pip install -r requirements.txt
copy .streamlit\secrets.toml.example .streamlit\secrets.toml   # then add your API key
streamlit run app.py
```
Open the printed URL on your phone (same Wi-Fi) or use Streamlit's network URL.

## Deploy (mobile access anywhere)
Storage auto-switches: **local = CSV** (zero setup); **cloud = Supabase** when
`SUPABASE_URL` + `SUPABASE_KEY` are set. Community Cloud's disk resets on redeploy, so
use Supabase for anything you want to keep.

1. **Supabase** — create a free project at supabase.com → SQL Editor → paste
   `supabase_schema.sql` and run it (creates `invoices` + `revenue` tables).
   In Project Settings → API, copy the **Project URL** and the **service_role** key.
2. **GitHub** — push this folder to a **private** repo. `.gitignore` already excludes
   `secrets.toml`, `data/`, and `.venv/`, so no secrets or local data get committed.
3. **Streamlit Cloud** — at share.streamlit.io, deploy from the repo (main file `app.py`).
   In the app's **Secrets**, add:
   ```toml
   ANTHROPIC_API_KEY = "sk-ant-..."
   SUPABASE_URL = "https://xxxx.supabase.co"
   SUPABASE_KEY = "your-service_role-key"
   ```
4. Open the app URL on your phone — invoices and revenue now persist in Supabase.

To test Supabase locally, put the same three keys in `.streamlit/secrets.toml`.

## Lightspeed (later)
Manual revenue entry works now. To auto-pull: get a K-Series API token + business_id
from Lightspeed Back Office, confirm the reports endpoint in your developer portal, fill
in `lightspeed.py` (`LSK_BASE`, `REVENUE_ENDPOINT`, response mapping), and add the
credentials to `secrets.toml`.
