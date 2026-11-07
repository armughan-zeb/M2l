-- Stage the lifetime offer without publishing it. Existing recurring plans stay
-- active for subscribers, while new checkout is controlled separately.
alter table public.plans drop constraint if exists plans_billing_period_check;
alter table public.plans add constraint plans_billing_period_check
  check (billing_period in ('monthly', 'yearly', 'lifetime'));
alter table public.pricing_catalog drop constraint if exists pricing_catalog_billing_period_check;
alter table public.pricing_catalog add constraint pricing_catalog_billing_period_check
  check (billing_period in ('monthly', 'yearly', 'lifetime'));

alter table public.plans
  add column if not exists is_available_for_purchase boolean not null default true;

insert into public.plans (
  plan_key, name, display_name, billing_period, price_monthly_display,
  price_label, video_limit_minutes, script_word_limit, resolution_label,
  unlimited_videos, unlimited_voiceovers, media_sources_label, feature_summary,
  sort_order, is_active, is_available_for_purchase, billing_provider,
  currency_code, price_cents, metadata
) values (
  'lifetime_pro', 'Pro', 'Pro Lifetime', 'lifetime', 149,
  '$149 one-time launch price', 35, 5460, '4K & 1080p Exports',
  true, true, 'AI + Stock + Real Media',
  '["Pro features for life", "Unlimited video exports", "Videos up to 35 minutes", "Full voice catalog", "2 device logins", "4K and batch queue"]'::jsonb,
  1, true, false, 'polar', 'USD', 14900,
  '{"monthly_export_limit": null, "regular_price_cents": 149900}'::jsonb
) on conflict (plan_key) do update set
  name = excluded.name, display_name = excluded.display_name,
  billing_period = excluded.billing_period, price_monthly_display = excluded.price_monthly_display,
  price_label = excluded.price_label, video_limit_minutes = excluded.video_limit_minutes,
  script_word_limit = excluded.script_word_limit, resolution_label = excluded.resolution_label,
  unlimited_videos = excluded.unlimited_videos, unlimited_voiceovers = excluded.unlimited_voiceovers,
  feature_summary = excluded.feature_summary, sort_order = excluded.sort_order,
  is_active = true, is_available_for_purchase = false, billing_provider = 'polar',
  currency_code = 'USD', price_cents = 14900, metadata = excluded.metadata,
  updated_at = now();

insert into public.pricing_catalog (
  plan_key, label, plan_display_name, billing_period, tier_name, price_label,
  duration_minutes, feature_summary, is_active, sort_order
) values (
  'lifetime_pro', 'Lifetime', 'Pro Lifetime', 'lifetime', 'Pro',
  '$149 one-time launch price', 35,
  '["Pro features for life", "Unlimited video exports", "4K and batch queue"]'::jsonb,
  false, 1
) on conflict (plan_key) do update set
  label = excluded.label, plan_display_name = excluded.plan_display_name,
  billing_period = excluded.billing_period, price_label = excluded.price_label,
  duration_minutes = excluded.duration_minutes, feature_summary = excluded.feature_summary,
  is_active = false, sort_order = excluded.sort_order;

-- After creating Polar products, register BOTH the $149 launch and $1,499
-- standard product IDs here. Checkout uses plans.provider_product_id for the
-- currently public offer; the webhook accepts either registered product.
create table if not exists public.polar_lifetime_products (
  provider_product_id text primary key,
  plan_key text not null references public.plans(plan_key),
  offer_kind text not null check (offer_kind in ('launch', 'standard')),
  created_at timestamptz not null default now()
);

create table if not exists public.billing_lifetime_purchases (
  id uuid primary key default gen_random_uuid(),
  provider text not null default 'polar',
  provider_order_id text not null,
  auth_user_id uuid not null references auth.users(id) on delete cascade,
  user_profile_id uuid not null references public.user_profiles(id) on delete cascade,
  plan_key text not null references public.plans(plan_key),
  provider_product_id text not null references public.polar_lifetime_products(provider_product_id),
  provider_customer_id text,
  status text not null check (status in ('paid', 'refunded')),
  amount_cents integer not null check (amount_cents >= 0),
  currency_code text not null default 'USD',
  metadata jsonb not null default '{}'::jsonb,
  paid_at timestamptz not null default now(),
  refunded_at timestamptz,
  updated_at timestamptz not null default now(),
  unique (provider, provider_order_id)
);

alter table public.user_plan_assignments
  add column if not exists billing_purchase_id uuid;
alter table public.user_plan_assignments
  drop constraint if exists user_plan_assignments_billing_purchase_id_fkey;
alter table public.user_plan_assignments
  add constraint user_plan_assignments_billing_purchase_id_fkey
  foreign key (billing_purchase_id) references public.billing_lifetime_purchases(id) on delete set null;
create unique index if not exists idx_user_plan_assignments_billing_purchase
  on public.user_plan_assignments (billing_purchase_id)
  where billing_purchase_id is not null;

alter table public.polar_lifetime_products enable row level security;
alter table public.billing_lifetime_purchases enable row level security;
-- Both tables are service-role only. Customers learn their access through the
-- existing protected entitlement RPC, never from client-side order metadata.

create or replace function public.apply_polar_lifetime_order(
  p_auth_user_id uuid,
  p_email text,
  p_full_name text,
  p_provider_customer_id text,
  p_provider_order_id text,
  p_provider_product_id text,
  p_amount_cents integer,
  p_currency_code text,
  p_metadata jsonb default '{}'::jsonb
) returns jsonb
language plpgsql security definer set search_path = public, auth
as $$
declare
  v_plan_key text;
  v_email text;
  v_profile public.user_profiles%rowtype;
  v_purchase public.billing_lifetime_purchases%rowtype;
begin
  select lp.plan_key into v_plan_key
  from public.polar_lifetime_products lp
  join public.plans p on p.plan_key = lp.plan_key
  where lp.provider_product_id = p_provider_product_id
    and lp.plan_key = 'lifetime_pro' and p.is_active = true;
  if v_plan_key is null or p_auth_user_id is null or nullif(p_provider_order_id, '') is null then
    raise exception 'Unrecognized lifetime order or customer';
  end if;
  -- The authenticated account on the checkout is the access owner. Do not
  -- allow a mutable billing-address email to claim another account.
  select lower(trim(u.email)) into v_email
  from auth.users u where u.id = p_auth_user_id;
  if v_email is null or v_email = '' then raise exception 'Billing user email is missing'; end if;

  insert into public.user_profiles (auth_user_id, email, full_name, account_status)
  values (p_auth_user_id, v_email, nullif(trim(p_full_name), ''), 'active')
  on conflict (auth_user_id) do update set
    email = excluded.email,
    full_name = coalesce(excluded.full_name, public.user_profiles.full_name),
    account_status = case when public.user_profiles.account_status = 'suspended'
      then 'suspended' else 'active' end, updated_at = now();
  select * into v_profile from public.user_profiles where auth_user_id = p_auth_user_id;

  insert into public.billing_customers (
    auth_user_id, user_profile_id, provider, provider_customer_id, email
  ) values (
    p_auth_user_id, v_profile.id, 'polar', nullif(p_provider_customer_id, ''), v_email
  ) on conflict (auth_user_id) do update set
    user_profile_id = excluded.user_profile_id, provider = 'polar',
    provider_customer_id = coalesce(excluded.provider_customer_id, public.billing_customers.provider_customer_id),
    email = excluded.email, updated_at = now();

  insert into public.billing_lifetime_purchases (
    provider, provider_order_id, auth_user_id, user_profile_id, plan_key,
    provider_product_id, provider_customer_id, status, amount_cents,
    currency_code, metadata
  ) values (
    'polar', p_provider_order_id, p_auth_user_id, v_profile.id, v_plan_key,
    p_provider_product_id, nullif(p_provider_customer_id, ''), 'paid',
    greatest(coalesce(p_amount_cents, 0), 0), upper(coalesce(nullif(p_currency_code, ''), 'USD')),
    coalesce(p_metadata, '{}'::jsonb)
  ) on conflict (provider, provider_order_id) do update set
    metadata = excluded.metadata, updated_at = now()
  returning * into v_purchase;
  if v_purchase.auth_user_id <> p_auth_user_id or v_purchase.provider_product_id <> p_provider_product_id then
    raise exception 'Lifetime order identity or product changed';
  end if;
  if v_purchase.status = 'refunded' then
    return jsonb_build_object('order_id', p_provider_order_id, 'status', 'refunded');
  end if;

  insert into public.user_plan_assignments (
    user_profile_id, plan_key, status, requested_by_email, starts_at, ends_at,
    note, billing_purchase_id
  ) values (
    v_profile.id, v_plan_key, 'active', v_email, now(), null,
    'Polar paid lifetime order', v_purchase.id
  ) on conflict (billing_purchase_id) where billing_purchase_id is not null do update set
    status = 'active', ends_at = null, updated_at = now();
  perform public.sync_assignment_state_for_auth_user(p_auth_user_id);
  return jsonb_build_object('order_id', p_provider_order_id, 'status', 'paid', 'plan_key', v_plan_key);
end;
$$;

create or replace function public.refund_polar_lifetime_order(p_provider_order_id text)
returns jsonb language plpgsql security definer set search_path = public
as $$
declare
  v_purchase public.billing_lifetime_purchases%rowtype;
  v_subscription public.billing_subscriptions%rowtype;
begin
  update public.billing_lifetime_purchases
  set status = 'refunded', refunded_at = coalesce(refunded_at, now()), updated_at = now()
  where provider = 'polar' and provider_order_id = p_provider_order_id
  returning * into v_purchase;
  if v_purchase.id is null then raise exception 'Lifetime order not found'; end if;
  update public.user_plan_assignments set status = 'revoked', updated_at = now()
  where billing_purchase_id = v_purchase.id and status = 'active';

  -- A subscriber who also bought lifetime gets their still-paid recurring
  -- access back after a lifetime refund, without a manual support intervention.
  select * into v_subscription from public.billing_subscriptions
  where user_profile_id = v_purchase.user_profile_id and provider = 'polar'
    and status in ('active', 'on_trial')
    and (current_period_end is null or current_period_end > now())
  order by current_period_start desc nulls last limit 1;
  if v_subscription.id is not null then
    insert into public.user_plan_assignments (
      user_profile_id, plan_key, status, starts_at, ends_at, note, billing_subscription_id
    ) values (
      v_purchase.user_profile_id, v_subscription.plan_key, 'active',
      coalesce(v_subscription.current_period_start, now()), v_subscription.current_period_end,
      'Polar subscription restored after lifetime refund', v_subscription.id
    ) on conflict (billing_subscription_id) where billing_subscription_id is not null do update set
      status = 'active', starts_at = excluded.starts_at, ends_at = excluded.ends_at,
      note = excluded.note, updated_at = now();
  end if;
  perform public.sync_assignment_state_for_auth_user(v_purchase.auth_user_id);
  return jsonb_build_object('order_id', p_provider_order_id, 'status', 'refunded');
end;
$$;

revoke all on function public.apply_polar_lifetime_order(uuid, text, text, text, text, text, integer, text, jsonb) from public;
grant execute on function public.apply_polar_lifetime_order(uuid, text, text, text, text, text, integer, text, jsonb) to service_role;
revoke all on function public.refund_polar_lifetime_order(text) from public;
grant execute on function public.refund_polar_lifetime_order(text) to service_role;
notify pgrst, 'reload schema';
