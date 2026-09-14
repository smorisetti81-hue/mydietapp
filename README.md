# MyDietApp V86.3 — PostgreSQL profile + meal plan persistence

V86.3 fixes a persistence gap found during the V86.2 reboot test: current-week meal edits were being written to the transient `/tmp` snapshot but not necessarily to PostgreSQL before Streamlit reran.

## What changed
- Keeps V86.2 PostgreSQL profile + meal-plan storage.
- Before every Streamlit rerun, if the PostgreSQL meal-plan state has been loaded, the current durable meal-plan aggregate is saved to `meal_plans`.
- This covers current-week meal edits, additions/removals, quantity changes, out-of-home menu updates and other plan mutations that trigger a rerun.
- No changes to the UI workflow are required.

## Deploy
Replace the project files with:
- `app.py`
- `db.py`
- `schema.sql`
- `requirements.txt`

Keep the existing Streamlit secret:

```toml
DATABASE_URL = "postgresql://..."
```

## Test
1. Deploy V86.3.
2. Open the current plan and change one meal in an obvious way.
3. Confirm the change immediately appears.
4. Reboot/reload the Streamlit app.
5. Confirm the same change is still present.

Do not change the database password or other secrets for this test.
