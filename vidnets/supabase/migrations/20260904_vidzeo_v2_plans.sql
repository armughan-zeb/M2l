-- Vidzeo Plan Updates — $1 Base, $19 Creator, $49 Pro (Most Popular)
-- Base: $1/mo, 5 min video limit, 10 exports per month, 1 device
-- Creator: $19/mo, 15 min video limit, 60 exports per month, 1 device
-- Pro: $49/mo, 30+ min video limit, unlimited exports, 2 devices (Most Popular)

-- 1. Insert or update Base Plan ($1)
INSERT INTO public.plans (
    plan_key, name, display_name, billing_period,
    price_monthly_display, price_label,
    video_limit_minutes, script_word_limit,
    resolution_label, unlimited_videos, unlimited_voiceovers,
    media_sources_label, feature_summary, metadata,
    sort_order, is_active
) VALUES (
    'base_monthly', 'Base', 'Base', 'monthly',
    1.00, '$1 / Month',
    5, 780,
    '1080p Exports', false, true,
    'AI + Stock + Real Media',
    '["Videos up to 5 minutes", "10 Exports Per Month", "Free Audio Voiceovers", "1 Device Login"]'::jsonb,
    '{"monthly_export_limit": 10}'::jsonb,
    1, true
) ON CONFLICT (plan_key) DO UPDATE SET
    name = 'Base',
    display_name = 'Base',
    price_monthly_display = 1.00,
    price_label = '$1 / Month',
    video_limit_minutes = 5,
    script_word_limit = 780,
    unlimited_videos = false,
    feature_summary = '["Videos up to 5 minutes", "10 Exports Per Month", "Free Audio Voiceovers", "1 Device Login"]'::jsonb,
    metadata = '{"monthly_export_limit": 10}'::jsonb,
    sort_order = 1,
    is_active = true,
    updated_at = now();

-- 2. Update Creator Plan ($19)
UPDATE public.plans SET
    name = 'Creator',
    display_name = 'Creator',
    price_monthly_display = 19.00,
    price_label = '$19 / Month',
    video_limit_minutes = 15,
    script_word_limit = 2340,
    unlimited_videos = false,
    feature_summary = '["Videos up to 15 minutes", "60 Exports Per Month", "Free Audio Voiceovers", "1 Device Login"]'::jsonb,
    metadata = '{"monthly_export_limit": 60}'::jsonb,
    sort_order = 2,
    is_active = true,
    updated_at = now()
WHERE plan_key = 'basic_monthly';

-- 3. Update Pro Plan ($49) - Most Popular
UPDATE public.plans SET
    name = 'Pro',
    display_name = 'Pro',
    price_monthly_display = 49.00,
    price_label = '$49 / Month',
    video_limit_minutes = 35,
    script_word_limit = 5460,
    unlimited_videos = true,
    feature_summary = '["Unlimited Exports", "Videos up to 30+ minutes", "Free Audio Voiceovers (Full Catalog)", "2 Device Logins", "Most Popular"]'::jsonb,
    metadata = '{"monthly_export_limit": null, "is_popular": true}'::jsonb,
    sort_order = 3,
    is_active = true,
    updated_at = now()
WHERE plan_key = 'plus_monthly';

-- 4. Update pricing_catalog table to mirror
INSERT INTO public.pricing_catalog (
    plan_key, price_label, duration_minutes,
    feature_summary, is_active, billing_period
) VALUES (
    'base_monthly', '$1 / Month', 5,
    '["Videos up to 5 minutes", "10 Exports Per Month", "Free Audio Voiceovers", "1 Device Login"]'::jsonb,
    true, 'monthly'
) ON CONFLICT (plan_key) DO UPDATE SET
    price_label = '$1 / Month',
    duration_minutes = 5,
    feature_summary = '["Videos up to 5 minutes", "10 Exports Per Month", "Free Audio Voiceovers", "1 Device Login"]'::jsonb,
    is_active = true;

UPDATE public.pricing_catalog SET
    price_label = '$19 / Month',
    duration_minutes = 15,
    feature_summary = '["Videos up to 15 minutes", "60 Exports Per Month", "Free Audio Voiceovers", "1 Device Login"]'::jsonb,
    is_active = true
WHERE plan_key = 'basic_monthly';

UPDATE public.pricing_catalog SET
    price_label = '$49 / Month',
    duration_minutes = 35,
    feature_summary = '["Unlimited Exports", "Videos up to 30+ minutes", "Free Audio Voiceovers (Full Catalog)", "2 Device Logins", "Most Popular"]'::jsonb,
    is_active = true
WHERE plan_key = 'plus_monthly';

-- 5. Update is_vidzeo_paid_plan function
CREATE OR REPLACE FUNCTION public.is_vidzeo_paid_plan(p_plan_key text)
RETURNS boolean
LANGUAGE sql
IMMUTABLE
AS $$
    SELECT lower(coalesce(p_plan_key, '')) IN (
        'base_monthly',
        'creator_monthly',
        'pro_monthly',
        'basic_monthly',
        'plus_monthly',
        'max_monthly'
    );
$$;

NOTIFY pgrst, 'reload schema';
