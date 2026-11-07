import { createClient } from 'https://esm.sh/@supabase/supabase-js@2';
import { verifyPolarPlan } from '../_shared/polar-catalog.ts';

const corsHeaders = {
  'Access-Control-Allow-Origin': 'https://vidnets.com',
  'Access-Control-Allow-Headers': 'authorization, x-client-info, apikey, content-type, x-supabase-api-version',
  'Access-Control-Allow-Methods': 'POST, OPTIONS',
};

function json(body: Record<string, unknown>, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { ...corsHeaders, 'Content-Type': 'application/json' },
  });
}

function env(name: string): string {
  return (Deno.env.get(name) || '').trim();
}

Deno.serve(async (request) => {
  if (request.method === 'OPTIONS') return new Response('ok', { headers: corsHeaders });
  if (request.method !== 'POST') return json({ error: 'Method not allowed' }, 405);

  const supabaseUrl = env('SUPABASE_URL');
  const anonKey = env('SUPABASE_ANON_KEY');
  const serviceRoleKey = env('SUPABASE_SERVICE_ROLE_KEY');
  const polarAccessToken = env('POLAR_ACCESS_TOKEN');
  const polarApiBase = env('POLAR_API_BASE_URL') || 'https://api.polar.sh';
  if (!supabaseUrl || !anonKey || !serviceRoleKey || !polarAccessToken) {
    return json({ error: 'Billing is not configured on the server.' }, 503);
  }

  const authorization = request.headers.get('Authorization') || '';
  const accessToken = authorization.replace(/^Bearer\s+/i, '').trim();
  if (!accessToken) return json({ error: 'Sign in with Google before checkout.' }, 401);

  const authClient = createClient(supabaseUrl, anonKey, {
    global: { headers: { Authorization: `Bearer ${accessToken}` } },
    auth: { persistSession: false, autoRefreshToken: false },
  });
  const { data: userData, error: userError } = await authClient.auth.getUser(accessToken);
  if (userError || !userData.user?.id || !userData.user.email) {
    return json({ error: 'Your sign-in session is no longer valid. Sign in again.' }, 401);
  }

  const payload = await request.json().catch(() => ({}));
  const planKey = String(payload?.plan_key || '').trim().toLowerCase();
  if (planKey !== 'lifetime_pro') {
    return json({ error: 'Only Lifetime Pro is available for purchase.' }, 400);
  }

  const adminClient = createClient(supabaseUrl, serviceRoleKey, {
    auth: { persistSession: false, autoRefreshToken: false },
  });
  const { data: plans, error: planError } = await adminClient
    .from('plans')
    .select('plan_key,display_name,billing_period,price_monthly_display,billing_provider,provider_product_id,provider_variant_id,is_active,is_available_for_purchase')
    .eq('plan_key', planKey)
    .eq('billing_provider', 'polar')
    .eq('is_active', true)
    .eq('is_available_for_purchase', true)
    .limit(1);
  const plan = plans?.[0];
  const productId = String(plan?.provider_product_id || plan?.provider_variant_id || '').trim();
  if (planError || !plan || !productId || !/^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(productId)) {
    return json({ error: 'This plan is not connected to a Polar product yet.' }, 503);
  }

  const {data: profile} = await adminClient.from('user_profiles').select('id,account_status').eq('auth_user_id',userData.user.id).maybeSingle();
  if (profile?.account_status === 'suspended') return json({error:'This account is suspended. Contact support.'},403);
  if (profile?.id) {
    const {data: lifetime,error: lifetimeError} = await adminClient.from('user_plan_assignments').select('id')
      .eq('user_profile_id',profile.id).eq('plan_key','lifetime_pro').eq('status','active').is('ends_at',null).limit(1);
    if (lifetimeError) return json({error:'Could not verify your existing plan.'},503);
    if (lifetime?.length) return json({error:'You already have Lifetime Pro. Sign in to the desktop app with this account to use it.'},409);
  }
  try { await verifyPolarPlan(plan); } catch (_) { return json({error:'This plan is being checked against the payment catalog. Please try another available plan or contact support.'},503); }
  {
    // Lifetime buyers must first stop renewal of an existing subscription.
    // Cancel-at-period-end is enough; they need not wait for access to expire.
    const { data: recurring, error: recurringError } = await adminClient
      .from('billing_subscriptions')
      .select('id,cancel_at_period_end')
      .eq('auth_user_id', userData.user.id)
      .in('status', ['active', 'on_trial']);
    if (recurringError) return json({ error: 'Could not verify your existing subscription.' }, 503);
    if ((recurring || []).some((subscription: any) => !subscription.cancel_at_period_end)) {
      return json({ error: 'Please manage or cancel renewal of your existing subscription in Polar before purchasing another plan. Your current access continues through its paid period.' }, 409);
    }
  }

  const checkoutResponse = await fetch(`${polarApiBase.replace(/\/$/, '')}/v1/checkouts`, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${polarAccessToken}`,
      Accept: 'application/json',
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      // Polar currently accepts the stable single-product shape here. It is
      // also compatible with older API versions while still selecting the
      // product's first fixed recurring price.
      products: [productId],
      metadata: { auth_user_id: userData.user.id, plan_key: planKey },
      customer_email: userData.user.email,
      customer_name: userData.user.user_metadata?.full_name || userData.user.user_metadata?.name || null,
      external_customer_id: userData.user.id,
      success_url: 'https://vidnets.com/?checkout=success&checkout_id={CHECKOUT_ID}',
      return_url: 'https://vidnets.com/#pricing',
      allow_discount_codes: planKey !== 'lifetime_pro',
    }),
  });
  const checkoutBody = await checkoutResponse.json().catch(() => ({}));
  if (!checkoutResponse.ok) {
    console.error('Polar checkout creation failed', checkoutResponse.status, checkoutBody?.detail || checkoutBody?.message || '');
    return json({ error: 'Secure checkout could not be created. Please try again.' }, 502);
  }

  const checkoutUrl = checkoutBody?.url || checkoutBody?.checkout_url;
  if (!checkoutUrl) return json({ error: 'Polar returned no checkout URL.' }, 502);
  return json({ checkout_url: checkoutUrl, plan_key: planKey, provider: 'polar' });
});
