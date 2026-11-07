create extension if not exists pgcrypto;

create or replace function public.set_updated_at()
returns trigger
language plpgsql
as $$
begin
    new.updated_at = now();
    return new;
end;
$$;

create table if not exists public.plans (
    plan_key text primary key,
    name text not null,
    display_name text not null,
    billing_period text not null check (billing_period in ('monthly', 'yearly')),
    price_monthly_display numeric(10, 2) not null default 0,
    price_label text not null default '',
    video_limit_minutes integer,
    resolution_label text not null default '1080p Exports',
    unlimited_videos boolean not null default true,
    unlimited_voiceovers boolean not null default true,
    media_sources_label text not null default 'AI + Stock + Real Media',
    feature_summary jsonb not null default '[]'::jsonb,
    sort_order integer not null default 0,
    is_active boolean not null default true,
    created_at timestamptz not null default now()
);

create table if not exists public.user_profiles (
    id uuid primary key default gen_random_uuid(),
    auth_user_id uuid unique,
    email text not null unique,
    full_name text,
    avatar_url text,
    account_status text not null default 'pending' check (account_status in ('pending', 'active', 'suspended')),
    last_login_at timestamptz,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table if not exists public.user_plan_assignments (
    id uuid primary key default gen_random_uuid(),
    user_profile_id uuid not null references public.user_profiles(id) on delete cascade,
    plan_key text not null references public.plans(plan_key),
    status text not null default 'pending' check (status in ('pending', 'active', 'expired', 'revoked')),
    requested_by_email text,
    assigned_by_user_id uuid,
    assigned_by_email text,
    starts_at timestamptz,
    ends_at timestamptz,
    note text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

drop trigger if exists trg_user_profiles_updated_at on public.user_profiles;
create trigger trg_user_profiles_updated_at
before update on public.user_profiles
for each row
execute function public.set_updated_at();

drop trigger if exists trg_user_plan_assignments_updated_at on public.user_plan_assignments;
create trigger trg_user_plan_assignments_updated_at
before update on public.user_plan_assignments
for each row
execute function public.set_updated_at();

create index if not exists idx_user_profiles_auth_user_id on public.user_profiles(auth_user_id);
create index if not exists idx_user_profiles_account_status on public.user_profiles(account_status);
create index if not exists idx_user_plan_assignments_user_profile_id on public.user_plan_assignments(user_profile_id);
create index if not exists idx_user_plan_assignments_status on public.user_plan_assignments(status);

insert into public.plans (
    plan_key,
    name,
    display_name,
    billing_period,
    price_monthly_display,
    price_label,
    video_limit_minutes,
    feature_summary,
    sort_order,
    is_active
)
values
    (
        'basic_monthly',
        'Basic',
        'Basic Monthly',
        'monthly',
        19.00,
        '$19 / Month',
        15,
        '["Videos up to 15 minutes long","Unlimited videos","1080p exports","Unlimited free voiceovers","AI + Stock + Real Media"]'::jsonb,
        1,
        true
    ),
    (
        'plus_monthly',
        'Plus',
        'Plus Monthly',
        'monthly',
        35.00,
        '$35 / Month',
        30,
        '["Videos up to 30 minutes","Unlimited videos","1080p exports","Unlimited free voiceovers","AI + Stock + Real Media"]'::jsonb,
        2,
        true
    ),
    (
        'max_monthly',
        'Max',
        'Max Monthly',
        'monthly',
        49.00,
        '$49 / Month',
        60,
        '["Videos up to or above 1 hour","Unlimited videos","1080p exports","Unlimited free voiceovers","AI + Stock + Real Media"]'::jsonb,
        3,
        true
    ),
    (
        'basic_yearly',
        'Basic',
        'Basic Yearly',
        'yearly',
        15.00,
        '$15 / Month (Yearly)',
        15,
        '["Videos up to 15 minutes long","Unlimited videos","1080p exports","Unlimited free voiceovers","AI + Stock + Real Media"]'::jsonb,
        4,
        true
    ),
    (
        'plus_yearly',
        'Plus',
        'Plus Yearly',
        'yearly',
        29.00,
        '$29 / Month (Yearly)',
        30,
        '["Videos up to 30 minutes","Unlimited videos","1080p exports","Unlimited free voiceovers","AI + Stock + Real Media"]'::jsonb,
        5,
        true
    ),
    (
        'max_yearly',
        'Max',
        'Max Yearly',
        'yearly',
        43.00,
        '$43 / Month (Yearly)',
        60,
        '["Videos up to or above 1 hour","Unlimited videos","1080p exports","Unlimited free voiceovers","AI + Stock + Real Media"]'::jsonb,
        6,
        true
    )
on conflict (plan_key) do update
set
    name = excluded.name,
    display_name = excluded.display_name,
    billing_period = excluded.billing_period,
    price_monthly_display = excluded.price_monthly_display,
    price_label = excluded.price_label,
    video_limit_minutes = excluded.video_limit_minutes,
    feature_summary = excluded.feature_summary,
    sort_order = excluded.sort_order,
    is_active = excluded.is_active;

create table if not exists public.profiles (
    id uuid primary key references auth.users(id) on delete cascade,
    email text unique,
    name text,
    avatar_url text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

drop trigger if exists trg_profiles_updated_at on public.profiles;
create trigger trg_profiles_updated_at
before update on public.profiles
for each row
execute function public.set_updated_at();

drop trigger if exists guard_profile_plan_fields on public.profiles;

alter table public.profiles
    add column if not exists plan text default 'free',
    add column if not exists status text default 'free',
    add column if not exists payment_ref text,
    add column if not exists requested_plan text default '',
    add column if not exists expiry_date timestamptz,
    add column if not exists activation_date timestamptz,
    add column if not exists free_chars_limit integer not null default 3000,
    add column if not exists free_chars_used integer not null default 0,
    add column if not exists device_fingerprint text,
    add column if not exists last_login_ip text,
    add column if not exists user_agent text,
    add column if not exists device_info jsonb default '{}'::jsonb,
    add column if not exists metadata jsonb default '{}'::jsonb,
    add column if not exists last_login_at timestamptz,
    add column if not exists total_logins integer default 0;

alter table public.profiles
    alter column plan set default 'free',
    alter column status set default 'free',
    alter column requested_plan set default '',
    alter column free_chars_limit set default 3000,
    alter column free_chars_used set default 0,
    alter column device_info set default '{}'::jsonb,
    alter column metadata set default '{}'::jsonb;

create index if not exists idx_profiles_email on public.profiles(email);
create index if not exists idx_profiles_device_fingerprint on public.profiles(device_fingerprint);

insert into public.profiles (id, email, name, avatar_url)
select
    u.id,
    lower(coalesce(u.email, '')),
    coalesce(u.raw_user_meta_data ->> 'full_name', u.raw_user_meta_data ->> 'name', split_part(coalesce(u.email, ''), '@', 1)),
    coalesce(u.raw_user_meta_data ->> 'avatar_url', u.raw_user_meta_data ->> 'picture', '')
from auth.users u
on conflict (id) do update
set
    email = excluded.email,
    name = excluded.name,
    avatar_url = excluded.avatar_url,
    updated_at = now();

do $$
begin
    perform set_config('app.bypass_profile_guard', '1', true);
    update public.profiles
    set
        plan = coalesce(nullif(plan, ''), 'free'),
        status = case
            when coalesce(nullif(plan, ''), 'free') = 'free' and coalesce(nullif(status, ''), '') = '' then 'free'
            else coalesce(nullif(status, ''), 'free')
        end,
        requested_plan = coalesce(requested_plan, ''),
        free_chars_limit = greatest(coalesce(free_chars_limit, 3000), 0),
        free_chars_used = greatest(coalesce(free_chars_used, 0), 0),
        device_fingerprint = nullif(lower(trim(coalesce(device_fingerprint, ''))), ''),
        device_info = coalesce(device_info, '{}'::jsonb),
        metadata = coalesce(metadata, '{}'::jsonb),
        total_logins = greatest(coalesce(total_logins, 0), 0),
        updated_at = now();
end
$$;

create table if not exists public.pricing_catalog (
    plan_key text primary key,
    label text not null,
    plan_display_name text not null,
    billing_period text not null check (billing_period in ('monthly', 'yearly')),
    tier_name text not null,
    price_label text not null,
    duration_minutes integer,
    feature_summary jsonb not null default '[]'::jsonb,
    is_active boolean not null default true,
    sort_order integer not null default 0
);

insert into public.pricing_catalog (
    plan_key,
    label,
    plan_display_name,
    billing_period,
    tier_name,
    price_label,
    duration_minutes,
    feature_summary,
    is_active,
    sort_order
)
values
    (
        'basic_monthly',
        'Monthly',
        'Basic Monthly',
        'monthly',
        'Basic',
        '$19 / Month',
        15,
        '["Videos up to 15 minutes long","Unlimited videos","1080p exports","Unlimited free voiceovers","AI + Stock + Real Media"]'::jsonb,
        true,
        10
    ),
    (
        'plus_monthly',
        'Monthly',
        'Plus Monthly',
        'monthly',
        'Plus',
        '$35 / Month',
        30,
        '["Videos up to 30 minutes","Unlimited videos","1080p exports","Unlimited free voiceovers","AI + Stock + Real Media"]'::jsonb,
        true,
        20
    ),
    (
        'max_monthly',
        'Monthly',
        'Max Monthly',
        'monthly',
        'Max',
        '$49 / Month',
        60,
        '["Videos up to or above 1 hour","Unlimited videos","1080p exports","Unlimited free voiceovers","AI + Stock + Real Media"]'::jsonb,
        true,
        30
    ),
    (
        'basic_yearly',
        'Yearly',
        'Basic Yearly',
        'yearly',
        'Basic',
        '$15 / Month',
        15,
        '["Videos up to 15 minutes long","Unlimited videos","1080p exports","Unlimited free voiceovers","AI + Stock + Real Media"]'::jsonb,
        true,
        40
    ),
    (
        'plus_yearly',
        'Yearly',
        'Plus Yearly',
        'yearly',
        'Plus',
        '$29 / Month',
        30,
        '["Videos up to 30 minutes","Unlimited videos","1080p exports","Unlimited free voiceovers","AI + Stock + Real Media"]'::jsonb,
        true,
        50
    ),
    (
        'max_yearly',
        'Yearly',
        'Max Yearly',
        'yearly',
        'Max',
        '$43 / Month',
        60,
        '["Videos up to or above 1 hour","Unlimited videos","1080p exports","Unlimited free voiceovers","AI + Stock + Real Media"]'::jsonb,
        true,
        60
    )
on conflict (plan_key) do update
set
    label = excluded.label,
    plan_display_name = excluded.plan_display_name,
    billing_period = excluded.billing_period,
    tier_name = excluded.tier_name,
    price_label = excluded.price_label,
    duration_minutes = excluded.duration_minutes,
    feature_summary = excluded.feature_summary,
    is_active = excluded.is_active,
    sort_order = excluded.sort_order;

alter table public.pricing_catalog enable row level security;
alter table public.profiles enable row level security;

drop policy if exists "Authenticated can read pricing" on public.pricing_catalog;
create policy "Authenticated can read pricing"
on public.pricing_catalog
for select
to authenticated
using (true);

drop policy if exists "Admin full access pricing" on public.pricing_catalog;
create policy "Admin full access pricing"
on public.pricing_catalog
for all
using ((auth.jwt() ->> 'role') = 'service_role')
with check ((auth.jwt() ->> 'role') = 'service_role');

drop policy if exists "Users can view own profile guard table" on public.profiles;
create policy "Users can view own profile guard table"
on public.profiles
for select
to authenticated
using (auth.uid() = id);

drop policy if exists "Admin full access profiles" on public.profiles;
create policy "Admin full access profiles"
on public.profiles
for all
using ((auth.jwt() ->> 'role') = 'service_role')
with check ((auth.jwt() ->> 'role') = 'service_role');

create or replace function public.guard_profile_plan_fields()
returns trigger
language plpgsql
set search_path = public
as $$
begin
    if current_setting('app.bypass_profile_guard', true) = '1'
       or (auth.jwt() ->> 'role') = 'service_role' then
        return new;
    end if;

    if new.plan is distinct from old.plan
        or new.status is distinct from old.status
        or new.expiry_date is distinct from old.expiry_date
        or new.activation_date is distinct from old.activation_date
        or new.payment_ref is distinct from old.payment_ref
        or new.requested_plan is distinct from old.requested_plan
        or new.free_chars_limit is distinct from old.free_chars_limit
        or new.free_chars_used is distinct from old.free_chars_used
        or new.device_fingerprint is distinct from old.device_fingerprint
        or new.last_login_ip is distinct from old.last_login_ip
        or new.user_agent is distinct from old.user_agent
        or new.device_info is distinct from old.device_info
        or new.last_login_at is distinct from old.last_login_at
        or new.total_logins is distinct from old.total_logins then
        raise exception 'Direct updates to protected security fields are not allowed';
    end if;

    return new;
end;
$$;

drop trigger if exists guard_profile_plan_fields on public.profiles;
create trigger guard_profile_plan_fields
before update on public.profiles
for each row
execute function public.guard_profile_plan_fields();

create or replace function public.is_vidzeo_paid_plan(p_plan_key text)
returns boolean
language sql
immutable
as $$
    select lower(coalesce(p_plan_key, '')) in (
        'basic_monthly',
        'plus_monthly',
        'max_monthly',
        'basic_yearly',
        'plus_yearly',
        'max_yearly'
    );
$$;

create or replace function public.sync_auth_user_to_profile()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
    perform set_config('app.bypass_profile_guard', '1', true);
    insert into public.profiles (id, email, name, avatar_url)
    values (
        new.id,
        lower(coalesce(new.email, '')),
        coalesce(new.raw_user_meta_data ->> 'full_name', new.raw_user_meta_data ->> 'name', split_part(coalesce(new.email, ''), '@', 1)),
        coalesce(new.raw_user_meta_data ->> 'avatar_url', new.raw_user_meta_data ->> 'picture', '')
    )
    on conflict (id) do update
    set
        email = excluded.email,
        name = excluded.name,
        avatar_url = excluded.avatar_url,
        updated_at = now();
    return new;
end;
$$;

drop trigger if exists on_auth_user_created_sync_vidzeo_profile on auth.users;
create trigger on_auth_user_created_sync_vidzeo_profile
after insert or update on auth.users
for each row
execute function public.sync_auth_user_to_profile();

create or replace function public.sync_assignment_state_for_auth_user(p_auth_user_id uuid)
returns void
language plpgsql
security definer
set search_path = public
as $$
declare
    v_user_profile_id uuid;
    v_user_profile_email text;
    v_user_profile_name text;
    v_user_profile_avatar_url text;
    v_user_account_status text;
    v_active_assignment_id uuid;
    v_active_plan_key text;
    v_active_starts_at timestamptz;
    v_active_created_at timestamptz;
    v_active_ends_at timestamptz;
    v_pending_assignment_id uuid;
    v_pending_plan_key text;
    v_next_status text := 'free';
    v_next_plan text := 'free';
    v_next_requested_plan text := '';
    v_next_activation timestamptz := null;
    v_next_expiry timestamptz := null;
begin
    if p_auth_user_id is null then
        return;
    end if;

    select id, email, full_name, avatar_url, account_status
    into v_user_profile_id, v_user_profile_email, v_user_profile_name, v_user_profile_avatar_url, v_user_account_status
    from public.user_profiles
    where auth_user_id = p_auth_user_id
    limit 1;

    if not found then
        return;
    end if;

    select id, plan_key, starts_at, created_at, ends_at
    into v_active_assignment_id, v_active_plan_key, v_active_starts_at, v_active_created_at, v_active_ends_at
    from public.user_plan_assignments
    where user_profile_id = v_user_profile_id
      and status = 'active'
    order by coalesce(starts_at, created_at) desc, created_at desc
    limit 1;

    select id, plan_key
    into v_pending_assignment_id, v_pending_plan_key
    from public.user_plan_assignments
    where user_profile_id = v_user_profile_id
      and status = 'pending'
    order by created_at desc
    limit 1;

    if lower(coalesce(v_user_account_status, 'pending')) = 'suspended' then
        v_next_status := 'suspended';
    elsif v_active_assignment_id is not null then
        v_next_status := 'active';
    elsif v_pending_assignment_id is not null then
        v_next_status := 'pending';
    else
        v_next_status := 'free';
    end if;

    if v_active_assignment_id is not null then
        v_next_plan := v_active_plan_key;
        v_next_activation := coalesce(v_active_starts_at, v_active_created_at);
        v_next_expiry := v_active_ends_at;
    end if;

    if v_pending_assignment_id is not null then
        v_next_requested_plan := v_pending_plan_key;
    end if;

    perform set_config('app.bypass_profile_guard', '1', true);
    insert into public.profiles (id, email, name, avatar_url)
    values (
        p_auth_user_id,
        lower(coalesce(v_user_profile_email, '')),
        coalesce(v_user_profile_name, split_part(coalesce(v_user_profile_email, ''), '@', 1)),
        coalesce(v_user_profile_avatar_url, '')
    )
    on conflict (id) do update
    set
        email = excluded.email,
        name = excluded.name,
        avatar_url = excluded.avatar_url,
        plan = v_next_plan,
        status = v_next_status,
        requested_plan = v_next_requested_plan,
        activation_date = v_next_activation,
        expiry_date = v_next_expiry,
        updated_at = now();
end;
$$;

create or replace function public.sync_user_profile_to_security_profile()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
    if new.auth_user_id is null then
        return new;
    end if;

    perform set_config('app.bypass_profile_guard', '1', true);
    insert into public.profiles (id, email, name, avatar_url)
    values (
        new.auth_user_id,
        lower(coalesce(new.email, '')),
        coalesce(new.full_name, split_part(coalesce(new.email, ''), '@', 1)),
        coalesce(new.avatar_url, '')
    )
    on conflict (id) do update
    set
        email = excluded.email,
        name = excluded.name,
        avatar_url = excluded.avatar_url,
        updated_at = now();

    perform public.sync_assignment_state_for_auth_user(new.auth_user_id);
    return new;
end;
$$;

drop trigger if exists trg_sync_user_profile_to_security_profile on public.user_profiles;
create trigger trg_sync_user_profile_to_security_profile
after insert or update on public.user_profiles
for each row
execute function public.sync_user_profile_to_security_profile();

create or replace function public.sync_plan_assignment_to_security_profile()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
declare
    v_user_profile_id uuid;
    v_auth_user_id uuid;
begin
    v_user_profile_id := coalesce(new.user_profile_id, old.user_profile_id);
    if v_user_profile_id is null then
        return coalesce(new, old);
    end if;

    select auth_user_id
    into v_auth_user_id
    from public.user_profiles
    where id = v_user_profile_id
    limit 1;

    if v_auth_user_id is not null then
        perform public.sync_assignment_state_for_auth_user(v_auth_user_id);
    end if;

    if tg_op = 'DELETE' then
        return old;
    end if;
    return new;
end;
$$;

drop trigger if exists trg_sync_plan_assignment_to_security_profile on public.user_plan_assignments;
create trigger trg_sync_plan_assignment_to_security_profile
after insert or update or delete on public.user_plan_assignments
for each row
execute function public.sync_plan_assignment_to_security_profile();

do $$
declare
    rec record;
begin
    for rec in
        select auth_user_id
        from public.user_profiles
        where auth_user_id is not null
    loop
        perform public.sync_assignment_state_for_auth_user(rec.auth_user_id);
    end loop;
end;
$$;

create table if not exists public.generation_quota_reservations (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references auth.users(id) on delete cascade,
    chars integer not null check (chars > 0),
    plan_at_reservation text not null,
    status text not null default 'reserved' check (status in ('reserved', 'consumed', 'released', 'expired')),
    created_at timestamptz not null default now(),
    expires_at timestamptz not null default (now() + interval '15 minutes'),
    finalized_at timestamptz,
    metadata jsonb not null default '{}'::jsonb
);

create index if not exists idx_quota_reservations_user_status_expires
    on public.generation_quota_reservations (user_id, status, expires_at);

alter table public.generation_quota_reservations enable row level security;

drop policy if exists "Users can view own quota reservations" on public.generation_quota_reservations;
create policy "Users can view own quota reservations"
on public.generation_quota_reservations
for select
to authenticated
using (auth.uid() = user_id);

drop policy if exists "Admin full access to quota reservations" on public.generation_quota_reservations;
create policy "Admin full access to quota reservations"
on public.generation_quota_reservations
for all
using ((auth.jwt() ->> 'role') = 'service_role')
with check ((auth.jwt() ->> 'role') = 'service_role');

drop function if exists public.request_plan_upgrade(text);
drop function if exists public.request_plan_upgrade(text, text);
drop function if exists public.reconcile_expired_quota_reservations(uuid);
drop function if exists public.reserve_generation_quota(integer);
drop function if exists public.reserve_generation_quota(integer, text);
drop function if exists public.finalize_generation_quota(uuid, boolean);
drop function if exists public.finalize_generation_quota(uuid, boolean, text);
drop function if exists public.get_plan_state();
drop function if exists public.get_plan_state(text);
drop function if exists public.consume_generation_quota(integer);
drop function if exists public.consume_generation_quota(integer, text);
drop function if exists public.sync_device_telemetry(text, jsonb, text, text);
drop function if exists public.assert_device_lock(uuid, text);
drop function if exists public.admin_reset_device_binding(uuid, text);

create function public.assert_device_lock(p_uid uuid, p_device_fingerprint text)
returns text
language plpgsql
security definer
set search_path = public
as $$
declare
    v_profile record;
    v_email text;
    v_name text;
    v_avatar_url text;
    v_fp text := lower(trim(coalesce(p_device_fingerprint, '')));
    v_stored_fp text;
begin
    if p_uid is null then
        raise exception 'NOT_AUTHENTICATED';
    end if;

    if v_fp = '' then
        raise exception 'DEVICE_FINGERPRINT_REQUIRED';
    end if;

    select
        u.email,
        coalesce(u.raw_user_meta_data ->> 'full_name', u.raw_user_meta_data ->> 'name', 'Creator'),
        coalesce(u.raw_user_meta_data ->> 'avatar_url', u.raw_user_meta_data ->> 'picture', '')
    into v_email, v_name, v_avatar_url
    from auth.users u
    where u.id = p_uid;

    if v_email is null then
        raise exception 'PROFILE_NOT_FOUND';
    end if;

    select *
    into v_profile
    from public.profiles
    where id = p_uid
    for update;

    if not found then
        perform set_config('app.bypass_profile_guard', '1', true);
        insert into public.profiles (
            id, email, name, avatar_url, plan, status, requested_plan,
            free_chars_limit, free_chars_used, device_fingerprint, updated_at
        )
        values (
            p_uid, lower(v_email), v_name, v_avatar_url, 'free', 'free', '',
            3000, 0, v_fp, now()
        )
        on conflict (id) do nothing;

        select *
        into v_profile
        from public.profiles
        where id = p_uid
        for update;
    end if;

    if not found then
        raise exception 'PROFILE_NOT_FOUND';
    end if;

    if coalesce(v_profile.email, '') <> lower(v_email)
        or trim(coalesce(v_profile.name, '')) = ''
        or coalesce(v_profile.avatar_url, '') <> coalesce(v_avatar_url, '') then
        perform set_config('app.bypass_profile_guard', '1', true);
        update public.profiles
        set
            email = lower(v_email),
            name = coalesce(nullif(v_profile.name, ''), v_name),
            avatar_url = coalesce(nullif(v_avatar_url, ''), v_profile.avatar_url),
            updated_at = now()
        where id = p_uid
        returning *
        into v_profile;
    end if;

    v_stored_fp := lower(trim(coalesce(v_profile.device_fingerprint, '')));

    if v_stored_fp = '' then
        perform set_config('app.bypass_profile_guard', '1', true);
        update public.profiles
        set
            device_fingerprint = v_fp,
            updated_at = now()
        where id = p_uid;
        return v_fp;
    end if;

    if v_stored_fp <> v_fp then
        raise exception 'DEVICE_MISMATCH';
    end if;

    return v_fp;
end;
$$;

create function public.sync_device_telemetry(
    p_device_fingerprint text,
    p_device_info jsonb default '{}'::jsonb,
    p_last_login_ip text default null,
    p_user_agent text default null
)
returns table(
    plan text,
    status text,
    free_chars_used integer,
    free_chars_limit integer,
    device_fingerprint text
)
language plpgsql
security definer
set search_path = public
as $$
declare
    v_uid uuid := auth.uid();
    v_fp text;
begin
    if v_uid is null then
        raise exception 'NOT_AUTHENTICATED';
    end if;

    v_fp := public.assert_device_lock(v_uid, p_device_fingerprint);

    perform set_config('app.bypass_profile_guard', '1', true);
    update public.profiles
    set
        last_login_ip = coalesce(nullif(p_last_login_ip, ''), last_login_ip),
        user_agent = coalesce(nullif(p_user_agent, ''), user_agent),
        device_info = coalesce(p_device_info, '{}'::jsonb),
        last_login_at = now(),
        total_logins = coalesce(total_logins, 0) + 1,
        updated_at = now()
    where id = v_uid;

    return query
    select
        coalesce(p.plan, 'free'),
        coalesce(p.status, 'free'),
        greatest(coalesce(p.free_chars_used, 0), 0),
        greatest(coalesce(p.free_chars_limit, 3000), 0),
        coalesce(p.device_fingerprint, v_fp)
    from public.profiles p
    where p.id = v_uid;
end;
$$;

create function public.reconcile_expired_quota_reservations(p_user_id uuid)
returns table(expired_count integer)
language plpgsql
security definer
set search_path = public
as $$
declare
    v_free_refund integer := 0;
begin
    if p_user_id is null then
        return query select 0::integer;
        return;
    end if;

    with expired as (
        select id, chars, plan_at_reservation
        from public.generation_quota_reservations
        where user_id = p_user_id
          and status = 'reserved'
          and expires_at <= now()
        for update
    )
    select coalesce(sum(case when plan_at_reservation = 'free' then chars else 0 end), 0)
    into v_free_refund
    from expired;

    if v_free_refund > 0 then
        perform set_config('app.bypass_profile_guard', '1', true);
        update public.profiles
        set
            free_chars_used = greatest(coalesce(free_chars_used, 0) - v_free_refund, 0),
            updated_at = now()
        where id = p_user_id;
    end if;

    update public.generation_quota_reservations
    set
        status = 'expired',
        finalized_at = now()
    where user_id = p_user_id
      and status = 'reserved'
      and expires_at <= now();

    return query
    select count(*)::integer
    from public.generation_quota_reservations
    where user_id = p_user_id
      and status = 'expired'
      and finalized_at >= now() - interval '5 seconds';
end;
$$;

create function public.request_plan_upgrade(
    p_plan_key text,
    p_device_fingerprint text default null
)
returns table(requested_plan text, status text)
language plpgsql
security definer
set search_path = public
as $$
declare
    v_uid uuid := auth.uid();
    v_plan_key text := lower(trim(coalesce(p_plan_key, '')));
    v_profile record;
begin
    if v_uid is null then
        raise exception 'NOT_AUTHENTICATED';
    end if;

    perform public.assert_device_lock(v_uid, p_device_fingerprint);

    if not public.is_vidzeo_paid_plan(v_plan_key) then
        raise exception 'Invalid plan key: %', v_plan_key;
    end if;

    if not exists (
        select 1
        from public.pricing_catalog
        where plan_key = v_plan_key
          and coalesce(is_active, true) = true
    ) then
        raise exception 'Selected plan is not available right now';
    end if;

    select *
    into v_profile
    from public.profiles
    where id = v_uid
    for update;

    if not found then
        raise exception 'PROFILE_NOT_FOUND';
    end if;

    if lower(coalesce(v_profile.status, '')) = 'active'
       and lower(coalesce(v_profile.plan, '')) = v_plan_key
       and (v_profile.expiry_date is null or v_profile.expiry_date > now()) then
        raise exception 'You are already on this plan';
    end if;

    perform set_config('app.bypass_profile_guard', '1', true);
    update public.profiles
    set
        requested_plan = v_plan_key,
        status = case when lower(coalesce(status, 'free')) = 'active' then status else 'pending' end,
        updated_at = now()
    where id = v_uid
    returning profiles.requested_plan, profiles.status
    into requested_plan, status;

    return next;
end;
$$;

create function public.reserve_generation_quota(
    p_chars integer,
    p_device_fingerprint text default null
)
returns table(
    allowed boolean,
    reason text,
    current_plan text,
    reservation_id uuid,
    remaining_chars integer,
    free_chars_used integer,
    free_chars_limit integer,
    "requires_upgrade" boolean
)
language plpgsql
security definer
set search_path = public
as $$
declare
    v_uid uuid := auth.uid();
    v_profile record;
    v_limit integer;
    v_used integer;
    v_plan text;
    v_status text;
    v_reservation_id uuid;
begin
    if v_uid is null then
        raise exception 'NOT_AUTHENTICATED';
    end if;

    perform public.assert_device_lock(v_uid, p_device_fingerprint);

    if p_chars is null or p_chars <= 0 then
        raise exception 'Requested characters must be greater than zero';
    end if;

    perform public.reconcile_expired_quota_reservations(v_uid);

    select *
    into v_profile
    from public.profiles
    where id = v_uid
    for update;

    if not found then
        raise exception 'PROFILE_NOT_FOUND';
    end if;

    v_plan := lower(coalesce(v_profile.plan, 'free'));
    v_status := lower(coalesce(v_profile.status, 'free'));
    v_limit := greatest(coalesce(v_profile.free_chars_limit, 3000), 0);
    v_used := greatest(coalesce(v_profile.free_chars_used, 0), 0);

    if v_status = 'active' and public.is_vidzeo_paid_plan(v_plan) then
        if v_profile.expiry_date is null or v_profile.expiry_date > now() then
            return query
            select
                true,
                'paid_plan_active',
                v_plan,
                null::uuid,
                null::integer,
                v_used,
                v_limit,
                false;
            return;
        end if;

        perform set_config('app.bypass_profile_guard', '1', true);
        update public.profiles
        set
            plan = 'free',
            status = 'expired',
            requested_plan = '',
            updated_at = now()
        where id = v_uid;

        v_plan := 'free';
        v_status := 'expired';
    end if;

    if v_used + p_chars > v_limit then
        return query
        select
            false,
            'free_quota_exceeded',
            'free',
            null::uuid,
            greatest(v_limit - v_used, 0),
            v_used,
            v_limit,
            true;
        return;
    end if;

    perform set_config('app.bypass_profile_guard', '1', true);
    update public.profiles
    set
        free_chars_used = v_used + p_chars,
        updated_at = now()
    where id = v_uid
    returning profiles.free_chars_used
    into v_used;

    insert into public.generation_quota_reservations (
        user_id,
        chars,
        plan_at_reservation,
        status,
        expires_at
    )
    values (
        v_uid,
        p_chars,
        'free',
        'reserved',
        now() + interval '15 minutes'
    )
    returning id
    into v_reservation_id;

    return query
    select
        true,
        'free_quota_reserved',
        'free',
        v_reservation_id,
        greatest(v_limit - v_used, 0),
        v_used,
        v_limit,
        (greatest(v_limit - v_used, 0) = 0);
end;
$$;

create function public.finalize_generation_quota(
    p_reservation_id uuid,
    p_success boolean default true,
    p_device_fingerprint text default null
)
returns table(
    finalized boolean,
    action text,
    current_plan text,
    remaining_chars integer,
    free_chars_used integer,
    free_chars_limit integer
)
language plpgsql
security definer
set search_path = public
as $$
declare
    v_uid uuid := auth.uid();
    v_res record;
    v_profile record;
    v_limit integer;
    v_used integer;
begin
    if v_uid is null then
        raise exception 'NOT_AUTHENTICATED';
    end if;

    perform public.assert_device_lock(v_uid, p_device_fingerprint);

    if p_reservation_id is null then
        return query
        select
            true,
            'no_reservation',
            'free',
            null::integer,
            null::integer,
            null::integer;
        return;
    end if;

    perform public.reconcile_expired_quota_reservations(v_uid);

    select *
    into v_res
    from public.generation_quota_reservations
    where id = p_reservation_id
      and user_id = v_uid
    for update;

    if not found then
        raise exception 'Quota reservation not found';
    end if;

    select *
    into v_profile
    from public.profiles
    where id = v_uid
    for update;

    if not found then
        raise exception 'PROFILE_NOT_FOUND';
    end if;

    v_limit := greatest(coalesce(v_profile.free_chars_limit, 3000), 0);
    v_used := greatest(coalesce(v_profile.free_chars_used, 0), 0);

    if v_res.status <> 'reserved' then
        return query
        select
            true,
            ('already_' || v_res.status)::text,
            lower(coalesce(v_profile.plan, 'free')),
            greatest(v_limit - v_used, 0),
            v_used,
            v_limit;
        return;
    end if;

    if p_success then
        update public.generation_quota_reservations
        set
            status = 'consumed',
            finalized_at = now()
        where id = v_res.id;

        return query
        select
            true,
            'consumed',
            lower(coalesce(v_profile.plan, 'free')),
            greatest(v_limit - v_used, 0),
            v_used,
            v_limit;
        return;
    end if;

    if v_res.plan_at_reservation = 'free' then
        perform set_config('app.bypass_profile_guard', '1', true);
        update public.profiles
        set
            free_chars_used = greatest(coalesce(free_chars_used, 0) - v_res.chars, 0),
            updated_at = now()
        where id = v_uid
        returning
            greatest(coalesce(free_chars_used, 0), 0),
            greatest(coalesce(free_chars_limit, 3000), 0)
        into v_used, v_limit;
    end if;

    update public.generation_quota_reservations
    set
        status = 'released',
        finalized_at = now()
    where id = v_res.id;

    return query
    select
        true,
        'released',
        lower(coalesce(v_profile.plan, 'free')),
        greatest(v_limit - v_used, 0),
        v_used,
        v_limit;
end;
$$;

create function public.get_plan_state(
    p_device_fingerprint text default null
)
returns table(
    plan text,
    status text,
    requested_plan text,
    expiry_date timestamptz,
    free_chars_used integer,
    free_chars_limit integer
)
language plpgsql
security definer
set search_path = public
as $$
declare
    v_uid uuid := auth.uid();
    v_profile record;
begin
    if v_uid is null then
        raise exception 'NOT_AUTHENTICATED';
    end if;

    perform public.assert_device_lock(v_uid, p_device_fingerprint);
    perform public.reconcile_expired_quota_reservations(v_uid);

    select *
    into v_profile
    from public.profiles
    where id = v_uid
    for update;

    if not found then
        return;
    end if;

    if lower(coalesce(v_profile.status, '')) = 'active'
       and public.is_vidzeo_paid_plan(v_profile.plan)
       and v_profile.expiry_date is not null
       and v_profile.expiry_date <= now() then
        perform set_config('app.bypass_profile_guard', '1', true);
        update public.profiles
        set
            plan = 'free',
            status = 'expired',
            requested_plan = '',
            updated_at = now()
        where id = v_uid
        returning *
        into v_profile;
    end if;

    return query
    select
        coalesce(v_profile.plan, 'free'),
        coalesce(v_profile.status, 'free'),
        coalesce(v_profile.requested_plan, ''),
        v_profile.expiry_date,
        greatest(coalesce(v_profile.free_chars_used, 0), 0),
        greatest(coalesce(v_profile.free_chars_limit, 3000), 0);
end;
$$;

create function public.consume_generation_quota(
    p_chars integer,
    p_device_fingerprint text default null
)
returns table(
    allowed boolean,
    reason text,
    current_plan text,
    remaining_chars integer,
    free_chars_used integer,
    free_chars_limit integer,
    "requires_upgrade" boolean
)
language plpgsql
security definer
set search_path = public
as $$
declare
    v_uid uuid := auth.uid();
    v_profile record;
    v_limit integer;
    v_used integer;
    v_plan text;
    v_status text;
begin
    if v_uid is null then
        raise exception 'NOT_AUTHENTICATED';
    end if;

    perform public.assert_device_lock(v_uid, p_device_fingerprint);

    if p_chars is null or p_chars <= 0 then
        raise exception 'Requested characters must be greater than zero';
    end if;

    perform public.reconcile_expired_quota_reservations(v_uid);

    select *
    into v_profile
    from public.profiles
    where id = v_uid
    for update;

    if not found then
        raise exception 'PROFILE_NOT_FOUND';
    end if;

    v_plan := lower(coalesce(v_profile.plan, 'free'));
    v_status := lower(coalesce(v_profile.status, 'free'));
    v_limit := greatest(coalesce(v_profile.free_chars_limit, 3000), 0);
    v_used := greatest(coalesce(v_profile.free_chars_used, 0), 0);

    if v_status = 'active' and public.is_vidzeo_paid_plan(v_plan) then
        if v_profile.expiry_date is null or v_profile.expiry_date > now() then
            return query
            select
                true,
                'paid_plan_active',
                v_plan,
                null::integer,
                v_used,
                v_limit,
                false;
            return;
        end if;

        perform set_config('app.bypass_profile_guard', '1', true);
        update public.profiles
        set
            plan = 'free',
            status = 'expired',
            requested_plan = '',
            updated_at = now()
        where id = v_uid;

        v_plan := 'free';
        v_status := 'expired';
    end if;

    if v_used + p_chars > v_limit then
        return query
        select
            false,
            'free_quota_exceeded',
            'free',
            greatest(v_limit - v_used, 0),
            v_used,
            v_limit,
            true;
        return;
    end if;

    perform set_config('app.bypass_profile_guard', '1', true);
    update public.profiles
    set
        free_chars_used = v_used + p_chars,
        updated_at = now()
    where id = v_uid
    returning profiles.free_chars_used
    into v_used;

    return query
    select
        true,
        'free_quota_consumed',
        'free',
        greatest(v_limit - v_used, 0),
        v_used,
        v_limit,
        (greatest(v_limit - v_used, 0) = 0);
end;
$$;

create function public.admin_reset_device_binding(
    p_user_id uuid,
    p_admin_note text default null
)
returns table(success boolean, status text, user_id uuid)
language plpgsql
security definer
set search_path = public
as $$
declare
    v_profile record;
begin
    if (auth.jwt() ->> 'role') <> 'service_role' then
        raise exception 'FORBIDDEN';
    end if;

    if p_user_id is null then
        raise exception 'PROFILE_NOT_FOUND';
    end if;

    select *
    into v_profile
    from public.profiles
    where id = p_user_id
    for update;

    if not found then
        raise exception 'PROFILE_NOT_FOUND';
    end if;

    perform set_config('app.bypass_profile_guard', '1', true);
    update public.profiles
    set
        device_fingerprint = null,
        updated_at = now(),
        metadata = coalesce(metadata, '{}'::jsonb) || jsonb_build_object(
            'device_reset_at', now(),
            'device_reset_note', coalesce(nullif(p_admin_note, ''), 'manual_reset')
        )
    where id = p_user_id;

    return query select true, 'DEVICE_RESET', p_user_id;
end;
$$;

revoke all on function public.assert_device_lock(uuid, text) from public;
revoke all on function public.sync_device_telemetry(text, jsonb, text, text) from public;
revoke all on function public.reconcile_expired_quota_reservations(uuid) from public;
revoke all on function public.request_plan_upgrade(text, text) from public;
revoke all on function public.get_plan_state(text) from public;
revoke all on function public.consume_generation_quota(integer, text) from public;
revoke all on function public.reserve_generation_quota(integer, text) from public;
revoke all on function public.finalize_generation_quota(uuid, boolean, text) from public;
revoke all on function public.admin_reset_device_binding(uuid, text) from public;

grant execute on function public.sync_device_telemetry(text, jsonb, text, text) to authenticated;
grant execute on function public.request_plan_upgrade(text, text) to authenticated;
grant execute on function public.get_plan_state(text) to authenticated;
grant execute on function public.consume_generation_quota(integer, text) to authenticated;
grant execute on function public.reserve_generation_quota(integer, text) to authenticated;
grant execute on function public.finalize_generation_quota(uuid, boolean, text) to authenticated;
grant execute on function public.admin_reset_device_binding(uuid, text) to service_role;

notify pgrst, 'reload schema';
