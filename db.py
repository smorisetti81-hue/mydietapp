"""MyDiet v86 database layer.

Phase 1: durable profile persistence in PostgreSQL/Supabase.
The Streamlit session remains the UI/cache layer; this module is the durable source
for profile data. If DATABASE_URL is not configured, functions safely no-op so the
existing /tmp persistence can remain as a transition fallback.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any, Dict, Optional

try:
    from psycopg.types.json import Jsonb
except Exception:  # pragma: no cover
    Jsonb = None

try:
    import psycopg
except Exception:  # pragma: no cover - dependency may be absent locally
    psycopg = None

PROFILE_FIELDS = (
    "name", "weight", "goal_weight", "height", "age", "sex",
    "activity_level", "deficit", "water_goal_ml", "quantity_mode",
    "diet_goal", "diet_style", "custom_calorie_target",
    "activity_tracking_mode", "training_frequency", "allergies",
    "excluded_foods",
)


def database_url() -> str:
    # Streamlit secrets are read by app.py and passed explicitly when possible.
    return str(os.environ.get("DATABASE_URL", "") or "").strip()


@contextmanager
def connection(url: Optional[str] = None):
    if psycopg is None:
        raise RuntimeError("psycopg non installato")
    dsn = (url or database_url()).strip()
    if not dsn:
        raise RuntimeError("DATABASE_URL non configurato")
    # Supabase/Postgres normally requires TLS. sslmode=require is harmless when
    # already present and avoids accidentally sending credentials in clear text.
    if "sslmode=" not in dsn:
        dsn += ("&" if "?" in dsn else "?") + "sslmode=require"
    with psycopg.connect(dsn, connect_timeout=8) as conn:
        yield conn


def ensure_schema(url: Optional[str] = None) -> bool:
    """Create the Phase-1 profile table if it does not exist."""
    sql = """
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
    """
    with connection(url) as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.commit()
    return True


def load_profile(mdid: str, url: Optional[str] = None) -> Optional[Dict[str, Any]]:
    mdid = str(mdid or "").strip()
    if not mdid:
        return None
    columns = ", ".join(PROFILE_FIELDS) + ", profile_setup_complete"
    with connection(url) as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT {columns} FROM profiles WHERE mdid=%s", (mdid,))
            row = cur.fetchone()
            if not row:
                return None
            keys = list(PROFILE_FIELDS) + ["profile_setup_complete"]
            return dict(zip(keys, row))


def save_profile(mdid: str, values: Dict[str, Any], profile_setup_complete: bool = True,
                 url: Optional[str] = None) -> bool:
    mdid = str(mdid or "").strip()
    if not mdid:
        return False
    payload = {k: values.get(k) for k in PROFILE_FIELDS}
    payload["profile_setup_complete"] = bool(profile_setup_complete)

    cols = ["mdid"] + list(PROFILE_FIELDS) + ["profile_setup_complete"]
    placeholders = ", ".join(["%s"] * len(cols))
    assignments = ", ".join(
        f"{c}=EXCLUDED.{c}" for c in PROFILE_FIELDS + ("profile_setup_complete",)
    )
    sql = f"""
        INSERT INTO profiles ({', '.join(cols)})
        VALUES ({placeholders})
        ON CONFLICT (mdid) DO UPDATE SET
            {assignments},
            updated_at=NOW()
    """
    params = [mdid] + [payload[k] for k in PROFILE_FIELDS] + [payload["profile_setup_complete"]]
    with connection(url) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
        conn.commit()
    return True


PLAN_JSON_FIELDS = (
    "meal_plan", "overrides", "plan_history",
    "out_lunch_days", "out_dinner_days", "mensa_menus",
    "next_meal_plan", "next_overrides", "next_week_start",
    "next_out_lunch_days", "next_out_dinner_days", "next_mensa_menus",
)


def ensure_plan_schema(url: Optional[str] = None) -> bool:
    """Create the durable meal-plan aggregate table for the current profile."""
    sql = """
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
    """
    with connection(url) as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.commit()
    return True


def _json_param(value: Any):
    if Jsonb is None:
        raise RuntimeError("psycopg Jsonb non disponibile")
    return Jsonb(value if value is not None else {})


def load_meal_plan_state(mdid: str, url: Optional[str] = None) -> Optional[Dict[str, Any]]:
    mdid = str(mdid or "").strip()
    if not mdid:
        return None
    columns = (
        "plan_week_start, meal_plan, overrides, plan_history, out_lunch_days, out_dinner_days, "
        "mensa_menus, next_meal_plan, next_overrides, next_week_start, next_out_lunch_days, "
        "next_out_dinner_days, next_mensa_menus, plan_needs_regeneration"
    )
    with connection(url) as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT {columns} FROM meal_plans WHERE mdid=%s", (mdid,))
            row = cur.fetchone()
            if not row:
                return None
    keys = [
        "plan_week_start", "meal_plan", "overrides", "plan_history", "out_lunch_days",
        "out_dinner_days", "mensa_menus", "next_meal_plan", "next_overrides",
        "next_week_start", "next_out_lunch_days", "next_out_dinner_days",
        "next_mensa_menus", "plan_needs_regeneration"
    ]
    data = dict(zip(keys, row))
    # PostgreSQL DATE values are converted back to the ISO strings used by the app.
    for key in ("plan_week_start", "next_week_start"):
        if data.get(key) is not None:
            data[key] = data[key].isoformat() if hasattr(data[key], "isoformat") else str(data[key])
    return data


def save_meal_plan_state(mdid: str, state: Dict[str, Any], url: Optional[str] = None) -> bool:
    mdid = str(mdid or "").strip()
    if not mdid:
        return False
    sql = """
        INSERT INTO meal_plans (
            mdid, plan_week_start, meal_plan, overrides, plan_history,
            out_lunch_days, out_dinner_days, mensa_menus, next_meal_plan,
            next_overrides, next_week_start, next_out_lunch_days,
            next_out_dinner_days, next_mensa_menus, plan_needs_regeneration
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
        )
        ON CONFLICT (mdid) DO UPDATE SET
            plan_week_start=EXCLUDED.plan_week_start,
            meal_plan=EXCLUDED.meal_plan,
            overrides=EXCLUDED.overrides,
            plan_history=EXCLUDED.plan_history,
            out_lunch_days=EXCLUDED.out_lunch_days,
            out_dinner_days=EXCLUDED.out_dinner_days,
            mensa_menus=EXCLUDED.mensa_menus,
            next_meal_plan=EXCLUDED.next_meal_plan,
            next_overrides=EXCLUDED.next_overrides,
            next_week_start=EXCLUDED.next_week_start,
            next_out_lunch_days=EXCLUDED.next_out_lunch_days,
            next_out_dinner_days=EXCLUDED.next_out_dinner_days,
            next_mensa_menus=EXCLUDED.next_mensa_menus,
            plan_needs_regeneration=EXCLUDED.plan_needs_regeneration,
            updated_at=NOW()
    """
    params = [
        mdid, state.get("plan_week_start"), _json_param(state.get("meal_plan", {})),
        _json_param(state.get("overrides", {})), _json_param(state.get("plan_history", {})),
        _json_param(state.get("out_lunch_days", [])), _json_param(state.get("out_dinner_days", [])),
        _json_param(state.get("mensa_menus", {})),
        _json_param(state.get("next_meal_plan")) if state.get("next_meal_plan") is not None else None,
        _json_param(state.get("next_overrides", {})), state.get("next_week_start"),
        _json_param(state.get("next_out_lunch_days", [])),
        _json_param(state.get("next_out_dinner_days", [])), _json_param(state.get("next_mensa_menus", {})),
        bool(state.get("plan_needs_regeneration", False)),
    ]
    with connection(url) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
        conn.commit()
    return True
