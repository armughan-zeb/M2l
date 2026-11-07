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
