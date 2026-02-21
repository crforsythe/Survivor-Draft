-- ============================================================
-- Survivor Draft – Schema additions for cast data
-- Run this in: Supabase Dashboard → SQL Editor → New Query
--
-- Adds extra columns to castaways for scraped EW data.
-- Safe to run even if some columns already exist (uses IF NOT EXISTS workaround).
-- ============================================================

ALTER TABLE castaways ADD COLUMN IF NOT EXISTS tribe          TEXT;
ALTER TABLE castaways ADD COLUMN IF NOT EXISTS seasons_played TEXT;
ALTER TABLE castaways ADD COLUMN IF NOT EXISTS age            INTEGER;
ALTER TABLE castaways ADD COLUMN IF NOT EXISTS hometown       TEXT;
ALTER TABLE castaways ADD COLUMN IF NOT EXISTS occupation     TEXT;
ALTER TABLE castaways ADD COLUMN IF NOT EXISTS photo_url      TEXT;
