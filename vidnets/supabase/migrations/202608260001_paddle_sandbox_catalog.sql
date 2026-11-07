-- Paddle sandbox catalog for Vidzeo's six stable plan keys.
-- These IDs are public catalog identifiers. Replace them with the approved
-- Paddle live IDs before switching the hosted page from sandbox to live.
update public.plans set
  billing_provider = 'paddle',
  provider_product_id = case plan_key
    when 'basic_monthly' then 'pro_01m0y7y26yfzhbssrv9nqg1qw6'
    when 'basic_yearly' then 'pro_01m0y7y26yfzhbssrv9nqg1qw6'
    when 'plus_monthly' then 'pro_01m0y85ctjwnwhrwgp9w6sf988'
    when 'plus_yearly' then 'pro_01m0y85ctjwnwhrwgp9w6sf988'
    when 'max_monthly' then 'pro_01m0y89826c7s2c93fzzj1332k'
    when 'max_yearly' then 'pro_01m0y89826c7s2c93fzzj1332k'
  end,
  provider_variant_id = case plan_key
    when 'basic_monthly' then 'pri_01m0y80jbbvabegg0km30t8z5e'
    when 'basic_yearly' then 'pri_01m0y8453aqtzs93nv03m56act'
    when 'plus_monthly' then 'pri_01m0y87715v0r9eq3ww330nr23'
    when 'plus_yearly' then 'pri_01m0y87v4mzz8svp6w7az7eyh6'
    when 'max_monthly' then 'pri_01m0y8ajhke306q34x327gcwy5'
    when 'max_yearly' then 'pri_01m0y8b764hbe0h4rkftc7f6t5'
  end,
  updated_at = now()
where plan_key in (
  'basic_monthly', 'basic_yearly', 'plus_monthly',
  'plus_yearly', 'max_monthly', 'max_yearly'
);
