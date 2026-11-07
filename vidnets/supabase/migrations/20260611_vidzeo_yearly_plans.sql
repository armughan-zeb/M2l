-- Add yearly plan keys back to is_vidzeo_paid_plan
CREATE OR REPLACE FUNCTION public.is_vidzeo_paid_plan(p_plan_key text)
RETURNS boolean
LANGUAGE sql
IMMUTABLE
AS $$
    SELECT lower(coalesce(p_plan_key, '')) IN (
        'basic_monthly',
        'plus_monthly',
        'max_monthly',
        'basic_yearly',
        'plus_yearly',
        'max_yearly'
    );
$$;

notify pgrst, 'reload schema';
