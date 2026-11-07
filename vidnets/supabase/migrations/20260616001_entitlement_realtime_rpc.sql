-- Keep desktop clients, packaged builds, and admin decisions on one entitlement contract.
-- Active access and pending requested plan are returned separately so upgrade requests
-- cannot disappear behind an existing active assignment.

drop function if exists public.get_vidzeo_access_state(text, jsonb, text, text);
drop function if exists public.request_vidzeo_plan(text, text, jsonb, text, text);

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
    pending_plan_key text,
    pending_plan_name text,
    access_updated_at timestamptz,
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
    v_active record;
    v_pending record;
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
        a.created_at,
        a.updated_at,
        p.display_name,
        p.name as plan_tier_name,
        p.billing_period,
        p.video_limit_minutes,
        p.script_word_limit,
        p.price_monthly_display
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

    select
        a.id,
        a.plan_key,
        a.status,
        a.starts_at,
        a.ends_at,
        a.created_at,
        a.updated_at,
        p.display_name,
        p.name as plan_tier_name,
        p.billing_period,
        p.video_limit_minutes,
        p.script_word_limit,
        p.price_monthly_display
    into v_pending
    from public.user_plan_assignments a
    join public.plans p on p.plan_key = a.plan_key
    where a.user_profile_id = v_profile_id
      and a.status = 'pending'
      and p.is_active = true
    order by a.created_at desc
    limit 1;

    return query
    select
        v_profile.id,
        v_uid,
        v_profile.email,
        coalesce(v_profile.full_name, v_profile.email, 'Vidzeo Creator'),
        coalesce(v_profile.avatar_url, ''),
        coalesce(v_active.plan_key, v_pending.plan_key),
        coalesce(v_active.display_name, v_pending.display_name, 'No active plan'),
        coalesce(v_active.status, v_pending.status, 'pending'),
        v_pending.plan_key,
        v_pending.display_name,
        greatest(
            coalesce(v_profile.updated_at, '-infinity'::timestamptz),
            coalesce(v_active.updated_at, v_active.created_at, '-infinity'::timestamptz),
            coalesce(v_pending.updated_at, v_pending.created_at, '-infinity'::timestamptz)
        ),
        case
            when lower(coalesce(v_profile.account_status, 'pending')) = 'suspended' then 'suspended'
            when v_active.id is not null then 'active'
            else 'pending'
        end,
        case
            when lower(coalesce(v_profile.account_status, 'pending')) = 'suspended'
                then 'Your account is suspended. Contact support or an administrator.'
            when v_active.id is not null and v_pending.id is not null
                then 'Your studio access is active. Your requested plan is waiting for admin approval.'
            when v_active.id is not null
                then 'Your studio access is active.'
            else 'Pick a plan and wait for admin activation to unlock the studio.'
        end,
        coalesce(v_profile.account_status, 'pending'),
        coalesce(v_active.billing_period, v_pending.billing_period),
        coalesce(v_active.video_limit_minutes, v_pending.video_limit_minutes),
        coalesce(v_active.script_word_limit, v_pending.script_word_limit, coalesce(v_active.video_limit_minutes, v_pending.video_limit_minutes) * 156),
        coalesce(v_active.price_monthly_display, v_pending.price_monthly_display),
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
    pending_plan_key text,
    pending_plan_name text,
    access_updated_at timestamptz,
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
    v_email text;
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

    if exists (
        select 1
        from public.user_plan_assignments a
        where a.user_profile_id = v_profile_id
          and a.plan_key = v_plan_key
          and a.status = 'active'
          and (a.starts_at is null or a.starts_at <= now())
          and (a.ends_at is null or a.ends_at > now())
    ) then
        return query
        select *
        from public.get_vidzeo_access_state(
            p_device_fingerprint,
            p_device_info,
            p_last_login_ip,
            p_user_agent
        );
        return;
    end if;

    update public.user_plan_assignments
    set status = 'revoked', updated_at = now()
    where user_profile_id = v_profile_id
      and status = 'pending';

    select up.email
    into v_email
    from public.user_profiles up
    where up.id = v_profile_id;

    insert into public.user_plan_assignments (
        user_profile_id,
        plan_key,
        status,
        requested_by_email,
        note
    )
    values (
        v_profile_id,
        v_plan_key,
        'pending',
        v_email,
        'Requested inside the Vidnets Studio desktop app.'
    );

    insert into public.admin_audit_logs (
        actor_user_id,
        actor_email,
        target_user_profile_id,
        target_email,
        action,
        details
    )
    values (
        v_profile_id,
        v_email,
        v_profile_id,
        v_email,
        'plan.requested',
        jsonb_build_object('plan_key', v_plan_key)
    );

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

revoke all on function public.get_vidzeo_access_state(text, jsonb, text, text) from public;
revoke all on function public.request_vidzeo_plan(text, text, jsonb, text, text) from public;
grant execute on function public.get_vidzeo_access_state(text, jsonb, text, text) to authenticated;
grant execute on function public.request_vidzeo_plan(text, text, jsonb, text, text) to authenticated;

notify pgrst, 'reload schema';
