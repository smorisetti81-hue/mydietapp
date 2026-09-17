# MyDietApp V87.3 — PostgreSQL meal logs + performance fix

Based on V87.1. Meal-log persistence is now fingerprint-gated: PostgreSQL is not contacted on ordinary Streamlit reruns/navigation unless the durable meal-registration state actually changed.

This reduces the delay when switching Home/Piano/Dispensa/Attività/Profilo and avoids the transient dim/duplicate-looking render that can appear while Streamlit is waiting for an unnecessary DB write.

No Secrets changes are required. Existing DATABASE_URL remains valid.


## V87.3 — fix legacy Thursday out-of-home flags
- Default `out_lunch_days` / `out_dinner_days` is now empty.
- On first load, if PostgreSQL still contains the exact legacy pair `Giovedì` for both lunch and dinner, the app clears them only when the stored Thursday meals are normal planned meals (not `FUORI CASA`) and immediately persists the correction.
- No other meal-log or plan persistence behavior is changed.
