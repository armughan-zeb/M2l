create extension if not exists pgcrypto;

create table if not exists public.plans (
    plan_key text primary key,
    name text not null,
    display_name text not null,
    billing_period text not null check (billing_period in ('monthly', 'yearly')),
    price_monthly_display numeric(10, 2) not null,
    price_label text not null,
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

create table if not exists public.admin_allowlist (
    email text primary key,
    role text not null default 'admin',
    is_active boolean not null default true,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table if not exists public.admin_audit_logs (
    id uuid primary key default gen_random_uuid(),
    actor_user_id uuid,
    actor_email text,
    target_user_profile_id uuid references public.user_profiles(id) on delete set null,
    target_email text,
    action text not null,
    details jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now()
);

create or replace function public.set_updated_at()
returns trigger
language plpgsql
as $$
begin
    new.updated_at = now();
    return new;
end;
$$;

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

drop trigger if exists trg_admin_allowlist_updated_at on public.admin_allowlist;
create trigger trg_admin_allowlist_updated_at
before update on public.admin_allowlist
for each row
execute function public.set_updated_at();

create index if not exists idx_user_profiles_auth_user_id on public.user_profiles(auth_user_id);
create index if not exists idx_user_profiles_account_status on public.user_profiles(account_status);
create index if not exists idx_user_plan_assignments_user_profile_id on public.user_plan_assignments(user_profile_id);
create index if not exists idx_user_plan_assignments_status on public.user_plan_assignments(status);
create index if not exists idx_admin_audit_logs_target_user_profile_id on public.admin_audit_logs(target_user_profile_id);

alter table public.plans enable row level security;
alter table public.user_profiles enable row level security;
alter table public.user_plan_assignments enable row level security;
alter table public.admin_allowlist enable row level security;
alter table public.admin_audit_logs enable row level security;

drop policy if exists "users can read active plans" on public.plans;
create policy "users can read active plans"
on public.plans
for select
to authenticated
using (is_active = true);

drop policy if exists "users can read own profile" on public.user_profiles;
create policy "users can read own profile"
on public.user_profiles
for select
to authenticated
using (auth.uid() = auth_user_id);

drop policy if exists "users can read own plan assignments" on public.user_plan_assignments;
create policy "users can read own plan assignments"
on public.user_plan_assignments
for select
to authenticated
using (
    exists (
        select 1
        from public.user_profiles p
        where p.id = user_profile_id
          and p.auth_user_id = auth.uid()
    )
);

insert into public.plans (
    plan_key,
    name,
    display_name,
    billing_period,
    price_monthly_display,
    price_label,
    video_limit_minutes,
    feature_summary,
    sort_order
) values
(
    'basic_monthly',
    'Basic',
    'Basic Monthly',
    'monthly',
    19.00,
    '$19 / Month',
    15,
    '["Videos up to 15 minutes long","Unlimited videos","1080p exports","Unlimited free voiceovers","AI + Stock + Real Media"]'::jsonb,
    1
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
    2
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
    3
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
    4
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
    5
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
    6
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
    is_active = true;
