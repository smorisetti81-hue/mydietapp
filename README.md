# MyDietApp V86 — PostgreSQL profile persistence

Phase 1 moves the MyDiet profile from ephemeral Streamlit state to PostgreSQL/Supabase.

## Files
- `app.py` — V85 app + V86 durable profile persistence
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

If `DATABASE_URL` is missing or temporarily unavailable, V86 falls back to the existing `/tmp` persistence and does not break the app.
