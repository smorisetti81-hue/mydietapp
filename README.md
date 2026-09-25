# MyDietApp V100 – Health UI Auto Refresh

V100 is based on the working V99 build.

## What changed
- Automatic UI refresh every 15 seconds on Home and Attività.
- Remote Health Sync polling reduced to a 10-second minimum interval.
- New health snapshots are applied only when their fingerprint changes.
- Bridge Android V1.8.2 is not modified.
- No manual browser refresh is required to see updated health values.

## Run
```bash
pip install -r requirements.txt
streamlit run app.py
```
