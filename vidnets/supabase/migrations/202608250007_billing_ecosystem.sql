-- Vidzeo billing ecosystem
-- Lemon Squeezy secrets are never stored in the database or shipped to clients.

alter table if exists public.plans
    add column if not exists billing_provider text not null default 'lemon_squeezy',
    add column if not exists provider_product_id text,
    add column if not exists provider_variant_id text,
    add column if not exists checkout_url text,
    add column if not exists currency_code text not null default 'USD',
    add column if not exists price_cents integer,
    add column if not exists metadata jsonb not null default '{}'::jsonb,
    add column if not exists updated_at timestamptz not null default now();

alter table if exists public.user_plan_assignments
    add column if not exists billing_subscription_id uuid;

-- Keep the public website, desktop entitlement API, and checkout catalog on the
-- same six stable keys. Variant IDs are intentionally filled in separately
-- after the Lemon Squeezy products are created.
update public.plans set
    price_monthly_display = case plan_key
        when 'basic_monthly' then 19.00
        when 'plus_monthly' then 49.00
        when 'max_monthly' then 99.00
        when 'basic_yearly' then 15.00
        when 'plus_yearly' then 39.00
        when 'max_yearly' then 79.00
        else price_monthly_display
    end,
    price_label = case plan_key
        when 'basic_monthly' then '$19 / Month'
        when 'plus_monthly' then '$49 / Month'
        when 'max_monthly' then '$99 / Month'
        when 'basic_yearly' then '$15 / Month (Yearly)'
        when 'plus_yearly' then '$39 / Month (Yearly)'
        when 'max_yearly' then '$79 / Month (Yearly)'
        else price_label
    end,
    is_active = true
where plan_key in (
    'basic_monthly', 'plus_monthly', 'max_monthly',
    'basic_yearly', 'plus_yearly', 'max_yearly'
);

update public.pricing_catalog set
    price_label = case plan_key
        when 'basic_monthly' then '$19 / Month'
        when 'plus_monthly' then '$49 / Month'
        when 'max_monthly' then '$99 / Month'
        when 'basic_yearly' then '$15 / Month (Yearly)'
        when 'plus_yearly' then '$39 / Month (Yearly)'
        when 'max_yearly' then '$79 / Month (Yearly)'
        else price_label
    end,
    is_active = true
where plan_key in (
    'basic_monthly', 'plus_monthly', 'max_monthly',
    'basic_yearly', 'plus_yearly', 'max_yearly'
);

create table if not exists public.billing_customers (
    id uuid primary key default gen_random_uuid(),
    auth_user_id uuid not null unique references auth.users(id) on delete cascade,
    user_profile_id uuid references public.user_profiles(id) on delete set null,
    provider text not null default 'lemon_squeezy',
    provider_customer_id text,
    email text not null,
    metadata jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (provider, provider_customer_id)
);

create table if not exists public.billing_subscriptions (
    id uuid primary key default gen_random_uuid(),
    auth_user_id uuid not null references auth.users(id) on delete cascade,
    user_profile_id uuid references public.user_profiles(id) on delete set null,
    plan_key text references public.plans(plan_key),
    provider text not null default 'lemon_squeezy',
    provider_subscription_id text not null,
    provider_customer_id text,
    status text not null default 'pending' check (status in ('pending', 'active', 'on_trial', 'paused', 'past_due', 'unpaid', 'cancelled', 'expired')),
    current_period_start timestamptz,
    current_period_end timestamptz,
    cancel_at_period_end boolean not null default false,
    metadata jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (provider, provider_subscription_id)
);

alter table if exists public.user_plan_assignments
    drop constraint if exists user_plan_assignments_billing_subscription_id_fkey;
alter table if exists public.user_plan_assignments
    add constraint user_plan_assignments_billing_subscription_id_fkey
    foreign key (billing_subscription_id) references public.billing_subscriptions(id) on delete set null;
create unique index if not exists idx_user_plan_assignments_billing_subscription
    on public.user_plan_assignments (billing_subscription_id)
    where billing_subscription_id is not null;

create table if not exists public.billing_events (
    id uuid primary key default gen_random_uuid(),
    provider text not null default 'lemon_squeezy',
    provider_event_id text not null,
    event_name text not null,
    signature_verified boolean not null default false,
    processed boolean not null default false,
    payload jsonb not null default '{}'::jsonb,
    error_message text,
    created_at timestamptz not null default now(),
    processed_at timestamptz,
    unique (provider, provider_event_id)
);

create index if not exists idx_billing_subscriptions_auth_user on public.billing_subscriptions(auth_user_id);
create index if not exists idx_billing_subscriptions_status on public.billing_subscriptions(status);
create index if not exists idx_billing_events_created_at on public.billing_events(created_at desc);

drop trigger if exists trg_plans_updated_at on public.plans;
create trigger trg_plans_updated_at
before update on public.plans
for each row execute function public.set_updated_at();
drop trigger if exists trg_billing_customers_updated_at on public.billing_customers;
create trigger trg_billing_customers_updated_at
before update on public.billing_customers
for each row execute function public.set_updated_at();
drop trigger if exists trg_billing_subscriptions_updated_at on public.billing_subscriptions;
create trigger trg_billing_subscriptions_updated_at
before update on public.billing_subscriptions
for each row execute function public.set_updated_at();

alter table public.billing_customers enable row level security;
alter table public.billing_subscriptions enable row level security;
alter table public.billing_events enable row level security;

drop policy if exists "Users can read own billing customer" on public.billing_customers;
create policy "Users can read own billing customer"
on public.billing_customers for select to authenticated
using (auth.uid() = auth_user_id);
drop policy if exists "Users can read own billing subscriptions" on public.billing_subscriptions;
create policy "Users can read own billing subscriptions"
on public.billing_subscriptions for select to authenticated
using (auth.uid() = auth_user_id);
drop policy if exists "Service role full billing customers" on public.billing_customers;
create policy "Service role full billing customers"
on public.billing_customers for all using ((auth.jwt() ->> 'role') = 'service_role')
with check ((auth.jwt() ->> 'role') = 'service_role');
drop policy if exists "Service role full billing subscriptions" on public.billing_subscriptions;
create policy "Service role full billing subscriptions"
on public.billing_subscriptions for all using ((auth.jwt() ->> 'role') = 'service_role')
with check ((auth.jwt() ->> 'role') = 'service_role');
drop policy if exists "Service role full billing events" on public.billing_events;
create policy "Service role full billing events"
on public.billing_events for all using ((auth.jwt() ->> 'role') = 'service_role')
with check ((auth.jwt() ->> 'role') = 'service_role');

create or replace function public.apply_lemon_subscription(
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
    if not exists (select 1 from public.plans where plan_key = p_plan_key and is_active = true) then
        raise exception 'Billing plan is not active: %', p_plan_key;
    end if;

    select coalesce(nullif(lower(trim(p_email)), ''), lower(trim(u.email)))
      into v_email
      from auth.users u
     where u.id = p_auth_user_id;
    if v_email is null or v_email = '' then
        raise exception 'Billing user email is missing';
    end if;

    insert into public.user_profiles (auth_user_id, email, full_name, account_status)
    values (p_auth_user_id, v_email, nullif(trim(p_full_name), ''), 'active')
    on conflict (auth_user_id) do update set
        email = coalesce(nullif(excluded.email, ''), public.user_profiles.email),
        full_name = coalesce(nullif(excluded.full_name, ''), public.user_profiles.full_name),
        updated_at = now();
    select * into v_profile from public.user_profiles where auth_user_id = p_auth_user_id;

    insert into public.billing_customers (auth_user_id, user_profile_id, provider, provider_customer_id, email)
    values (p_auth_user_id, v_profile.id, 'lemon_squeezy', nullif(p_provider_customer_id, ''), v_email)
    on conflict (auth_user_id) do update set
        user_profile_id = excluded.user_profile_id,
        provider_customer_id = coalesce(excluded.provider_customer_id, public.billing_customers.provider_customer_id),
        email = coalesce(nullif(excluded.email, ''), public.billing_customers.email),
        updated_at = now();

    insert into public.billing_subscriptions (
        auth_user_id, user_profile_id, plan_key, provider, provider_subscription_id,
        provider_customer_id, status, current_period_start, current_period_end,
        cancel_at_period_end, metadata
    ) values (
        p_auth_user_id, v_profile.id, p_plan_key, 'lemon_squeezy', p_provider_subscription_id,
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
        'Lemon Squeezy subscription sync', v_subscription.id
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

revoke all on function public.apply_lemon_subscription(uuid, text, text, text, text, text, text, timestamptz, timestamptz, boolean, jsonb) from public;
grant execute on function public.apply_lemon_subscription(uuid, text, text, text, text, text, text, timestamptz, timestamptz, boolean, jsonb) to service_role;

notify pgrst, 'reload schema';
