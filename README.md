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
