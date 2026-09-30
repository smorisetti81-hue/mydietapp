# MyDietApp V100.1 – Health Auto Refresh Fix

Based on V100.

- Keeps automatic UI refresh every 15 seconds on Home and Attività.
- Keeps remote Health Sync polling.
- FIX: health snapshot fingerprint now hashes the entire payload instead of only its first 32 characters.
- This detects changes in steps/calories/distance even when the beginning of the encoded payload remains unchanged.
- Bridge V1.8.2 is not modified.
