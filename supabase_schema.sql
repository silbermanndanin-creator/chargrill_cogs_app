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

create table if not exists pos_days (
    date              text primary key,      -- one finalised end-of-day slip per date; enables upsert on date
    iso_week          text,
    month             text,
    total_incl_gst    numeric,
    doordash          numeric,
    ubereats          numeric,
    adjusted_incl_gst numeric,               -- after netting the delivery commission
    adjusted_ex_gst   numeric,               -- ex-GST revenue used for COGS %
    saved_at          text
);

-- This app runs server-side on Streamlit Cloud and connects with the service_role
-- key, so Row Level Security is not required. If you prefer to enable RLS, add
-- policies that allow the service role full access.
