-- Polar is the active billing provider for Vidzeo.
-- Product UUIDs are written after the Polar catalog is created; secrets never
-- live in this migration or in the desktop/website bundles.

alter table if exists public.plans
  alter column billing_provider set default 'polar';
alter table if exists public.billing_customers
  alter column provider set default 'polar';
alter table if exists public.billing_subscriptions
  alter column provider set default 'polar';
alter table if exists public.billing_events
  alter column provider set default 'polar';

update public.plans
set billing_provider = 'polar',
    -- Keep UUIDs already provisioned by the catalog bootstrap. Numeric legacy
    -- provider IDs are discarded so checkout can never call the old provider.
    provider_product_id = case when provider_product_id ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$' then provider_product_id else null end,
    provider_variant_id = case when provider_variant_id ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$' then provider_variant_id else null end,
    checkout_url = null,
    updated_at = now()
where plan_key in ('base_monthly', 'basic_monthly', 'plus_monthly', 'max_monthly',
                   'basic_yearly', 'plus_yearly', 'max_yearly', 'base_yearly');

update public.plans set name = 'Creator', display_name = 'Creator Yearly', updated_at = now()
where plan_key = 'basic_yearly';
update public.plans set name = 'Pro', display_name = 'Pro Yearly', updated_at = now()
where plan_key = 'plus_yearly';

insert into public.plans (
  plan_key, name, display_name, billing_period, price_monthly_display,
  price_label, video_limit_minutes, script_word_limit, resolution_label,
  unlimited_videos, unlimited_voiceovers, media_sources_label, feature_summary,
  metadata, sort_order, is_active, billing_provider
) values (
  'base_yearly', 'Base', 'Base', 'yearly', 1.00, '$1 / Month (Yearly)', 5, 780,
  '1080p Exports', false, true, 'AI + Stock + Real Media',
  '["Videos up to 5 minutes", "10 Exports Per Month", "Free Audio Voiceovers", "1 Device Login"]'::jsonb,
  '{"monthly_export_limit": 10}'::jsonb, 1, true, 'polar'
) on conflict (plan_key) do update set
  billing_provider = 'polar', price_monthly_display = 1.00,
  price_label = '$1 / Month (Yearly)', is_active = true, updated_at = now();

insert into public.pricing_catalog (
  plan_key, label, plan_display_name, billing_period, tier_name, price_label,
  duration_minutes, feature_summary, is_active, sort_order
) values (
  'base_yearly', 'Yearly', 'Base', 'yearly', 'Base', '$1 / Month (Yearly)', 5,
  '["Videos up to 5 minutes", "10 Exports Per Month", "Free Audio Voiceovers", "1 Device Login"]'::jsonb,
  true, 1
) on conflict (plan_key) do update set
  price_label = '$1 / Month (Yearly)', is_active = true;

create or replace function public.apply_polar_subscription(
    p_auth_user_id uuid,
    p_email text,
    p_full_name text,
    p_provider_customer_id text,
    p_provider_subscription_id text,
    p_plan_key text,
    p_status text,
    p_period_start timestamptz default null,
    p_period_end timestamptz default null,
    p_cancel_at_period_end boolean default false,
    p_metadata jsonb default '{}'::jsonb
)
returns jsonb
language plpgsql
security definer
set search_path = public, auth
as $$
declare
    v_profile public.user_profiles%rowtype;
    v_subscription public.billing_subscriptions%rowtype;
    v_assignment public.user_plan_assignments%rowtype;
    v_assignment_status text;
    v_email text;
begin
    if p_auth_user_id is null or p_provider_subscription_id is null or p_plan_key is null then
        raise exception 'Billing subscription is missing identity, provider subscription, or plan key';
    end if;
    if not exists (select 1 from public.plans where plan_key = p_plan_key and is_active = true and billing_provider = 'polar') then
        raise exception 'Polar billing plan is not active: %', p_plan_key;
    end if;

    select coalesce(nullif(lower(trim(p_email)), ''), lower(trim(u.email)))
      into v_email
      from auth.users u
     where u.id = p_auth_user_id;
    if v_email is null or v_email = '' then raise exception 'Billing user email is missing'; end if;

    insert into public.user_profiles (auth_user_id, email, full_name, account_status)
    values (p_auth_user_id, v_email, nullif(trim(p_full_name), ''), 'active')
    on conflict (auth_user_id) do update set
        email = coalesce(nullif(excluded.email, ''), public.user_profiles.email),
        full_name = coalesce(nullif(excluded.full_name, ''), public.user_profiles.full_name),
        updated_at = now();
    select * into v_profile from public.user_profiles where auth_user_id = p_auth_user_id;

    insert into public.billing_customers (auth_user_id, user_profile_id, provider, provider_customer_id, email)
    values (p_auth_user_id, v_profile.id, 'polar', nullif(p_provider_customer_id, ''), v_email)
    on conflict (auth_user_id) do update set
        user_profile_id = excluded.user_profile_id,
        provider = 'polar',
        provider_customer_id = coalesce(excluded.provider_customer_id, public.billing_customers.provider_customer_id),
        email = coalesce(nullif(excluded.email, ''), public.billing_customers.email),
        updated_at = now();

    insert into public.billing_subscriptions (
        auth_user_id, user_profile_id, plan_key, provider, provider_subscription_id,
        provider_customer_id, status, current_period_start, current_period_end,
        cancel_at_period_end, metadata
    ) values (
        p_auth_user_id, v_profile.id, p_plan_key, 'polar', p_provider_subscription_id,
        nullif(p_provider_customer_id, ''), p_status, p_period_start, p_period_end,
        coalesce(p_cancel_at_period_end, false), coalesce(p_metadata, '{}'::jsonb)
    ) on conflict (provider, provider_subscription_id) do update set
        user_profile_id = excluded.user_profile_id,
        plan_key = excluded.plan_key,
        provider_customer_id = coalesce(excluded.provider_customer_id, public.billing_subscriptions.provider_customer_id),
        status = excluded.status,
        current_period_start = excluded.current_period_start,
        current_period_end = excluded.current_period_end,
        cancel_at_period_end = excluded.cancel_at_period_end,
        metadata = excluded.metadata,
        updated_at = now()
    returning * into v_subscription;

    v_assignment_status := case
        when lower(coalesce(p_status, '')) in ('active', 'on_trial') then 'active'
        when lower(coalesce(p_status, '')) in ('cancelled', 'expired', 'past_due', 'unpaid') then 'expired'
        else 'pending'
    end;

    if v_assignment_status = 'active' then
        update public.user_plan_assignments
        set status = 'revoked', updated_at = now()
        where user_profile_id = v_profile.id
          and status = 'active'
          and coalesce(billing_subscription_id, gen_random_uuid()) <> v_subscription.id;
    end if;

    insert into public.user_plan_assignments (
        user_profile_id, plan_key, status, requested_by_email, starts_at, ends_at,
        note, billing_subscription_id
    ) values (
        v_profile.id, p_plan_key, v_assignment_status, v_email,
        coalesce(p_period_start, now()), p_period_end,
        'Polar subscription sync', v_subscription.id
    ) on conflict (billing_subscription_id) where billing_subscription_id is not null do update set
        plan_key = excluded.plan_key,
        status = excluded.status,
        starts_at = excluded.starts_at,
        ends_at = excluded.ends_at,
        note = excluded.note,
        updated_at = now()
    returning * into v_assignment;

    return jsonb_build_object(
        'profile_id', v_profile.id,
        'subscription_id', v_subscription.id,
        'assignment_id', v_assignment.id,
        'plan_key', v_assignment.plan_key,
        'status', v_assignment.status
    );
end;
$$;

revoke all on function public.apply_polar_subscription(uuid, text, text, text, text, text, text, timestamptz, timestamptz, boolean, jsonb) from public;
grant execute on function public.apply_polar_subscription(uuid, text, text, text, text, text, text, timestamptz, timestamptz, boolean, jsonb) to service_role;

notify pgrst, 'reload schema';
