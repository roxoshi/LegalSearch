-- Add Gemini-generated annotation columns to acts table.
-- Run once against staging/prod after deploying models.py change.

ALTER TABLE acts ADD COLUMN IF NOT EXISTS q1          TEXT;
ALTER TABLE acts ADD COLUMN IF NOT EXISTS q2          TEXT;
ALTER TABLE acts ADD COLUMN IF NOT EXISTS q3          TEXT;
ALTER TABLE acts ADD COLUMN IF NOT EXISTS ann_summary TEXT;
