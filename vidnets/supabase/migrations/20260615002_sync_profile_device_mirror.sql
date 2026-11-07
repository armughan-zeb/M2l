-- Keep the compatibility profiles table mirrored from canonical user_devices.

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
    v_device public.user_devices%rowtype;
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

    select *
    into v_device
    from public.user_devices ud
    where ud.user_profile_id = v_profile.id
      and ud.status = 'active'
    order by ud.last_seen_at desc
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
        device_fingerprint = coalesce(v_device.device_fingerprint, public.profiles.device_fingerprint),
        device_info = coalesce(v_device.device_info, public.profiles.device_info, '{}'::jsonb),
        last_login_ip = coalesce(v_device.last_login_ip, public.profiles.last_login_ip),
        user_agent = coalesce(v_device.user_agent, public.profiles.user_agent),
        last_login_at = coalesce(v_device.last_seen_at, public.profiles.last_login_at),
        updated_at = now()
    where id = p_auth_user_id;
end;
$$;

do $$
begin
    perform set_config('app.bypass_profile_guard', '1', true);

    update public.profiles p
    set
        device_fingerprint = ud.device_fingerprint,
        device_info = coalesce(ud.device_info, p.device_info, '{}'::jsonb),
        last_login_ip = coalesce(ud.last_login_ip, p.last_login_ip),
        user_agent = coalesce(ud.user_agent, p.user_agent),
        last_login_at = coalesce(ud.last_seen_at, p.last_login_at),
        updated_at = now()
    from public.user_profiles up
    join lateral (
        select *
        from public.user_devices d
        where d.user_profile_id = up.id
          and d.status = 'active'
        order by d.last_seen_at desc
        limit 1
    ) ud on true
    where p.id = up.auth_user_id
      and up.auth_user_id is not null;
end;
$$;

revoke all on function public.sync_assignment_state_for_auth_user(uuid) from public;
grant execute on function public.sync_assignment_state_for_auth_user(uuid) to service_role;

notify pgrst, 'reload schema';
