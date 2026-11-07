-- Vidzeo Supabase auth/plan repair.
-- Canonical access source: user_profiles + user_plan_assignments + plans.
-- Compatibility mirror: profiles. Device access: max 2 active devices per user.

create extension if not exists pgcrypto;

alter table if exists public.user_profiles
    add column if not exists device_fingerprint text,
    add column if not exists device_info jsonb not null default '{}'::jsonb,
    add column if not exists last_login_ip text,
    add column if not exists user_agent text,
    add column if not exists total_logins integer not null default 0,
    add column if not exists metadata jsonb not null default '{}'::jsonb;

alter table if exists public.profiles
    add column if not exists plan text default 'free',
    add column if not exists status text default 'free',
    add column if not exists requested_plan text default '',
    add column if not exists expiry_date timestamptz,
    add column if not exists activation_date timestamptz,
    add column if not exists device_fingerprint text,
    add column if not exists last_login_ip text,
    add column if not exists user_agent text,
    add column if not exists device_info jsonb default '{}'::jsonb,
    add column if not exists last_login_at timestamptz,
    add column if not exists total_logins integer default 0,
    add column if not exists metadata jsonb default '{}'::jsonb;

alter table if exists public.plans
    add column if not exists script_word_limit integer;

update public.plans
set script_word_limit = coalesce(script_word_limit, greatest(coalesce(video_limit_minutes, 0), 0) * 156)
where script_word_limit is null;

create table if not exists public.user_devices (
    id uuid primary key default gen_random_uuid(),
    user_profile_id uuid not null references public.user_profiles(id) on delete cascade,
    auth_user_id uuid not null references auth.users(id) on delete cascade,
    device_fingerprint text not null,
    device_info jsonb not null default '{}'::jsonb,
    status text not null default 'active' check (status in ('active', 'revoked')),
    first_seen_at timestamptz not null default now(),
    last_seen_at timestamptz not null default now(),
    last_login_ip text,
    user_agent text,
    revoked_at timestamptz,
    revoked_by_email text,
    revoke_note text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

drop trigger if exists trg_user_devices_updated_at on public.user_devices;
create trigger trg_user_devices_updated_at
before update on public.user_devices
for each row
execute function public.set_updated_at();

create index if not exists idx_user_devices_profile_status
    on public.user_devices(user_profile_id, status, last_seen_at desc);
create index if not exists idx_user_devices_auth_user_status
    on public.user_devices(auth_user_id, status, last_seen_at desc);
create unique index if not exists idx_user_devices_active_fingerprint
    on public.user_devices(user_profile_id, device_fingerprint)
    where status = 'active';

create table if not exists public.user_device_events (
    id uuid primary key default gen_random_uuid(),
    auth_user_id uuid not null references auth.users(id) on delete cascade,
    user_profile_id uuid references public.user_profiles(id) on delete cascade,
    event_type text not null,
    device_fingerprint text,
    last_login_ip text,
    user_agent text,
    device_info jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now()
);

alter table public.user_device_events
    drop constraint if exists user_device_events_event_type_check;
alter table public.user_device_events
    add constraint user_device_events_event_type_check
    check (event_type in (
        'login',
        'device_bound',
        'device_match',
        'device_mismatch',
        'device_reset',
        'device_revoked',
        'device_limit_reached'
    ));

create index if not exists idx_user_device_events_auth_user_created
    on public.user_device_events(auth_user_id, created_at desc);

with ranked as (
    select
        id,
        row_number() over (
            partition by user_profile_id, status
            order by coalesce(starts_at, created_at) desc, created_at desc
        ) as rn
    from public.user_plan_assignments
    where status in ('active', 'pending')
)
update public.user_plan_assignments a
set status = 'revoked', updated_at = now()
from ranked r
where a.id = r.id
  and r.rn > 1;

update public.user_plan_assignments
set status = 'expired', updated_at = now()
where status = 'active'
  and ends_at is not null
  and ends_at <= now();

create unique index if not exists idx_user_plan_assignments_one_active
    on public.user_plan_assignments(user_profile_id)
    where status = 'active';
create unique index if not exists idx_user_plan_assignments_one_pending
    on public.user_plan_assignments(user_profile_id)
    where status = 'pending';

insert into public.user_devices (
    user_profile_id,
    auth_user_id,
    device_fingerprint,
    device_info,
    status,
    first_seen_at,
    last_seen_at,
    last_login_ip,
    user_agent
)
select
    up.id,
    up.auth_user_id,
    lower(trim(up.device_fingerprint)),
    coalesce(up.device_info, '{}'::jsonb),
    'active',
    coalesce(up.last_login_at, up.created_at, now()),
    coalesce(up.last_login_at, up.updated_at, now()),
    up.last_login_ip,
    up.user_agent
from public.user_profiles up
where up.auth_user_id is not null
  and nullif(trim(coalesce(up.device_fingerprint, '')), '') is not null
on conflict do nothing;

alter table public.user_devices enable row level security;

drop policy if exists "Users can read own devices" on public.user_devices;
create policy "Users can read own devices"
on public.user_devices
for select
to authenticated
using (auth.uid() = auth_user_id);

drop policy if exists "Admin full access user devices" on public.user_devices;
create policy "Admin full access user devices"
on public.user_devices
for all
using ((auth.jwt() ->> 'role') = 'service_role')
with check ((auth.jwt() ->> 'role') = 'service_role');

drop function if exists public.get_vidzeo_access_state(text, jsonb, text, text);
drop function if exists public.request_vidzeo_plan(text, text, jsonb, text, text);
drop function if exists public.assert_vidzeo_device_lock(text, jsonb, text, text);
drop function if exists public.list_user_devices(uuid);
drop function if exists public.admin_revoke_user_device(uuid, text, text);
drop function if exists public.admin_reset_user_devices(uuid, text, text);
drop function if exists public.admin_reset_device_binding(uuid, text);

create or replace function public.ensure_vidzeo_user_profile()
returns uuid
language plpgsql
security definer
set search_path = public
as $$
declare
    v_uid uuid := auth.uid();
    v_email text;
    v_name text;
    v_avatar_url text;
    v_profile_id uuid;
begin
    if v_uid is null then
        raise exception 'NOT_AUTHENTICATED';
    end if;

    select
        lower(coalesce(u.email, '')),
        coalesce(u.raw_user_meta_data ->> 'full_name', u.raw_user_meta_data ->> 'name', split_part(coalesce(u.email, ''), '@', 1), 'Vidzeo Creator'),
        coalesce(u.raw_user_meta_data ->> 'avatar_url', u.raw_user_meta_data ->> 'picture', '')
    into v_email, v_name, v_avatar_url
    from auth.users u
    where u.id = v_uid;

    if coalesce(v_email, '') = '' then
        raise exception 'PROFILE_NOT_FOUND';
    end if;

    insert into public.user_profiles (
        auth_user_id,
        email,
        full_name,
        avatar_url,
        account_status,
        last_login_at
    )
    values (
        v_uid,
        v_email,
        v_name,
        v_avatar_url,
        'pending',
        now()
    )
    on conflict (email) do update
    set
        auth_user_id = coalesce(public.user_profiles.auth_user_id, excluded.auth_user_id),
        full_name = coalesce(nullif(excluded.full_name, ''), public.user_profiles.full_name),
        avatar_url = coalesce(nullif(excluded.avatar_url, ''), public.user_profiles.avatar_url),
        last_login_at = now(),
        updated_at = now()
    returning id
    into v_profile_id;

    perform set_config('app.bypass_profile_guard', '1', true);
    insert into public.profiles (id, email, name, avatar_url)
    values (v_uid, v_email, v_name, v_avatar_url)
    on conflict (id) do update
    set
        email = excluded.email,
        name = excluded.name,
        avatar_url = excluded.avatar_url,
        updated_at = now();

    return v_profile_id;
end;
$$;

create or replace function public.sync_assignment_state_for_auth_user(p_auth_user_id uuid)
returns void
language plpgsql
security definer
set search_path = public
as $$
declare
    v_profile public.user_profiles%rowtype;
    v_active record;
    v_pending record;
    v_plan text := 'free';
    v_status text := 'free';
    v_requested_plan text := '';
begin
    select *
    into v_profile
    from public.user_profiles
    where auth_user_id = p_auth_user_id
    limit 1;

    if v_profile.id is null then
        return;
    end if;

    update public.user_plan_assignments
    set status = 'expired', updated_at = now()
    where user_profile_id = v_profile.id
      and status = 'active'
      and ends_at is not null
      and ends_at <= now();

    select a.*
    into v_active
    from public.user_plan_assignments a
    join public.plans p on p.plan_key = a.plan_key
    where a.user_profile_id = v_profile.id
      and a.status = 'active'
      and (a.starts_at is null or a.starts_at <= now())
      and (a.ends_at is null or a.ends_at > now())
      and p.is_active = true
    order by coalesce(a.starts_at, a.created_at) desc, a.created_at desc
    limit 1;

    select a.*
    into v_pending
    from public.user_plan_assignments a
    join public.plans p on p.plan_key = a.plan_key
    where a.user_profile_id = v_profile.id
      and a.status = 'pending'
      and p.is_active = true
    order by a.created_at desc
    limit 1;

    if lower(coalesce(v_profile.account_status, 'pending')) = 'suspended' then
        v_status := 'suspended';
    elsif v_active.id is not null then
        v_plan := v_active.plan_key;
        v_status := 'active';
    elsif v_pending.id is not null then
        v_status := 'pending';
        v_requested_plan := v_pending.plan_key;
    end if;

    perform set_config('app.bypass_profile_guard', '1', true);
    update public.profiles
    set
        email = v_profile.email,
        name = coalesce(v_profile.full_name, v_profile.email),
        avatar_url = coalesce(v_profile.avatar_url, ''),
        plan = v_plan,
        status = v_status,
        requested_plan = v_requested_plan,
        activation_date = case when v_active.id is not null then coalesce(v_active.starts_at, v_active.created_at) else null end,
        expiry_date = case when v_active.id is not null then v_active.ends_at else null end,
        updated_at = now()
    where id = p_auth_user_id;
end;
$$;

create or replace function public.assert_vidzeo_device_lock(
    p_device_fingerprint text,
    p_device_info jsonb default '{}'::jsonb,
    p_last_login_ip text default null,
    p_user_agent text default null
)
returns table(
    device_id uuid,
    device_fingerprint text,
    device_status text,
    device_count integer,
    device_limit integer
)
language plpgsql
security definer
set search_path = public
as $$
declare
    v_uid uuid := auth.uid();
    v_profile_id uuid;
    v_fp text := lower(trim(coalesce(p_device_fingerprint, '')));
    v_device public.user_devices%rowtype;
    v_count integer := 0;
    v_limit integer := 2;
    v_event_type text := 'device_match';
begin
    if v_uid is null then
        raise exception 'NOT_AUTHENTICATED';
    end if;
    if v_fp = '' then
        raise exception 'DEVICE_FINGERPRINT_REQUIRED';
    end if;

    v_profile_id := public.ensure_vidzeo_user_profile();

    perform 1
    from public.user_profiles up
    where up.id = v_profile_id
    for update;

    select count(*)
    into v_count
    from public.user_devices ud
    where ud.user_profile_id = v_profile_id
      and ud.status = 'active';

    select *
    into v_device
    from public.user_devices ud
    where ud.user_profile_id = v_profile_id
      and ud.device_fingerprint = v_fp
      and ud.status = 'active'
    limit 1
    for update;

    if v_device.id is not null then
        update public.user_devices
        set
            device_info = coalesce(p_device_info, '{}'::jsonb),
            last_seen_at = now(),
            last_login_ip = nullif(p_last_login_ip, ''),
            user_agent = nullif(p_user_agent, ''),
            updated_at = now()
        where id = v_device.id
        returning *
        into v_device;
    else
        if v_count >= v_limit then
            insert into public.user_device_events (
                auth_user_id, user_profile_id, event_type, device_fingerprint,
                last_login_ip, user_agent, device_info
            )
            values (
                v_uid, v_profile_id, 'device_limit_reached', v_fp,
                nullif(p_last_login_ip, ''), nullif(p_user_agent, ''), coalesce(p_device_info, '{}'::jsonb)
            );
            raise exception 'DEVICE_LIMIT_REACHED';
        end if;

        insert into public.user_devices (
            user_profile_id,
            auth_user_id,
            device_fingerprint,
            device_info,
            status,
            first_seen_at,
            last_seen_at,
            last_login_ip,
            user_agent
        )
        values (
            v_profile_id,
            v_uid,
            v_fp,
            coalesce(p_device_info, '{}'::jsonb),
            'active',
            now(),
            now(),
            nullif(p_last_login_ip, ''),
            nullif(p_user_agent, '')
        )
        returning *
        into v_device;

        v_count := v_count + 1;
        v_event_type := 'device_bound';
    end if;

    update public.user_profiles
    set
        device_fingerprint = v_fp,
        device_info = coalesce(p_device_info, '{}'::jsonb),
        last_login_ip = nullif(p_last_login_ip, ''),
        user_agent = nullif(p_user_agent, ''),
        last_login_at = now(),
        total_logins = greatest(coalesce(total_logins, 0), 0) + 1,
        updated_at = now()
    where id = v_profile_id;

    perform set_config('app.bypass_profile_guard', '1', true);
    update public.profiles
    set
        device_fingerprint = v_fp,
        device_info = coalesce(p_device_info, '{}'::jsonb),
        last_login_ip = nullif(p_last_login_ip, ''),
        user_agent = nullif(p_user_agent, ''),
        last_login_at = now(),
        total_logins = greatest(coalesce(total_logins, 0), 0) + 1,
        updated_at = now()
    where id = v_uid;

    insert into public.user_device_events (
        auth_user_id, user_profile_id, event_type, device_fingerprint,
        last_login_ip, user_agent, device_info
    )
    values (
        v_uid, v_profile_id, v_event_type, v_fp,
        nullif(p_last_login_ip, ''), nullif(p_user_agent, ''), coalesce(p_device_info, '{}'::jsonb)
    );

    return query
    select v_device.id, v_device.device_fingerprint, v_device.status, v_count, v_limit;
end;
$$;

create or replace function public.get_vidzeo_access_state(
    p_device_fingerprint text,
    p_device_info jsonb,
    p_last_login_ip text,
    p_user_agent text
)
returns table(
    user_id uuid,
    auth_user_id uuid,
    email text,
    name text,
    avatar_url text,
    plan_key text,
    plan_name text,
    plan_status text,
    access_state text,
    access_copy text,
    account_status text,
    billing_period text,
    max_video_minutes integer,
    max_script_words integer,
    price_monthly_display numeric,
    device_id uuid,
    device_fingerprint text,
    device_status text,
    device_count integer,
    device_limit integer
)
language plpgsql
security definer
set search_path = public
as $$
declare
    v_uid uuid := auth.uid();
    v_profile_id uuid;
    v_profile public.user_profiles%rowtype;
    v_assignment record;
    v_device record;
begin
    if v_uid is null then
        raise exception 'NOT_AUTHENTICATED';
    end if;

    select *
    into v_device
    from public.assert_vidzeo_device_lock(p_device_fingerprint, p_device_info, p_last_login_ip, p_user_agent)
    limit 1;

    v_profile_id := public.ensure_vidzeo_user_profile();
    perform public.sync_assignment_state_for_auth_user(v_uid);

    select *
    into v_profile
    from public.user_profiles
    where id = v_profile_id
    limit 1;

    select
        a.id,
        a.plan_key,
        a.status,
        a.starts_at,
        a.ends_at,
        p.display_name,
        p.name as plan_tier_name,
        p.billing_period,
        p.video_limit_minutes,
        p.script_word_limit,
        p.price_monthly_display
    into v_assignment
    from public.user_plan_assignments a
    join public.plans p on p.plan_key = a.plan_key
    where a.user_profile_id = v_profile_id
      and a.status = 'active'
      and (a.starts_at is null or a.starts_at <= now())
      and (a.ends_at is null or a.ends_at > now())
      and p.is_active = true
    order by coalesce(a.starts_at, a.created_at) desc, a.created_at desc
    limit 1;

    if v_assignment.id is null then
        select
            a.id,
            a.plan_key,
            a.status,
            a.starts_at,
            a.ends_at,
            p.display_name,
            p.name as plan_tier_name,
            p.billing_period,
            p.video_limit_minutes,
            p.script_word_limit,
            p.price_monthly_display
        into v_assignment
        from public.user_plan_assignments a
        join public.plans p on p.plan_key = a.plan_key
        where a.user_profile_id = v_profile_id
          and a.status = 'pending'
          and p.is_active = true
        order by a.created_at desc
        limit 1;
    end if;

    return query
    select
        v_profile.id,
        v_uid,
        v_profile.email,
        coalesce(v_profile.full_name, v_profile.email, 'Vidzeo Creator'),
        coalesce(v_profile.avatar_url, ''),
        v_assignment.plan_key,
        coalesce(v_assignment.display_name, 'No active plan'),
        coalesce(v_assignment.status, 'pending'),
        case
            when lower(coalesce(v_profile.account_status, 'pending')) = 'suspended' then 'suspended'
            when v_assignment.id is not null and v_assignment.status = 'active' then 'active'
            else 'pending'
        end,
        case
            when lower(coalesce(v_profile.account_status, 'pending')) = 'suspended'
                then 'Your account is suspended. Contact support or an administrator.'
            when v_assignment.id is not null and v_assignment.status = 'active'
                then 'Your studio access is active.'
            else 'Pick a plan and wait for admin activation to unlock the studio.'
        end,
        coalesce(v_profile.account_status, 'pending'),
        v_assignment.billing_period,
        v_assignment.video_limit_minutes,
        coalesce(v_assignment.script_word_limit, v_assignment.video_limit_minutes * 156),
        v_assignment.price_monthly_display,
        v_device.device_id,
        v_device.device_fingerprint,
        v_device.device_status,
        v_device.device_count,
        v_device.device_limit;
end;
$$;

create or replace function public.request_vidzeo_plan(
    p_plan_key text,
    p_device_fingerprint text,
    p_device_info jsonb,
    p_last_login_ip text,
    p_user_agent text
)
returns table(
    user_id uuid,
    auth_user_id uuid,
    email text,
    name text,
    avatar_url text,
    plan_key text,
    plan_name text,
    plan_status text,
    access_state text,
    access_copy text,
    account_status text,
    billing_period text,
    max_video_minutes integer,
    max_script_words integer,
    price_monthly_display numeric,
    device_id uuid,
    device_fingerprint text,
    device_status text,
    device_count integer,
    device_limit integer
)
language plpgsql
security definer
set search_path = public
as $$
declare
    v_uid uuid := auth.uid();
    v_profile_id uuid;
    v_plan_key text := lower(trim(coalesce(p_plan_key, '')));
begin
    if v_uid is null then
        raise exception 'NOT_AUTHENTICATED';
    end if;

    perform public.assert_vidzeo_device_lock(p_device_fingerprint, p_device_info, p_last_login_ip, p_user_agent);
    v_profile_id := public.ensure_vidzeo_user_profile();

    if not exists (
        select 1
        from public.plans p
        where p.plan_key = v_plan_key
          and p.is_active = true
    ) then
        raise exception 'Invalid plan key: %', v_plan_key;
    end if;

    update public.user_plan_assignments
    set status = 'revoked', updated_at = now()
    where user_profile_id = v_profile_id
      and status = 'pending';

    insert into public.user_plan_assignments (
        user_profile_id,
        plan_key,
        status,
        requested_by_email,
        note
    )
    select
        v_profile_id,
        v_plan_key,
        'pending',
        up.email,
        'Requested inside the Vidnets Studio desktop app.'
    from public.user_profiles up
    where up.id = v_profile_id;

    perform public.sync_assignment_state_for_auth_user(v_uid);

    return query
    select *
    from public.get_vidzeo_access_state(
        p_device_fingerprint,
        p_device_info,
        p_last_login_ip,
        p_user_agent
    );
end;
$$;

create or replace function public.list_user_devices(p_user_id uuid default null)
returns table(
    id uuid,
    user_profile_id uuid,
    auth_user_id uuid,
    device_fingerprint text,
    device_info jsonb,
    status text,
    first_seen_at timestamptz,
    last_seen_at timestamptz,
    last_login_ip text,
    user_agent text,
    revoked_at timestamptz,
    revoked_by_email text,
    revoke_note text
)
language plpgsql
security definer
set search_path = public
as $$
declare
    v_uid uuid := auth.uid();
    v_role text := auth.jwt() ->> 'role';
begin
    if v_uid is null and v_role is distinct from 'service_role' then
        raise exception 'NOT_AUTHENTICATED';
    end if;

    return query
    select
        ud.id,
        ud.user_profile_id,
        ud.auth_user_id,
        ud.device_fingerprint,
        ud.device_info,
        ud.status,
        ud.first_seen_at,
        ud.last_seen_at,
        ud.last_login_ip,
        ud.user_agent,
        ud.revoked_at,
        ud.revoked_by_email,
        ud.revoke_note
    from public.user_devices ud
    where (
        v_role = 'service_role'
        and (
            p_user_id is null
            or ud.user_profile_id = p_user_id
            or ud.auth_user_id = p_user_id
        )
    )
    or (
        v_role is distinct from 'service_role'
        and ud.auth_user_id = v_uid
    )
    order by ud.status, ud.last_seen_at desc;
end;
$$;

create or replace function public.admin_revoke_user_device(
    p_device_id uuid,
    p_admin_email text default null,
    p_admin_note text default null
)
returns setof public.user_devices
language plpgsql
security definer
set search_path = public
as $$
declare
    v_device public.user_devices%rowtype;
begin
    if (auth.jwt() ->> 'role') is distinct from 'service_role' then
        raise exception 'ADMIN_REQUIRED';
    end if;

    update public.user_devices
    set
        status = 'revoked',
        revoked_at = now(),
        revoked_by_email = nullif(lower(coalesce(p_admin_email, '')), ''),
        revoke_note = nullif(coalesce(p_admin_note, ''), ''),
        updated_at = now()
    where id = p_device_id
    returning *
    into v_device;

    if v_device.id is null then
        raise exception 'DEVICE_NOT_FOUND';
    end if;

    insert into public.user_device_events (
        auth_user_id, user_profile_id, event_type, device_fingerprint, device_info
    )
    values (
        v_device.auth_user_id, v_device.user_profile_id, 'device_revoked',
        v_device.device_fingerprint, coalesce(v_device.device_info, '{}'::jsonb)
    );

    return query select * from public.user_devices where id = p_device_id;
end;
$$;

create or replace function public.admin_reset_user_devices(
    p_user_id uuid,
    p_admin_email text default null,
    p_admin_note text default null
)
returns integer
language plpgsql
security definer
set search_path = public
as $$
declare
    v_profile public.user_profiles%rowtype;
    v_count integer := 0;
begin
    if (auth.jwt() ->> 'role') is distinct from 'service_role' then
        raise exception 'ADMIN_REQUIRED';
    end if;

    select *
    into v_profile
    from public.user_profiles up
    where up.id = p_user_id
       or up.auth_user_id = p_user_id
    limit 1;

    if v_profile.id is null then
        raise exception 'USER_NOT_FOUND';
    end if;

    update public.user_devices
    set
        status = 'revoked',
        revoked_at = now(),
        revoked_by_email = nullif(lower(coalesce(p_admin_email, '')), ''),
        revoke_note = nullif(coalesce(p_admin_note, ''), ''),
        updated_at = now()
    where user_profile_id = v_profile.id
      and status = 'active';

    get diagnostics v_count = row_count;

    insert into public.user_device_events (
        auth_user_id, user_profile_id, event_type, device_fingerprint, device_info
    )
    select
        auth_user_id, user_profile_id, 'device_reset', device_fingerprint, coalesce(device_info, '{}'::jsonb)
    from public.user_devices
    where user_profile_id = v_profile.id
      and revoked_at >= now() - interval '5 seconds';

    update public.user_profiles
    set
        device_fingerprint = null,
        device_info = '{}'::jsonb,
        last_login_ip = null,
        user_agent = null,
        updated_at = now()
    where id = v_profile.id;

    perform set_config('app.bypass_profile_guard', '1', true);
    update public.profiles
    set
        device_fingerprint = null,
        device_info = '{}'::jsonb,
        last_login_ip = null,
        user_agent = null,
        updated_at = now()
    where id = v_profile.auth_user_id;

    return v_count;
end;
$$;

create or replace function public.admin_reset_device_binding(p_target_user_profile_id uuid, p_admin_email text default null)
returns table(success boolean, status text, user_id uuid)
language plpgsql
security definer
set search_path = public
as $$
begin
    perform public.admin_reset_user_devices(p_target_user_profile_id, p_admin_email, 'Compatibility reset');
    return query select true, 'reset'::text, p_target_user_profile_id;
end;
$$;

select public.sync_assignment_state_for_auth_user(up.auth_user_id)
from public.user_profiles up
where up.auth_user_id is not null;

revoke all on function public.ensure_vidzeo_user_profile() from public;
revoke all on function public.sync_assignment_state_for_auth_user(uuid) from public;
revoke all on function public.assert_vidzeo_device_lock(text, jsonb, text, text) from public;
revoke all on function public.get_vidzeo_access_state(text, jsonb, text, text) from public;
revoke all on function public.request_vidzeo_plan(text, text, jsonb, text, text) from public;
revoke all on function public.list_user_devices(uuid) from public;
revoke all on function public.admin_revoke_user_device(uuid, text, text) from public;
revoke all on function public.admin_reset_user_devices(uuid, text, text) from public;
revoke all on function public.admin_reset_device_binding(uuid, text) from public;

grant execute on function public.get_vidzeo_access_state(text, jsonb, text, text) to authenticated;
grant execute on function public.request_vidzeo_plan(text, text, jsonb, text, text) to authenticated;
grant execute on function public.list_user_devices(uuid) to authenticated, service_role;
grant execute on function public.admin_revoke_user_device(uuid, text, text) to service_role;
grant execute on function public.admin_reset_user_devices(uuid, text, text) to service_role;
grant execute on function public.admin_reset_device_binding(uuid, text) to service_role;
grant execute on function public.sync_assignment_state_for_auth_user(uuid) to service_role;

notify pgrst, 'reload schema';
