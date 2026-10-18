# MyDietApp V100.7 — Family Mode Foundation (draft)

Based on **V100.6 – MACRO ESTIMATES**. The original app code and existing database schema are retained. This release adds a reviewed starting point for family-mode architecture; it does **not** yet activate family mode in the app.

## Included
- `MIGRATION_FAMILY_MODE_V100_7_DRAFT.sql`: additive draft for Supabase/Postgres family groups, adult membership, invite-token records, shared pantry, shared meal assignments, and per-person meal portions.
- Row-level security policies limit shared family data to authenticated family members.
- The family owner is automatically added as a member when a family group is created.
- Existing `profiles`, `meal_plans`, `meal_logs`, `mdid` identifiers, plans, and pantry data are not modified by this draft.

## Important status / limitations
- **Do not run this migration on production yet.** Review and test it in a staging Supabase project first.
- This is a database foundation only. The current Streamlit app still uses its existing `mdid` flow; this release does not add Supabase Auth, invite acceptance, account linking, or family screens.
- Invite creation/acceptance needs a trusted server-side implementation that validates hashed, expiring, one-time tokens. Client-side membership writes are intentionally not enabled.
- Individual profiles, calorie targets, food logs, and progress must remain private per adult account. Children must not inherit an adult weight-loss calorie deficit; age-appropriate planning rules are still required before family meal generation is enabled.
- Pantry quantities must only be deducted when a meal is confirmed consumed, not when a plan is generated. The current app flow is unchanged by this draft.

## V100.6 behavior retained
- TDEE-based calorie target calculation.
- Weekly-plan calorie alignment safeguard.
- AI-estimated macros are shown only when available and remain labelled as estimates.
- Existing app dependencies and runtime entry point are unchanged.

## Next implementation steps
1. Test and refine this migration in a separate Supabase staging project.
2. Add Supabase Auth and securely link each adult account to one family group without using `mdid` as authorization.
3. Implement invite acceptance on the server and test RLS with two separate accounts.
4. Add family setup and shared pantry/meal views, then test meal portions and shopping-list aggregation.
5. Add age-appropriate child portions before enabling child meal planning.
