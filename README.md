# MyDietApp v87 — PostgreSQL meal logs

V87 adds durable meal registration persistence on top of the working v86.x profile + meal-plan persistence.

## What is persisted
- meal registration state for the active weekly plan
- partially checked/eaten ingredient IDs
- registration timestamp

## Migration
No manual SQL is required: the app creates `public.meal_logs` automatically after the profile exists.
The included `schema.sql` also contains the table definition for reference/manual setup.

## Test
1. Deploy V87.
2. Open Home.
3. Register one planned meal.
4. Confirm the meal shows `✓ Registrato` and calories update.
5. Reboot/reload Streamlit.
6. Confirm the same meal is still registered and the calorie total is preserved.
7. Press undo registration, reboot, and confirm it is no longer registered.

PostgreSQL remains the durable source for profile, plan and meal registrations. The existing /tmp snapshot remains only as a transition fallback.
