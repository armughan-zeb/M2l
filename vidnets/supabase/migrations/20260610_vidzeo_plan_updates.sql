-- Vidzeo Plan Updates — Simplified to 3 monthly plans with new pricing/features
-- Basic: $19/mo, 15 min, 60 exports
-- Plus: $49/mo, 30 min, unlimited exports
-- Max: $99/mo, 2 hr+, unlimited exports, 3 devices

-- 1. Update plans table
UPDATE public.plans SET
    price_monthly_display = 19.00,
    price_label = '$19 / Month',
    video_limit_minutes = 15,
    script_word_limit = 2340,
    unlimited_videos = false,
    feature_summary = '["Videos up to 15 minutes","60 Exports Per Month"]'::jsonb,
    sort_order = 1
WHERE plan_key = 'basic_monthly';

UPDATE public.plans SET
    price_monthly_display = 49.00,
    price_label = '$49 / Month',
    video_limit_minutes = 30,
    script_word_limit = 4680,
    unlimited_videos = true,
    feature_summary = '["Videos up to 30 minutes","Unlimited Exports"]'::jsonb,
    sort_order = 2
WHERE plan_key = 'plus_monthly';

UPDATE public.plans SET
    price_monthly_display = 99.00,
    price_label = '$99 / Month',
    video_limit_minutes = 180,
    script_word_limit = 28080,
    unlimited_videos = true,
    feature_summary = '["Everything in Plus","Videos up to 2 hours+","Unlimited Exports","Up to 3 Devices"]'::jsonb,
    sort_order = 3
WHERE plan_key = 'max_monthly';

-- 2. Deactivate yearly plans
UPDATE public.plans SET is_active = false WHERE billing_period = 'yearly';

-- 3. Update pricing_catalog
UPDATE public.pricing_catalog SET
    price_label = '$19 / Month',
    duration_minutes = 15,
    feature_summary = '["Videos up to 15 minutes","60 Exports Per Month"]'::jsonb
WHERE plan_key = 'basic_monthly';

UPDATE public.pricing_catalog SET
    price_label = '$49 / Month',
    duration_minutes = 30,
    feature_summary = '["Videos up to 30 minutes","Unlimited Exports"]'::jsonb
WHERE plan_key = 'plus_monthly';

UPDATE public.pricing_catalog SET
    price_label = '$99 / Month',
    duration_minutes = 180,
    feature_summary = '["Everything in Plus","Videos up to 2 hours+","Unlimited Exports","Up to 3 Devices"]'::jsonb
WHERE plan_key = 'max_monthly';

UPDATE public.pricing_catalog SET is_active = false WHERE billing_period = 'yearly';

-- 4. Update is_vidzeo_paid_plan to remove yearly keys
CREATE OR REPLACE FUNCTION public.is_vidzeo_paid_plan(p_plan_key text)
RETURNS boolean
LANGUAGE sql
IMMUTABLE
AS $$
    SELECT lower(coalesce(p_plan_key, '')) IN (
        'basic_monthly',
        'plus_monthly',
        'max_monthly'
    );
$$;

notify pgrst, 'reload schema';
