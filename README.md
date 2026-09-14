# MyDietApp V87.1 — PostgreSQL meal logs (safe import)

Based on the verified V86.6 build. Adds durable meal registration while making the new meal-log DB API safe against a stale db.py deployment: the app no longer crashes at import time if an old db.py is temporarily present.

Deploy all files together. Keep the existing DATABASE_URL secret unchanged.

After deployment, first verify Home/Piano still show the current plan. Then register one meal and reboot to test persistence.
