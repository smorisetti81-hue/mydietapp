# MyDietApp V100.1 – Health Auto Refresh Fix

Based on V100.

- Keeps automatic UI refresh every 15 seconds on Home and Attività.
- Keeps remote Health Sync polling.
- FIX: health snapshot fingerprint now hashes the entire payload instead of only its first 32 characters.
- This detects changes in steps/calories/distance even when the beginning of the encoded payload remains unchanged.
- Bridge V1.8.2 is not modified.


## V100.2 – Health Sync profile fix
- Health Sync lookup now uses the current MyDiet `mdid`, matching the Android Bridge configuration (`ID profilo MyDiet`).
- `HEALTH_BRIDGE_PROFILE_ID` remains an optional Streamlit Secret override for special installations.
- Removed the hardcoded profile fallback that could cause a valid Bridge snapshot to return HTTP 404.
- A 404 is now reported as “snapshot not found for current profile” instead of a generic HTTP error.
- Android Bridge V1.8.2 is unchanged.


## V100.3 – TDEE calorie target engine
- Automatic targets now start from estimated daily maintenance (TDEE = BMR × activity factor), then adjust for the selected goal.
- Weight loss: estimated 15% reduction from TDEE, capped at 500 kcal/day, with the existing 1500 kcal minimum retained.
- Maintenance: estimated TDEE.
- Weight gain: modest surplus (10%, bounded to 200–300 kcal/day).
- Muscle gain: modest surplus (7%, bounded to 150–250 kcal/day).
- Custom calorie target remains supported; when no custom value exists, the UI defaults to estimated TDEE rather than BMR.
- Legacy profile deficit and weight projection now use the same target calculation; a zero deficit is no longer silently replaced with 500 kcal/day.
- No database schema, database connection, stored profile, pantry, meal plan, or history data is modified by this release.
- The 1500 kcal floor is a conservative software guard retained from the prior version, not an individualized medical recommendation.


## V100.4 – TDEE target label fix
- Corrected the profile-page automatic-target message so it reports estimated maintenance (TDEE) and the goal adjustment, rather than incorrectly saying the target is calculated directly from BMR.
- Clarified the energy-details caption: TDEE is estimated as BMR × activity factor, then adjusted according to the selected goal.
- No calorie formulas, database schema, database connection, stored profile, pantry, meal plan, or history data were changed.


## V100.5 – Weekly plan calorie alignment
- Weekly-plan prompt now specifies the daily target and approximate calorie distribution across breakfast, snack, lunch, and dinner.
- Added a deterministic consistency safeguard after generation: if a normal at-home day differs from the target by more than 8%, ingredient quantities and their kcal values are proportionally adjusted when the required factor is within 0.75–1.40.
- Days containing an outside-home meal, or without usable ingredient calories, are left untouched rather than misleadingly scaling the other meals.
- This safeguard aligns the displayed arithmetic; it does not independently verify nutrition quality or medical suitability. Review ingredient quantities when testing the generated plan.
- No database schema, connection, profile, pantry, current plan, or history data is changed by the code update. Existing plans are not automatically rewritten; the safeguard applies when a new plan is generated.
