-- Canonical Lemon Squeezy catalog for the Vidzeo store.
-- Store: 456770 (vidzeo.lemonsqueezy.com)
-- This migration intentionally contains public numeric catalog IDs only;
-- API/webhook secrets remain Supabase Edge Function secrets.

update public.plans
set billing_provider = 'lemon_squeezy',
    provider_product_id = case plan_key
      when 'basic_monthly' then '1317359'
      when 'basic_yearly' then '1317359'
      when 'plus_monthly' then '1317367'
      when 'plus_yearly' then '1317367'
      when 'max_monthly' then '1317370'
      when 'max_yearly' then '1317370'
      else provider_product_id
    end,
    provider_variant_id = case plan_key
      when 'basic_monthly' then '2059653'
      when 'basic_yearly' then '2059641'
      when 'plus_monthly' then '2059657'
      when 'plus_yearly' then '2059654'
      when 'max_monthly' then '2059662'
      when 'max_yearly' then '2059660'
      else provider_variant_id
    end,
    is_active = case
      when plan_key in (
        'basic_monthly', 'basic_yearly', 'plus_monthly',
        'plus_yearly', 'max_monthly', 'max_yearly'
      ) then true
      else is_active
    end,
    checkout_url = null,
    updated_at = now()
where plan_key in (
  'basic_monthly', 'basic_yearly', 'plus_monthly',
  'plus_yearly', 'max_monthly', 'max_yearly'
);
