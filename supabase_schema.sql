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
    line_items    text          -- JSON string of [{description, quantity, unit, amount}, ...]
);

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
    adjusted_incl_gst numeric,               -- after netting the delivery commission
    adjusted_ex_gst   numeric,               -- ex-GST revenue used for COGS %
    saved_at          text
);
-- If pos_days already exists from before, add the bite column:
--   alter table pos_days add column if not exists bite numeric;

-- Food Safety daily temperature records — one record per day, stored as a JSON blob
-- (managers, all section temps, chicken cooks). Enables upsert on date.
create table if not exists food_safety (
    date      text primary key,
    data      text,                            -- JSON string of the day's full record
    saved_at  text
);

-- This app runs server-side on Streamlit Cloud and connects with the service_role
-- key, so Row Level Security is not required. If you prefer to enable RLS, add
-- policies that allow the service role full access.
