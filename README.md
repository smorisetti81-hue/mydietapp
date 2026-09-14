# MyDietApp V86.2 — PostgreSQL profile persistence

Phase 1 moves the MyDiet profile from ephemeral Streamlit state to PostgreSQL/Supabase.

## Files
- `app.py` — V85 app + V86.2 durable profile persistence
- `db.py` — PostgreSQL access layer
- `schema.sql` — one-time database schema
- `requirements.txt` — adds psycopg

## Supabase setup
1. Create/open a Supabase project.
2. In **SQL Editor**, run `schema.sql`.
3. Copy the project's Postgres connection string (prefer the pooler connection string for hosted Streamlit).
4. In Streamlit Cloud → App → Settings → Secrets add:

```toml
DATABASE_URL = "postgresql://..."
```

Keep the existing Gemini and Health Sync secrets unchanged.

## Test
Use the existing URL with the stable mdid:
`?mdid=c475e5a10b9144b6bb6cde887e06caee`

After deployment:
1. Open Profile.
2. Change a harmless field (e.g. water goal) and Save.
3. Confirm the UI reports `salvato su PostgreSQL` if status is exposed.
4. Restart/reload the Streamlit app.
5. Confirm the profile is restored even after the app container/session is recreated.

If `DATABASE_URL` is missing or temporarily unavailable, V86.2 falls back to the existing `/tmp` persistence and does not break the app.


## Phase 2 — Meal plan persistence

V86.2 adds durable PostgreSQL persistence for the meal-plan domain without dumping the Streamlit session state into the database. The `meal_plans` table stores the active plan, next-week draft, history, meal overrides, out-of-home settings and menu associations.

After deploying V86.2, the first test is intentionally simple: open **Piano**, verify the existing plan is visible, make one harmless meal edit, allow the app to rerun, then restart/redeploy the Streamlit app and verify the edit is still present.

The existing `/tmp` snapshot remains as a transition fallback; it is not the durable source for plan data when PostgreSQL is available.
