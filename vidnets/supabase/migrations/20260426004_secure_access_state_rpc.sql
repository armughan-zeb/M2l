-- Vidnets Studio secure consumer access RPC.
-- Run after the base Vidzeo schema. This lets the shipped local app use only
-- Supabase anon + the user's JWT while plan state stays enforced inside Postgres.

create extension if not exists pgcrypto;

alter table if exists public.user_profiles
    add column if not exists device_fingerprint text,
    add column if not exists device_info jsonb not null default '{}'::jsonb,
    add column if not exists last_login_ip text,
    add column if not exists user_agent text,
    add column if not exists total_logins integer not null default 0,
    add column if not exists metadata jsonb not null default '{}'::jsonb;

alter table if exists public.plans
    add column if not exists script_word_limit integer;

update public.plans
set script_word_limit = case plan_key
    when 'basic_monthly' then 2340
    when 'plus_monthly' then 4680
    when 'max_monthly' then 9360
    when 'basic_yearly' then 2340
    when 'plus_yearly' then 4680
    when 'max_yearly' then 9360
    else coalesce(script_word_limit, greatest(coalesce(video_limit_minutes, 0), 0) * 156)
end
where coalesce(script_word_limit, 0) = 0
   or plan_key in (
        'basic_monthly',
        'plus_monthly',
        'max_monthly',
        'basic_yearly',
        'plus_yearly',
        'max_yearly'
   );

create table if not exists public.user_device_events (
    id uuid primary key default gen_random_uuid(),
    auth_user_id uuid not null references auth.users(id) on delete cascade,
    user_profile_id uuid references public.user_profiles(id) on delete cascade,
    event_type text not null check (event_type in ('login', 'device_bound', 'device_match', 'device_mismatch', 'device_reset')),
    device_fingerprint text,
    last_login_ip text,
    user_agent text,
    device_info jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now()
);

create index if not exists idx_user_device_events_auth_user_created
    on public.user_device_events(auth_user_id, created_at desc);

create index if not exists idx_user_profiles_device_fingerprint
    on public.user_profiles(device_fingerprint);

alter table public.user_device_events enable row level security;

drop policy if exists "Users can read own device events" on public.user_device_events;
create policy "Users can read own device events"
on public.user_device_events
for select
to authenticated
using (auth.uid() = auth_user_id);

drop policy if exists "Admin full access user device events" on public.user_device_events;
create policy "Admin full access user device events"
on public.user_device_events
for all
using ((auth.jwt() ->> 'role') = 'service_role')
with check ((auth.jwt() ->> 'role') = 'service_role');

alter table public.user_profiles enable row level security;
alter table public.user_plan_assignments enable row level security;
alter table public.plans enable row level security;

drop policy if exists "Users can read own profile" on public.user_profiles;
create policy "Users can read own profile"
on public.user_profiles
for select
to authenticated
using (auth.uid() = auth_user_id);

drop policy if exists "Admin full access user profiles" on public.user_profiles;
create policy "Admin full access user profiles"
on public.user_profiles
for all
using ((auth.jwt() ->> 'role') = 'service_role')
with check ((auth.jwt() ->> 'role') = 'service_role');

drop policy if exists "Users can read own plan assignments" on public.user_plan_assignments;
create policy "Users can read own plan assignments"
on public.user_plan_assignments
for select
to authenticated
using (
    exists (
        select 1
        from public.user_profiles up
        where up.id = user_plan_assignments.user_profile_id
          and up.auth_user_id = auth.uid()
    )
);

drop policy if exists "Admin full access user plan assignments" on public.user_plan_assignments;
create policy "Admin full access user plan assignments"
on public.user_plan_assignments
for all
using ((auth.jwt() ->> 'role') = 'service_role')
with check ((auth.jwt() ->> 'role') = 'service_role');

drop policy if exists "Authenticated can read plans" on public.plans;
create policy "Authenticated can read plans"
on public.plans
for select
to authenticated
using (is_active = true);

drop policy if exists "Admin full access plans" on public.plans;
create policy "Admin full access plans"
on public.plans
for all
using ((auth.jwt() ->> 'role') = 'service_role')
with check ((auth.jwt() ->> 'role') = 'service_role');

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

    select up.id
    into v_profile_id
    from public.user_profiles up
    where up.auth_user_id = v_uid
    limit 1;

    if v_profile_id is not null then
        update public.user_profiles
        set
            email = v_email,
            full_name = coalesce(nullif(v_name, ''), full_name),
            avatar_url = coalesce(nullif(v_avatar_url, ''), avatar_url),
            last_login_at = now(),
            updated_at = now()
        where id = v_profile_id;

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

create or replace function public.assert_device_lock(
    p_uid uuid,
    p_device_fingerprint text,
    p_device_info jsonb default '{}'::jsonb,
    p_last_login_ip text default null,
    p_user_agent text default null
)
returns text
language plpgsql
security definer
set search_path = public
as $$
declare
    v_profile_id uuid;
    v_fp text := lower(trim(coalesce(p_device_fingerprint, '')));
    v_existing_fp text;
begin
    if p_uid is null or auth.uid() is distinct from p_uid then
        raise exception 'NOT_AUTHENTICATED';
    end if;

    if v_fp = '' then
        raise exception 'DEVICE_FINGERPRINT_REQUIRED';
    end if;

    v_profile_id := public.ensure_vidzeo_user_profile();

    select lower(trim(coalesce(up.device_fingerprint, '')))
    into v_existing_fp
    from public.user_profiles up
    where up.id = v_profile_id
    for update;

    if coalesce(v_existing_fp, '') = '' then
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
        where id = p_uid;

        insert into public.user_device_events (
            auth_user_id, user_profile_id, event_type, device_fingerprint,
            last_login_ip, user_agent, device_info
        )
        values (
            p_uid, v_profile_id, 'device_bound', v_fp,
            nullif(p_last_login_ip, ''), nullif(p_user_agent, ''), coalesce(p_device_info, '{}'::jsonb)
        );

        return v_fp;
    end if;

    if v_existing_fp <> v_fp then
        insert into public.user_device_events (
            auth_user_id, user_profile_id, event_type, device_fingerprint,
            last_login_ip, user_agent, device_info
        )
        values (
            p_uid, v_profile_id, 'device_mismatch', v_fp,
            nullif(p_last_login_ip, ''), nullif(p_user_agent, ''), coalesce(p_device_info, '{}'::jsonb)
        );
        raise exception 'DEVICE_MISMATCH';
    end if;

    update public.user_profiles
    set
        device_info = coalesce(p_device_info, device_info, '{}'::jsonb),
        last_login_ip = coalesce(nullif(p_last_login_ip, ''), last_login_ip),
        user_agent = coalesce(nullif(p_user_agent, ''), user_agent),
        last_login_at = now(),
        total_logins = greatest(coalesce(total_logins, 0), 0) + 1,
        updated_at = now()
    where id = v_profile_id;

    perform set_config('app.bypass_profile_guard', '1', true);
    update public.profiles
    set
        device_fingerprint = v_fp,
        device_info = coalesce(p_device_info, device_info, '{}'::jsonb),
        last_login_ip = coalesce(nullif(p_last_login_ip, ''), last_login_ip),
        user_agent = coalesce(nullif(p_user_agent, ''), user_agent),
        last_login_at = now(),
        total_logins = greatest(coalesce(total_logins, 0), 0) + 1,
        updated_at = now()
    where id = p_uid;

    insert into public.user_device_events (
        auth_user_id, user_profile_id, event_type, device_fingerprint,
        last_login_ip, user_agent, device_info
    )
    values (
        p_uid, v_profile_id, 'device_match', v_fp,
        nullif(p_last_login_ip, ''), nullif(p_user_agent, ''), coalesce(p_device_info, '{}'::jsonb)
    );

    return v_fp;
end;
$$;

create or replace function public.sync_assignment_state_for_auth_user(p_auth_user_id uuid)
returns void
language plpgsql
security definer
set search_path = public
as $$
declare
    v_profile_id uuid;
    v_email text;
    v_name text;
    v_avatar_url text;
    v_account_status text;
    v_active record;
    v_pending record;
    v_next_status text := 'free';
    v_next_plan text := 'free';
    v_next_requested_plan text := '';
begin
    if p_auth_user_id is null then
        return;
    end if;

    update public.user_plan_assignments
    set status = 'expired', updated_at = now()
    where status = 'active'
      and ends_at is not null
      and ends_at <= now()
      and user_profile_id in (
          select id from public.user_profiles where auth_user_id = p_auth_user_id
      );

    select id, email, full_name, avatar_url, account_status
    into v_profile_id, v_email, v_name, v_avatar_url, v_account_status
    from public.user_profiles
    where auth_user_id = p_auth_user_id
    limit 1;

    if not found then
        return;
    end if;

    select a.*, p.display_name as plan_display_name
    into v_active
    from public.user_plan_assignments a
    join public.plans p on p.plan_key = a.plan_key
    where a.user_profile_id = v_profile_id
      and a.status = 'active'
      and (a.starts_at is null or a.starts_at <= now())
      and (a.ends_at is null or a.ends_at > now())
      and p.is_active = true
    order by coalesce(a.starts_at, a.created_at) desc, a.created_at desc
    limit 1;

    select a.*
    into v_pending
    from public.user_plan_assignments a
    where a.user_profile_id = v_profile_id
      and a.status = 'pending'
    order by a.created_at desc
    limit 1;

    if lower(coalesce(v_account_status, 'pending')) = 'suspended' then
        v_next_status := 'suspended';
    elsif v_active.id is not null then
        v_next_status := 'active';
        v_next_plan := v_active.plan_key;
    elsif v_pending.id is not null then
        v_next_status := 'pending';
    else
        v_next_status := 'free';
    end if;

    if v_pending.id is not null then
        v_next_requested_plan := v_pending.plan_key;
    end if;

    perform set_config('app.bypass_profile_guard', '1', true);
    insert into public.profiles (id, email, name, avatar_url)
    values (
        p_auth_user_id,
        lower(coalesce(v_email, '')),
        coalesce(v_name, split_part(coalesce(v_email, ''), '@', 1)),
        coalesce(v_avatar_url, '')
    )
    on conflict (id) do update
    set
        email = excluded.email,
        name = excluded.name,
        avatar_url = excluded.avatar_url,
        plan = v_next_plan,
        status = v_next_status,
        requested_plan = v_next_requested_plan,
        activation_date = case when v_active.id is not null then coalesce(v_active.starts_at, v_active.created_at) else null end,
        expiry_date = case when v_active.id is not null then v_active.ends_at else null end,
        updated_at = now();
end;
$$;

create or replace function public.get_vidzeo_access_state(
    p_device_fingerprint text,
    p_device_info jsonb default '{}'::jsonb,
    p_last_login_ip text default null,
    p_user_agent text default null
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
    device_fingerprint text
)
language plpgsql
security definer
set search_path = public
as $$
declare
    v_uid uuid := auth.uid();
    v_profile_id uuid;
    v_profile record;
    v_assignment record;
    v_fp text;
begin
    if v_uid is null then
        raise exception 'NOT_AUTHENTICATED';
    end if;

    v_fp := public.assert_device_lock(v_uid, p_device_fingerprint, p_device_info, p_last_login_ip, p_user_agent);
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
        v_fp;
end;
$$;

create or replace function public.request_vidzeo_plan(
    p_plan_key text,
    p_device_fingerprint text,
    p_device_info jsonb default '{}'::jsonb,
    p_last_login_ip text default null,
    p_user_agent text default null
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
    device_fingerprint text
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

    perform public.assert_device_lock(v_uid, p_device_fingerprint, p_device_info, p_last_login_ip, p_user_agent);
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

create or replace function public.admin_reset_device_binding(
    p_user_id uuid,
    p_admin_note text default null
)
returns table(success boolean, status text, user_id uuid)
language plpgsql
security definer
set search_path = public
as $$
declare
    v_auth_user_id uuid;
begin
    if (auth.jwt() ->> 'role') <> 'service_role' then
        raise exception 'FORBIDDEN';
    end if;

    select up.auth_user_id
    into v_auth_user_id
    from public.user_profiles up
    where up.id = p_user_id or up.auth_user_id = p_user_id
    limit 1;

    if v_auth_user_id is null then
        raise exception 'PROFILE_NOT_FOUND';
    end if;

    update public.user_profiles
    set
        device_fingerprint = null,
        updated_at = now(),
        metadata = coalesce(metadata, '{}'::jsonb) || jsonb_build_object(
            'device_reset_at', now(),
            'device_reset_note', coalesce(nullif(p_admin_note, ''), 'manual_reset')
        )
    where auth_user_id = v_auth_user_id;

    perform set_config('app.bypass_profile_guard', '1', true);
    update public.profiles
    set
        device_fingerprint = null,
        updated_at = now(),
        metadata = coalesce(metadata, '{}'::jsonb) || jsonb_build_object(
            'device_reset_at', now(),
            'device_reset_note', coalesce(nullif(p_admin_note, ''), 'manual_reset')
        )
    where id = v_auth_user_id;

    insert into public.user_device_events (
        auth_user_id, user_profile_id, event_type, device_fingerprint, device_info
    )
    select v_auth_user_id, up.id, 'device_reset', null, jsonb_build_object('note', coalesce(nullif(p_admin_note, ''), 'manual_reset'))
    from public.user_profiles up
    where up.auth_user_id = v_auth_user_id
    limit 1;

    return query select true, 'DEVICE_RESET', p_user_id;
end;
$$;

revoke all on function public.ensure_vidzeo_user_profile() from public;
revoke all on function public.assert_device_lock(uuid, text, jsonb, text, text) from public;
revoke all on function public.get_vidzeo_access_state(text, jsonb, text, text) from public;
revoke all on function public.request_vidzeo_plan(text, text, jsonb, text, text) from public;
revoke all on function public.admin_reset_device_binding(uuid, text) from public;

grant execute on function public.get_vidzeo_access_state(text, jsonb, text, text) to authenticated;
grant execute on function public.request_vidzeo_plan(text, text, jsonb, text, text) to authenticated;
grant execute on function public.admin_reset_device_binding(uuid, text) to service_role;

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

notify pgrst, 'reload schema';
