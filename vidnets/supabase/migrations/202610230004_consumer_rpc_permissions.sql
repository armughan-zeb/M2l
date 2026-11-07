begin;
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
    v_limit integer := 1;
    v_event_type text := 'device_match';
begin
    if v_uid is null then
        raise exception 'NOT_AUTHENTICATED';
    end if;
    if v_fp = '' then
        raise exception 'DEVICE_FINGERPRINT_REQUIRED';
    end if;

    v_profile_id := public.ensure_vidzeo_user_profile();
    select case when a.plan_key in ('free_trial','base_monthly','base_yearly','basic_monthly','basic_yearly') then 1 else 2 end
    into v_limit from public.user_plan_assignments a join public.plans p using(plan_key)
    where a.user_profile_id=v_profile_id and a.status='active' and p.is_active
      and (a.starts_at is null or a.starts_at<=now()) and (a.ends_at is null or a.ends_at>now())
    order by (a.plan_key='lifetime_pro') desc,(a.plan_key<>'free_trial') desc,coalesce(a.starts_at,a.created_at) desc limit 1;
    v_limit:=coalesce(v_limit,1);


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


-- Server/admin and trigger functions must not inherit consumer execution grants.
do $$ declare f record; begin
  for f in select p.oid::regprocedure as signature,p.proname
      from pg_proc p join pg_namespace n on n.oid=p.pronamespace
      where n.nspname='public' and p.prosecdef loop
    execute format('revoke all on function %s from public,anon,authenticated',f.signature);
    if f.proname in ('ensure_vidzeo_user_profile','assert_vidzeo_device_lock','get_vidzeo_access_state',
        'request_vidzeo_plan','ensure_vidnets_free_access','get_vidnets_export_usage','reserve_vidnets_export') then
      execute format('grant execute on function %s to authenticated',f.signature);
    end if;
    if f.proname='finish_vidnets_export' then
      execute format('grant execute on function %s to anon,authenticated',f.signature);
    end if;
    execute format('grant execute on function %s to service_role',f.signature);
  end loop;
end $$;
alter default privileges in schema public revoke execute on functions from public,anon,authenticated;
notify pgrst,'reload schema';
commit;
