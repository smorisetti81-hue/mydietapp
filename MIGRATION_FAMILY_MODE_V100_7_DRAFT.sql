-- MyDietApp V100.7 — additive family-mode foundation (DRAFT)
-- IMPORTANT: review and test in a Supabase staging project before applying.
-- This migration intentionally does not alter profiles, meal_plans, meal_logs,
-- existing mdid values, pantry data, or any existing user records.
-- Requires Supabase Auth (auth.uid()). Family membership must be granted by a
-- trusted server-side invite flow; never accept arbitrary user_id from the client.

BEGIN;

CREATE TABLE IF NOT EXISTS public.family_groups (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL DEFAULT 'La mia famiglia',
    owner_user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE RESTRICT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS public.family_members (
    family_id UUID NOT NULL REFERENCES public.family_groups(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    role TEXT NOT NULL DEFAULT 'member' CHECK (role IN ('owner', 'member')),
    display_name TEXT NOT NULL DEFAULT '',
    joined_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (family_id, user_id),
    -- Initial release: one family per adult account. Can be revisited later.
    UNIQUE (user_id)
);

CREATE TABLE IF NOT EXISTS public.family_invites (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    family_id UUID NOT NULL REFERENCES public.family_groups(id) ON DELETE CASCADE,
    invited_by UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    invite_token_hash TEXT NOT NULL UNIQUE,
    expires_at TIMESTAMPTZ NOT NULL,
    accepted_by UUID REFERENCES auth.users(id) ON DELETE SET NULL,
    accepted_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (expires_at > created_at)
);

CREATE TABLE IF NOT EXISTS public.family_pantry_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    family_id UUID NOT NULL REFERENCES public.family_groups(id) ON DELETE CASCADE,
    item_key TEXT NOT NULL,
    item_data JSONB NOT NULL DEFAULT '{}'::jsonb,
    quantity NUMERIC,
    unit TEXT NOT NULL DEFAULT '',
    updated_by UUID REFERENCES auth.users(id) ON DELETE SET NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (family_id, item_key)
);

CREATE TABLE IF NOT EXISTS public.family_meal_assignments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    family_id UUID NOT NULL REFERENCES public.family_groups(id) ON DELETE CASCADE,
    meal_date DATE NOT NULL,
    meal_name TEXT NOT NULL,
    sharing_mode TEXT NOT NULL DEFAULT 'shared'
        CHECK (sharing_mode IN ('shared', 'individual', 'mixed')),
    meal_data JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_by UUID REFERENCES auth.users(id) ON DELETE SET NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (family_id, meal_date, meal_name)
);

CREATE TABLE IF NOT EXISTS public.family_meal_people (
    assignment_id UUID NOT NULL REFERENCES public.family_meal_assignments(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    portion_data JSONB NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (assignment_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_family_members_user ON public.family_members(user_id);
CREATE INDEX IF NOT EXISTS idx_family_pantry_family ON public.family_pantry_items(family_id);
CREATE INDEX IF NOT EXISTS idx_family_meals_date ON public.family_meal_assignments(family_id, meal_date);

-- New family owner is automatically added as the first member.
CREATE OR REPLACE FUNCTION public.mydiet_add_family_owner_member()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
BEGIN
    INSERT INTO public.family_members (family_id, user_id, role)
    VALUES (NEW.id, NEW.owner_user_id, 'owner')
    ON CONFLICT (family_id, user_id) DO NOTHING;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_mydiet_family_owner_member ON public.family_groups;
CREATE TRIGGER trg_mydiet_family_owner_member
AFTER INSERT ON public.family_groups
FOR EACH ROW EXECUTE FUNCTION public.mydiet_add_family_owner_member();

ALTER TABLE public.family_groups ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.family_members ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.family_invites ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.family_pantry_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.family_meal_assignments ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.family_meal_people ENABLE ROW LEVEL SECURITY;

-- Family data is visible only to authenticated users who are members.
DROP POLICY IF EXISTS family_groups_select_member ON public.family_groups;
CREATE POLICY family_groups_select_member ON public.family_groups
FOR SELECT TO authenticated
USING (EXISTS (
    SELECT 1 FROM public.family_members m
    WHERE m.family_id = family_groups.id AND m.user_id = (SELECT auth.uid())
));

DROP POLICY IF EXISTS family_groups_insert_owner ON public.family_groups;
CREATE POLICY family_groups_insert_owner ON public.family_groups
FOR INSERT TO authenticated
WITH CHECK (owner_user_id = (SELECT auth.uid()));

DROP POLICY IF EXISTS family_groups_update_owner ON public.family_groups;
CREATE POLICY family_groups_update_owner ON public.family_groups
FOR UPDATE TO authenticated
USING (owner_user_id = (SELECT auth.uid()))
WITH CHECK (owner_user_id = (SELECT auth.uid()));

DROP POLICY IF EXISTS family_members_select_member ON public.family_members;
CREATE POLICY family_members_select_member ON public.family_members
FOR SELECT TO authenticated
USING (EXISTS (
    SELECT 1 FROM public.family_members mine
    WHERE mine.family_id = family_members.family_id AND mine.user_id = (SELECT auth.uid())
));

-- No client-side insert/update/delete policy for membership or invites.
-- A trusted server-side invite-acceptance flow must validate hashed tokens,
-- expiry, one-time use, and membership limits before granting access.

DROP POLICY IF EXISTS family_pantry_select_member ON public.family_pantry_items;
CREATE POLICY family_pantry_select_member ON public.family_pantry_items
FOR SELECT TO authenticated
USING (EXISTS (
    SELECT 1 FROM public.family_members m
    WHERE m.family_id = family_pantry_items.family_id AND m.user_id = (SELECT auth.uid())
));
DROP POLICY IF EXISTS family_pantry_insert_member ON public.family_pantry_items;
CREATE POLICY family_pantry_insert_member ON public.family_pantry_items
FOR INSERT TO authenticated
WITH CHECK (EXISTS (
    SELECT 1 FROM public.family_members m
    WHERE m.family_id = family_pantry_items.family_id AND m.user_id = (SELECT auth.uid())
));
DROP POLICY IF EXISTS family_pantry_update_member ON public.family_pantry_items;
CREATE POLICY family_pantry_update_member ON public.family_pantry_items
FOR UPDATE TO authenticated
USING (EXISTS (
    SELECT 1 FROM public.family_members m
    WHERE m.family_id = family_pantry_items.family_id AND m.user_id = (SELECT auth.uid())
))
WITH CHECK (EXISTS (
    SELECT 1 FROM public.family_members m
    WHERE m.family_id = family_pantry_items.family_id AND m.user_id = (SELECT auth.uid())
));
DROP POLICY IF EXISTS family_pantry_delete_member ON public.family_pantry_items;
CREATE POLICY family_pantry_delete_member ON public.family_pantry_items
FOR DELETE TO authenticated
USING (EXISTS (
    SELECT 1 FROM public.family_members m
    WHERE m.family_id = family_pantry_items.family_id AND m.user_id = (SELECT auth.uid())
));

DROP POLICY IF EXISTS family_meals_select_member ON public.family_meal_assignments;
CREATE POLICY family_meals_select_member ON public.family_meal_assignments
FOR SELECT TO authenticated
USING (EXISTS (
    SELECT 1 FROM public.family_members m
    WHERE m.family_id = family_meal_assignments.family_id AND m.user_id = (SELECT auth.uid())
));
DROP POLICY IF EXISTS family_meals_insert_member ON public.family_meal_assignments;
CREATE POLICY family_meals_insert_member ON public.family_meal_assignments
FOR INSERT TO authenticated
WITH CHECK (EXISTS (
    SELECT 1 FROM public.family_members m
    WHERE m.family_id = family_meal_assignments.family_id AND m.user_id = (SELECT auth.uid())
));
DROP POLICY IF EXISTS family_meals_update_member ON public.family_meal_assignments;
CREATE POLICY family_meals_update_member ON public.family_meal_assignments
FOR UPDATE TO authenticated
USING (EXISTS (
    SELECT 1 FROM public.family_members m
    WHERE m.family_id = family_meal_assignments.family_id AND m.user_id = (SELECT auth.uid())
))
WITH CHECK (EXISTS (
    SELECT 1 FROM public.family_members m
    WHERE m.family_id = family_meal_assignments.family_id AND m.user_id = (SELECT auth.uid())
));
DROP POLICY IF EXISTS family_meals_delete_member ON public.family_meal_assignments;
CREATE POLICY family_meals_delete_member ON public.family_meal_assignments
FOR DELETE TO authenticated
USING (EXISTS (
    SELECT 1 FROM public.family_members m
    WHERE m.family_id = family_meal_assignments.family_id AND m.user_id = (SELECT auth.uid())
));

DROP POLICY IF EXISTS family_meal_people_select_member ON public.family_meal_people;
CREATE POLICY family_meal_people_select_member ON public.family_meal_people
FOR SELECT TO authenticated
USING (EXISTS (
    SELECT 1 FROM public.family_meal_assignments a
    JOIN public.family_members m ON m.family_id = a.family_id
    WHERE a.id = family_meal_people.assignment_id AND m.user_id = (SELECT auth.uid())
));
-- Writes to per-person portions are intentionally withheld until the trusted
-- membership/invite flow and individual profile privacy rules are implemented.

COMMIT;
