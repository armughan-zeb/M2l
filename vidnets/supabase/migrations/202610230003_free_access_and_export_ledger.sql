-- No-card starter access and atomic, account-wide export allowances.
begin;
insert into public.plans (
  plan_key,name,display_name,billing_period,price_monthly_display,price_label,
  video_limit_minutes,script_word_limit,resolution_label,unlimited_videos,
  unlimited_voiceovers,media_sources_label,feature_summary,metadata,sort_order,
  is_active,is_available_for_purchase,billing_provider,currency_code,price_cents
) values (
  'free_trial','Free','Free Starter','monthly',0,'Free · no card required',1,156,
  '1080p Exports',false,true,'YouTube + Web + Stock',
  '["5 exports per calendar month","Up to 1 minute per video","Remotion editing and captions","No card or automatic billing"]',
  '{"monthly_export_limit":5,"is_free":true,"device_limit":1}',0,true,false,'internal','USD',0
) on conflict(plan_key) do update set video_limit_minutes=1,script_word_limit=156,
  metadata=excluded.metadata,feature_summary=excluded.feature_summary,is_active=true,
  is_available_for_purchase=false,price_cents=0,updated_at=now();

-- Repair equivalent yearly allowances without reducing any existing entitlement.
update public.plans set video_limit_minutes=coalesce(video_limit_minutes,5),
  script_word_limit=coalesce(script_word_limit,780),
  metadata=coalesce(metadata,'{}')||'{"monthly_export_limit":10}'::jsonb where plan_key='base_yearly';
update public.plans set metadata=coalesce(metadata,'{}')||'{"monthly_export_limit":60}'::jsonb where plan_key='basic_yearly';
update public.plans set video_limit_minutes=greatest(coalesce(video_limit_minutes,0),35),
  script_word_limit=greatest(coalesce(script_word_limit,0),5460) where plan_key='plus_yearly';

create table if not exists public.vidnets_export_ledger (
  id uuid primary key default gen_random_uuid(),
  auth_user_id uuid not null references auth.users(id) on delete cascade,
  user_profile_id uuid not null references public.user_profiles(id) on delete cascade,
  request_id text not null check(length(request_id) between 1 and 160),
  plan_key text not null references public.plans(plan_key),
  usage_month date not null default date_trunc('month',now() at time zone 'UTC')::date,
  status text not null default 'reserved' check(status in ('reserved','completed','failed','expired')),
  -- This capability can only settle this single reservation; it is never an account credential.
  receipt text not null unique default encode(gen_random_bytes(32),'hex'),
  duration_seconds numeric not null default 0 check(duration_seconds>=0),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  completed_at timestamptz,
  unique(auth_user_id,request_id)
);
create index if not exists vidnets_exports_user_month on public.vidnets_export_ledger(auth_user_id,usage_month,status);
create index if not exists vidnets_exports_completed on public.vidnets_export_ledger(completed_at) where status='completed';
alter table public.vidnets_export_ledger enable row level security;
revoke all on public.vidnets_export_ledger from public,anon,authenticated;
grant all on public.vidnets_export_ledger to service_role;

create or replace function public.ensure_vidnets_free_access() returns uuid
language plpgsql security definer set search_path=public,pg_temp as $$
declare v_uid uuid:=auth.uid(); v_id uuid; v_status text;
begin
  if v_uid is null then raise exception 'NOT_AUTHENTICATED'; end if;
  perform pg_advisory_xact_lock(hashtextextended(v_uid::text,801));
  v_id:=public.ensure_vidzeo_user_profile();
  select account_status into v_status from public.user_profiles where id=v_id;
  if v_status='suspended' then return v_id; end if;
  if not exists(select 1 from public.user_plan_assignments a join public.plans p using(plan_key)
    where a.user_profile_id=v_id and a.status='active' and p.is_active
      and (a.starts_at is null or a.starts_at<=now()) and (a.ends_at is null or a.ends_at>now())) then
    insert into public.user_plan_assignments(user_profile_id,plan_key,status,starts_at,note)
      values(v_id,'free_trial','active',now(),'No-card Free Starter access');
    update public.user_profiles set account_status='active' where id=v_id and account_status<>'suspended';
  end if;
  return v_id;
end $$;

create or replace function public.get_vidnets_export_usage() returns jsonb
language plpgsql security definer set search_path=public,pg_temp as $$
declare v_uid uuid:=auth.uid(); v_plan record; v_limit integer; v_used integer; v_reserved integer;
begin
  if v_uid is null then raise exception 'NOT_AUTHENTICATED'; end if;
  select p.* into v_plan from public.user_profiles u
    join public.user_plan_assignments a on a.user_profile_id=u.id join public.plans p using(plan_key)
    where u.auth_user_id=v_uid and u.account_status<>'suspended' and a.status='active' and p.is_active
      and (a.starts_at is null or a.starts_at<=now()) and (a.ends_at is null or a.ends_at>now())
    order by (a.plan_key='lifetime_pro') desc,(a.plan_key<>'free_trial') desc,coalesce(a.starts_at,a.created_at) desc limit 1;
  if v_plan.plan_key is null then raise exception 'ACTIVE_ACCESS_REQUIRED'; end if;
  v_limit:=nullif(v_plan.metadata->>'monthly_export_limit','')::integer;
  select count(*) filter(where status='completed'),count(*) filter(where status='reserved' and updated_at>now()-interval '72 hours')
    into v_used,v_reserved from public.vidnets_export_ledger
    where auth_user_id=v_uid and usage_month=date_trunc('month',now() at time zone 'UTC')::date;
  return jsonb_build_object('month',to_char(now() at time zone 'UTC','YYYY-MM'),'used',v_used,'reserved',v_reserved,
    'limit',v_limit,'remaining',case when v_limit is null then null else greatest(0,v_limit-v_used-v_reserved) end,
    'is_unlimited',v_limit is null,'plan_key',v_plan.plan_key,'max_video_minutes',v_plan.video_limit_minutes);
end $$;

create or replace function public.reserve_vidnets_export(p_request_id text,p_duration_seconds numeric default 0) returns jsonb
language plpgsql security definer set search_path=public,pg_temp as $$
declare v_uid uuid:=auth.uid(); v_profile uuid; v_usage jsonb; v_row public.vidnets_export_ledger%rowtype;
begin
  if v_uid is null then raise exception 'NOT_AUTHENTICATED'; end if;
  if length(coalesce(p_request_id,'')) not between 1 and 160 or p_duration_seconds<0 then raise exception 'INVALID_EXPORT_REQUEST'; end if;
  perform pg_advisory_xact_lock(hashtextextended(v_uid::text,802));
  v_usage:=public.get_vidnets_export_usage();
  if (v_usage->>'max_video_minutes')::numeric is not null and
      p_duration_seconds>(v_usage->>'max_video_minutes')::numeric*60 then
    return v_usage||jsonb_build_object('allowed',false,'reason','duration');
  end if;
  select * into v_row from public.vidnets_export_ledger where auth_user_id=v_uid and request_id=p_request_id;
  if v_row.status='completed' then
    return v_usage||jsonb_build_object('allowed',false,'reason','already_completed');
  end if;
  if v_row.status='reserved' and v_row.updated_at>now()-interval '72 hours' then
    return v_usage||jsonb_build_object('allowed',true,'receipt',v_row.receipt,'request_id',p_request_id);
  end if;
  if (v_usage->>'remaining')::integer is not null and (v_usage->>'remaining')::integer<=0 then
    return v_usage||jsonb_build_object('allowed',false,'reason','monthly_quota');
  end if;
  select id into v_profile from public.user_profiles where auth_user_id=v_uid;
  insert into public.vidnets_export_ledger(auth_user_id,user_profile_id,request_id,plan_key,duration_seconds)
    values(v_uid,v_profile,p_request_id,v_usage->>'plan_key',coalesce(p_duration_seconds,0))
    on conflict(auth_user_id,request_id) do update set status='reserved',updated_at=now(),
      usage_month=date_trunc('month',now() at time zone 'UTC')::date,plan_key=excluded.plan_key
    returning * into v_row;
  return public.get_vidnets_export_usage()||jsonb_build_object('allowed',true,'receipt',v_row.receipt,'request_id',p_request_id);
end $$;

create or replace function public.finish_vidnets_export(p_receipt text,p_success boolean,p_duration_seconds numeric default 0) returns boolean
language plpgsql security definer set search_path=public,pg_temp as $$
begin
  if p_receipt !~ '^[a-f0-9]{64}$' then return false; end if;
  update public.vidnets_export_ledger set status=case when p_success then 'completed' else 'failed' end,
    duration_seconds=greatest(0,coalesce(p_duration_seconds,0)),updated_at=now(),
    completed_at=case when p_success then now() else null end
    where receipt=p_receipt and status<>'completed';
  return exists(select 1 from public.vidnets_export_ledger where receipt=p_receipt);
end $$;

create or replace function public.get_vidnets_admin_metrics() returns jsonb
language sql security definer set search_path=public,pg_temp as $$
select jsonb_build_object(
 'exports_total',(select count(*) from public.vidnets_export_ledger where status='completed'),
 'exports_month',(select count(*) from public.vidnets_export_ledger where status='completed' and completed_at>=date_trunc('month',now() at time zone 'UTC') at time zone 'UTC'),
 'exports_failed',(select count(*) from public.vidnets_export_ledger where status='failed'),
 'exports_processing',(select count(*) from public.vidnets_export_ledger where status='reserved' and updated_at>now()-interval '72 hours'),
 'active_devices',(select count(*) from public.user_devices where status='active'),
 'free_accounts',(select count(distinct a.user_profile_id) from public.user_plan_assignments a join public.user_profiles u on u.id=a.user_profile_id where a.status='active' and a.plan_key='free_trial' and u.account_status<>'suspended'
   and not exists(select 1 from public.user_plan_assignments paid where paid.user_profile_id=a.user_profile_id and paid.status='active' and paid.plan_key<>'free_trial' and (paid.ends_at is null or paid.ends_at>now()))),
 'lifetime_accounts',(select count(distinct user_profile_id) from public.user_plan_assignments where status='active' and plan_key='lifetime_pro'),
 'tracking_started',(select min(created_at) from public.vidnets_export_ledger)
); $$;

revoke all on function public.ensure_vidnets_free_access() from public,anon,authenticated;
revoke all on function public.get_vidnets_export_usage() from public,anon,authenticated;
revoke all on function public.reserve_vidnets_export(text,numeric) from public,anon,authenticated;
revoke all on function public.finish_vidnets_export(text,boolean,numeric) from public,anon,authenticated;
revoke all on function public.get_vidnets_admin_metrics() from public,anon,authenticated;
grant execute on function public.ensure_vidnets_free_access(),public.get_vidnets_export_usage(),public.reserve_vidnets_export(text,numeric) to authenticated;
grant execute on function public.finish_vidnets_export(text,boolean,numeric) to anon,authenticated,service_role;
grant execute on function public.get_vidnets_admin_metrics() to service_role;
notify pgrst,'reload schema';
commit;
