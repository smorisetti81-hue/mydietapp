# MyDietApp V87.2 — PostgreSQL meal logs + performance fix

Based on V87.1. Meal-log persistence is now fingerprint-gated: PostgreSQL is not contacted on ordinary Streamlit reruns/navigation unless the durable meal-registration state actually changed.

This reduces the delay when switching Home/Piano/Dispensa/Attività/Profilo and avoids the transient dim/duplicate-looking render that can appear while Streamlit is waiting for an unnecessary DB write.

No Secrets changes are required. Existing DATABASE_URL remains valid.
