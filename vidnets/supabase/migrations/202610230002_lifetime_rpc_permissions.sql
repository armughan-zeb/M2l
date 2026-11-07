-- Supabase's default grants can include anon/authenticated explicitly, independently of PUBLIC.
revoke all on function public.apply_polar_lifetime_order(uuid,text,text,text,text,text,integer,text,jsonb) from public, anon, authenticated;
revoke all on function public.refund_polar_lifetime_order(text) from public, anon, authenticated;
grant execute on function public.apply_polar_lifetime_order(uuid,text,text,text,text,text,integer,text,jsonb) to service_role;
grant execute on function public.refund_polar_lifetime_order(text) to service_role;
notify pgrst, 'reload schema';
