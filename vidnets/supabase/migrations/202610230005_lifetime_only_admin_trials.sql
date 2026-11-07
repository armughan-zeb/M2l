-- Public sales: Lifetime Pro only. Preserve paid assignments and owner-granted trials.
begin;
update public.plans set is_available_for_purchase=false,updated_at=now()
where plan_key<>'lifetime_pro';
update public.plans set name='Admin Trial',display_name='Admin Trial',
  price_label='Private trial · administrator invitation only',
  metadata=coalesce(metadata,'{}')||'{"admin_only":true,"monthly_export_limit":5,"device_limit":1}'::jsonb,
  feature_summary='["Granted only by the administrator","5 exports per UTC calendar month","Up to 1 minute per video"]'::jsonb,
  updated_at=now() where plan_key='free_trial';

-- Old desktop versions may still call this RPC. It now only creates a profile.
create or replace function public.ensure_vidnets_free_access() returns uuid
language plpgsql security definer set search_path=public,pg_temp as $$
begin
  if auth.uid() is null then raise exception 'NOT_AUTHENTICATED'; end if;
  return public.ensure_vidzeo_user_profile();
end $$;

-- Retire only identifiable automatic grants, never a grant issued by an admin.
do $$ declare profile record; begin
  for profile in
    with removed as (
      update public.user_plan_assignments set status='revoked',ends_at=now(),updated_at=now(),
        note='Automatic Free Starter access retired; Lifetime Pro purchase required.'
      where plan_key='free_trial' and status in ('active','pending')
        and note='No-card Free Starter access' and assigned_by_user_id is null
        and coalesce(assigned_by_email,'')=''
      returning user_profile_id
    ) select distinct u.auth_user_id from removed r join public.user_profiles u on u.id=r.user_profile_id
  loop
    perform public.sync_assignment_state_for_auth_user(profile.auth_user_id);
  end loop;
end $$;

-- Desktop plan requests are not a way to request a private trial or a retired plan.
do $$ declare definition text; begin
  select pg_get_functiondef('public.request_vidzeo_plan(text,text,jsonb,text,text)'::regprocedure) into definition;
  definition:=replace(definition,'if v_uid is null then',
    'if v_plan_key <> ''lifetime_pro'' then raise exception ''Only Lifetime Pro is available for purchase. Trials require an administrator.''; end if; if v_uid is null then');
  execute definition;
end $$;
commit;
