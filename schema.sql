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


-- MyDiet v86.2 — Phase 2 durable meal-plan aggregate
CREATE TABLE IF NOT EXISTS meal_plans (
    mdid TEXT PRIMARY KEY REFERENCES profiles(mdid) ON DELETE CASCADE,
    plan_week_start DATE,
    meal_plan JSONB NOT NULL DEFAULT '{}'::jsonb,
    overrides JSONB NOT NULL DEFAULT '{}'::jsonb,
    plan_history JSONB NOT NULL DEFAULT '{}'::jsonb,
    out_lunch_days JSONB NOT NULL DEFAULT '[]'::jsonb,
    out_dinner_days JSONB NOT NULL DEFAULT '[]'::jsonb,
    mensa_menus JSONB NOT NULL DEFAULT '{}'::jsonb,
    next_meal_plan JSONB,
    next_overrides JSONB NOT NULL DEFAULT '{}'::jsonb,
    next_week_start DATE,
    next_out_lunch_days JSONB NOT NULL DEFAULT '[]'::jsonb,
    next_out_dinner_days JSONB NOT NULL DEFAULT '[]'::jsonb,
    next_mensa_menus JSONB NOT NULL DEFAULT '{}'::jsonb,
    plan_needs_regeneration BOOLEAN NOT NULL DEFAULT FALSE,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_meal_plans_updated_at ON meal_plans(updated_at);
ALTER TABLE meal_plans ENABLE ROW LEVEL SECURITY;

-- MyDiet v87 — durable meal registrations / logs
CREATE TABLE IF NOT EXISTS meal_logs (
    mdid TEXT NOT NULL REFERENCES profiles(mdid) ON DELETE CASCADE,
    meal_date DATE NOT NULL,
    meal_name TEXT NOT NULL,
    registered BOOLEAN NOT NULL DEFAULT FALSE,
    eaten_items JSONB NOT NULL DEFAULT '[]'::jsonb,
    registered_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (mdid, meal_date, meal_name)
);
CREATE INDEX IF NOT EXISTS idx_meal_logs_date ON meal_logs(mdid, meal_date);
ALTER TABLE meal_logs ENABLE ROW LEVEL SECURITY;
