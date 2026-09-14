-- MyDiet v86 — Phase 1 PostgreSQL/Supabase schema
-- Run once in the Supabase SQL Editor.

CREATE TABLE IF NOT EXISTS profiles (
    mdid TEXT PRIMARY KEY,
    name TEXT NOT NULL DEFAULT '',
    weight DOUBLE PRECISION,
    goal_weight DOUBLE PRECISION,
    height DOUBLE PRECISION,
    age INTEGER,
    sex TEXT,
    activity_level TEXT,
    deficit DOUBLE PRECISION,
    water_goal_ml INTEGER,
    quantity_mode TEXT,
    diet_goal TEXT,
    diet_style TEXT,
    custom_calorie_target INTEGER,
    activity_tracking_mode TEXT,
    training_frequency TEXT,
    allergies TEXT,
    excluded_foods TEXT,
    profile_setup_complete BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_profiles_updated_at ON profiles(updated_at);
