# MyDietApp V88.3 — Calorie total fix

Fixes the Home calorie calculation so weekly-plan `eaten` flags from previous days/test sessions are no longer summed into today's total.

Today's total now considers only ingredient states belonging to today's planned meals, plus today's manual foods.

The temporary calorie diagnostic was aligned with the same day-scoped logic.
