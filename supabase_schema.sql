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
