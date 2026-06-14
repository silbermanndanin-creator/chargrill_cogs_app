-- Run this once in the Supabase SQL editor (your project -> SQL Editor -> New query).
-- Creates the two tables the app reads/writes. Column names/types match storage.py.

create table if not exists invoices (
    id            bigint generated always as identity primary key,
    saved_at      text,
    supplier_raw  text,
    supplier      text,
    invoice_date  text,
    total_ex_gst  numeric,
    iso_week      text,
    month         text,
    line_items    text,         -- JSON string of [{description, quantity, unit, amount}, ...]
    source_file   text          -- inbox bucket key; deduped by the unique index below
);

-- Add the dedupe key on databases created before this column existed (the
-- create-table above is a no-op once the table exists). The inbox cron stamps each
-- saved invoice with the bucket file it came from and upserts on this key, so a file
-- whose move to processed/ failed and gets re-read on the next run overwrites its own
-- row instead of inserting a duplicate. NULL for manual / in-app uploads that have no
-- bucket file — Postgres allows many NULLs under a UNIQUE index, and the app's content
-- check (find_duplicate) still guards those.
alter table invoices add column if not exists source_file text;
create unique index if not exists invoices_source_file_key on invoices (source_file);

create table if not exists revenue (
    period_type  text,
    period_key   text,
    revenue      numeric,
    updated_at   text,
    primary key (period_type, period_key)   -- enables upsert on (period_type, period_key)
);

create table if not exists labour (
    period_type  text,
    period_key   text,
    labour_cost  numeric,                       -- gross wages for the period (week grain)
    hours        numeric,                        -- total hours
    foh_hours    numeric,                        -- front-of-house hours
    boh_hours    numeric,                        -- back-of-house (kitchen) hours
    updated_at   text,
    primary key (period_type, period_key)        -- enables upsert on (period_type, period_key)
);
-- If the labour table already exists from before, add the new columns:
--   alter table labour add column if not exists foh_hours numeric;
--   alter table labour add column if not exists boh_hours numeric;

-- Holds the latest Payroll Setup.xlsx (staff, award rates, public holidays) as base64,
-- so the cloud app can run the weekly Tanda-CSV labour calc. PRIVATE data — never commit
-- this to git; it lives only in Supabase. Single row (id = 1), replaced on re-upload.
create table if not exists payroll_setup (
    id           integer primary key,            -- always 1; upsert replaces the latest
    filename     text,
    file_b64     text,                            -- base64 of the .xlsx
    uploaded_at  text
);

create table if not exists pos_days (
    date              text primary key,      -- one finalised end-of-day slip per date; enables upsert on date
    iso_week          text,
    month             text,
    total_incl_gst    numeric,
    doordash          numeric,
    ubereats          numeric,
    bite              numeric,               -- Bite Business / app payments (incl GST)
    cash              numeric,               -- Cash takings (incl GST), feeds reconciliation POS col
    adjusted_incl_gst numeric,               -- after netting the delivery commission
    adjusted_ex_gst   numeric,               -- ex-GST revenue used for COGS %
    saved_at          text
);
-- If pos_days already exists from before, add the bite + cash columns:
--   alter table pos_days add column if not exists bite numeric;
--   alter table pos_days add column if not exists cash numeric;

-- Original invoice photos/PDFs (audit + GST trail), kept separate from the invoices
-- table so the dashboard's load stays light. One row per invoice (keyed by saved_at).
create table if not exists invoice_images (
    saved_at    text primary key,
    media_type  text,
    image_b64   text
);

-- Part-time contracts: each employee's fixed working days/times (kept in the DB, not
-- in git, since it's personal data). One row per employee+weekday.
create table if not exists contracts (
    employee  text,
    weekday   text,
    start     text,
    finish    text,
    primary key (employee, weekday)
);

-- Part-time variation events: each shift where a tracked part-timer's actual start
-- time (or day) differed from their contract. Combined across weeks into variation
-- letters. One row per employee+shift_date.
create table if not exists variation_events (
    employee          text,
    shift_date        text,
    weekday           text,
    actual_start      text,
    actual_finish     text,
    contracted_start  text,
    kind              text,
    week_ending       text,
    created_at        text,
    primary key (employee, shift_date)
);

-- Stock items: the products counted in the weekly stocktake, grouped by supplier
-- (Baida/Chicken, Veggies, Blueseas only), with the price per their unit
-- (e.g. salmon unit 'kg', unit_price 37.25 -> $37.25/kg).
create table if not exists stock_items (
    item        text primary key,
    supplier    text,
    unit        text,
    unit_price  numeric
);
-- If stock_items already existed without the supplier column:
--   alter table stock_items add column if not exists supplier text;

-- Weekly stocktake: end-of-week stock-on-hand $ value (valued at last-paid prices),
-- used to compute TRUE COGS = opening + purchases - closing. One row per ISO week.
create table if not exists stocktake (
    period_key   text primary key,      -- 'YYYY-Www'; enables upsert on the week
    stock_value  numeric,               -- $ value of stock on hand at week's end
    updated_at   text
);

-- Packaging order pad: on-hand counts from the latest packaging stocktake. The whole
-- map is replaced on each save (one row per item). Order qty is derived in the app.
create table if not exists packaging_counts (
    item        text primary key,
    on_hand     numeric,
    updated_at  text
);

-- Drinks order pad: on-hand counts from the latest drinks stocktake (same shape as
-- packaging_counts; single supplier, no colour split).
create table if not exists drinks_counts (
    item        text primary key,
    on_hand     numeric,
    updated_at  text
);

-- Food Safety daily temperature records — one record per day, stored as a JSON blob
-- (managers, all section temps, chicken cooks). Enables upsert on date.
create table if not exists food_safety (
    date      text primary key,
    data      text,                            -- JSON string of the day's full record
    saved_at  text
);

-- Invoice tracker ticks: the owner's weekly confirmation that a supplier's invoices
-- are all uploaded ('confirmed'), or that the supplier isn't delivering this week
-- ('skipped'), so the completeness check doesn't flag it. One row per ISO week+supplier.
create table if not exists invoice_checks (
    period_key  text,                            -- 'YYYY-Www'
    supplier    text,                            -- canonical category (config.SUPPLIERS)
    state       text,                            -- 'confirmed' | 'skipped'
    note        text,
    updated_at  text,
    primary key (period_key, supplier)           -- enables upsert on (period_key, supplier)
);

-- Employee classification overrides: change an employee's Full-Time / Part-Time / Casual
-- (and optionally section / flat rate) from the app, without re-uploading Payroll Setup.xlsx.
-- One row per employee (keyed by display name); applied on top of the setup sheet.
create table if not exists employee_overrides (
    employee         text primary key,
    employment_type  text,                       -- 'Full-Time' | 'Part-Time' | 'Casual'
    section          text,                        -- 'FOH' | 'BOH' | ''
    flat_rate        numeric,                     -- optional flat hourly rate override
    updated_at       text
);

-- Latest weekly Tanda shift CSV (base64), so the Variations tab can reuse the CSV
-- uploaded in the Labour tab without re-uploading — survives redeploys. Single row (id=1).
create table if not exists shift_csv (
    id           integer primary key,            -- always 1; upsert replaces the latest
    filename     text,
    csv_b64      text,
    week_ending  text,
    uploaded_at  text
);

-- Storage bucket for emailed invoices: Power Automate drops each email attachment
-- here, and inbox_ingest.py (GitHub Actions cron) reads + saves them. Private — the
-- service_role key has full access; nothing public is needed. (You can also create
-- this in Dashboard -> Storage -> New bucket, name "invoice_inbox", Public off.)
insert into storage.buckets (id, name, public)
values ('invoice_inbox', 'invoice_inbox', false)
on conflict (id) do nothing;

-- Generated variation letters kept in the app (download anytime). One row per filename
-- (re-saving the same letter updates it). file_b64 = base64 of the .docx.
create table if not exists letters (
    filename    text primary key,
    employee    text,
    label       text,
    file_b64    text,
    saved_at    text
);

-- Per-employee letter details (Employment Agreement date + address) for variation letters.
-- Personal data — kept in the DB, never in git. One row per employee (display name).
create table if not exists emp_details (
    employee        text primary key,
    agreement_date  text,
    address1        text,
    address2        text,
    updated_at      text
);

-- Catering orders from every platform (Hampr / Eat First / Yordar / Online Catering),
-- ingested from the Supabase Storage bucket by catering_ingest.py. line_items is a JSON
-- string of [{item, person, quantity, unit_price}, ...]; `person` carries the per-person
-- name on individually-named orders (e.g. Hampr bowls) for bowl labelling. source_file is
-- UNIQUE so the ingest Action can re-run without creating duplicates.
create table if not exists catering_orders (
    id            bigint generated always as identity primary key,
    saved_at      text,
    platform      text,
    order_type    text,                 -- 'delivery' | 'pickup'
    company       text,                 -- the business the order is FOR (DHL, Anduril); '' if personal
    deliver_date  text,                 -- YYYY-MM-DD (what the app/digest filter on)
    deliver_time  text,                 -- 'HH:MM' 24h
    headcount     integer,              -- people the order feeds ("GROUP SIZE"/"Number of People")
    contact_name  text,
    address       text,
    phone         text,
    order_ref     text,
    line_items    text,                 -- JSON [{item, quantity, person, unit_price, note}, ...]
    items_total   numeric,
    confidence    text,
    source_file   text unique           -- bucket path; enables upsert / dedupe
);
-- If you created catering_orders before these columns existed, add them:
--   alter table catering_orders add column if not exists order_type text;
--   alter table catering_orders add column if not exists headcount integer;
--   alter table catering_orders add column if not exists company text;

-- Platform payment documents (Hampr remittance advice / Yordar RGI / Eat First RCTI),
-- ingested from the Supabase Storage bucket by remittance_ingest.py. `lines` is a JSON
-- string of [{order_ref, order_date, company, amount, commission}, ...] — one entry per
-- order the document pays for. The app matches order_ref back to catering_orders to show
-- outstanding $ per platform. source_file is UNIQUE so the ingest Action can re-run
-- without creating duplicates.
create table if not exists platform_remittances (
    id            bigint generated always as identity primary key,
    saved_at      text,
    platform      text,                 -- 'Hampr' | 'Eat First' | 'Yordar'
    doc_ref       text,                 -- RGI-260608006 / AU60031-308187; '' for Hampr (no number)
    doc_date      text,                 -- YYYY-MM-DD payment / invoice date
    total_paid    numeric,              -- total $ deposited with this document
    lines         text,                 -- JSON [{order_ref, order_date, company, amount, commission}, ...]
    confidence    text,
    source_file   text unique           -- bucket path; enables upsert / dedupe
);

-- Our own invoices TO the catering platforms, mirrored from the Google Drive
-- "Catering" folder (Power Automate copies new PDFs into drive_invoices/ of the
-- catering bucket; drive_invoice_ingest.py reads the platform ones). Lets the app
-- flag delivered platform orders with no invoice raised yet.
create table if not exists drive_invoices (
    id             bigint generated always as identity primary key,
    saved_at       text,
    invoice_no     text,                -- our invoice number ('1061')
    platform       text,                -- 'Hampr' | 'Eat First' | 'Yordar'
    company        text,                -- end customer in the line description (Rokt, DHL…)
    invoice_date   text,                -- YYYY-MM-DD
    total_inc_gst  numeric,             -- BALANCE DUE inc GST (what the platform deposits)
    confidence     text,
    source_file    text unique          -- bucket path; enables upsert / dedupe
);

-- Weekly delivery-platform PAYMENT summaries (Uber Eats / DoorDash), ingested from the
-- Storage bucket by delivery_ingest.py. These carry the ACTUAL net the platform pays the
-- venue, so the app replaces its flat 40%-commission estimate with the real figure for the
-- matching ISO week (gross_incl_gst is present for Uber, 0 for DoorDash whose email only
-- states the net — the app pairs that net with the DoorDash gross from the POS slips).
-- source_file is UNIQUE so the ingest Action can re-run without creating duplicates.
create table if not exists delivery_payouts (
    id             bigint generated always as identity primary key,
    saved_at       text,
    platform       text,                -- 'Uber Eats' | 'DoorDash'
    platform_key   text,                -- 'ubereats' | 'doordash' (matches the POS columns)
    period_start   text,                -- YYYY-MM-DD pay-week start
    period_end     text,                -- YYYY-MM-DD pay-week end
    iso_week       text,                -- YYYY-Www derived from period_start (join key to POS)
    gross_incl_gst numeric,             -- week's sales incl GST (Uber); 0 if not in the email
    net_payout     numeric,             -- ACTUAL money deposited this week
    ad_spend       numeric,             -- marketing/ad spend (positive); 0 if none
    fees_total     numeric,             -- platform service/commission fee (positive); 0 if not shown
    orders         integer,
    confidence     text,
    source_file    text unique          -- bucket path; enables upsert / dedupe
);

-- This app runs server-side on Streamlit Cloud and connects with the service_role
-- key, so Row Level Security is not required. If you prefer to enable RLS, add
-- policies that allow the service role full access.
