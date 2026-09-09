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
