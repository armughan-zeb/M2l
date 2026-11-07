import { createClient } from 'https://esm.sh/@supabase/supabase-js@2';

const corsHeaders = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Headers': 'content-type, paddle-signature',
};
const encoder = new TextEncoder();

function env(name: string): string { return (Deno.env.get(name) || '').trim(); }
function json(body: Record<string, unknown>, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { ...corsHeaders, 'Content-Type': 'application/json' },
  });
}
function hex(bytes: Uint8Array): string {
  return Array.from(bytes).map((value) => value.toString(16).padStart(2, '0')).join('');
}
async function hmac(secret: string, value: string): Promise<string> {
  const key = await crypto.subtle.importKey('raw', encoder.encode(secret), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']);
  return hex(new Uint8Array(await crypto.subtle.sign('HMAC', key, encoder.encode(value))));
}
function safeEqual(left: string, right: string): boolean {
  if (!left || left.length !== right.length) return false;
  let result = 0;
  for (let index = 0; index < left.length; index += 1) result |= left.charCodeAt(index) ^ right.charCodeAt(index);
  return result === 0;
}
function signatureParts(header: string): { timestamp: string; hash: string } {
  const parts = header.split(';').reduce<Record<string, string>>((acc, part) => {
    const [key, ...value] = part.trim().split('=');
    if (key && value.length) acc[key] = value.join('=');
    return acc;
  }, {});
  return { timestamp: parts.ts || '', hash: parts.h1 || '' };
}
function normalizeStatus(eventName: string, rawStatus: unknown): string {
  const status = String(rawStatus || '').trim().toLowerCase();
  if (eventName.includes('canceled') || eventName.includes('cancelled')) return 'cancelled';
  if (status === 'trialing' || status === 'trial') return 'on_trial';
  if (status === 'active') return 'active';
  if (status === 'past_due') return 'past_due';
  if (status === 'paused') return 'paused';
  if (status === 'canceled' || status === 'cancelled') return 'cancelled';
  if (status === 'expired') return 'expired';
  return 'pending';
}

Deno.serve(async (request) => {
  if (request.method === 'OPTIONS') return new Response('ok', { headers: corsHeaders });
  if (request.method !== 'POST') return json({ error: 'Method not allowed' }, 405);

  const supabaseUrl = env('SUPABASE_URL');
  const serviceRoleKey = env('SUPABASE_SERVICE_ROLE_KEY');
  const webhookSecret = env('PADDLE_WEBHOOK_SECRET');
  if (!supabaseUrl || !serviceRoleKey || !webhookSecret) return json({ error: 'Webhook is not configured.' }, 503);

  // Paddle signs the exact raw body as `${timestamp}:${body}`. Do not parse or
  // re-serialize before checking the signature.
  const rawBody = await request.text();
  const header = request.headers.get('paddle-signature') || request.headers.get('Paddle-Signature') || '';
  const { timestamp, hash } = signatureParts(header);
  const timestampNumber = Number(timestamp);
  if (!timestamp || !hash || !Number.isFinite(timestampNumber) || Math.abs(Date.now() / 1000 - timestampNumber) > 300) {
    return json({ error: 'Invalid or expired signature.' }, 401);
  }
  const expected = await hmac(webhookSecret, `${timestamp}:${rawBody}`);
  if (!safeEqual(hash, expected)) return json({ error: 'Invalid signature.' }, 401);

  let payload: Record<string, any>;
  try { payload = JSON.parse(rawBody); } catch { return json({ error: 'Invalid JSON payload.' }, 400); }
  const eventName = String(payload?.event_type || payload?.eventName || 'unknown').trim().toLowerCase();
  const data = payload?.data || {};
  const providerEventId = String(payload?.event_id || payload?.id || `${eventName}:${data?.id || rawBody.length}`).trim();
  const custom = data?.custom_data || {};
  const authUserId = String(custom?.auth_user_id || '').trim();
  const customPlanKey = String(custom?.plan_key || '').trim().toLowerCase();
  const priceId = String(data?.items?.[0]?.price?.id || data?.items?.[0]?.price_id || '').trim();
  const subscriptionId = String(data?.id || '').trim();
  const customerId = String(data?.customer_id || '').trim();
  const email = String(custom?.email || data?.customer?.email || data?.email || '').trim().toLowerCase();

  const admin = createClient(supabaseUrl, serviceRoleKey, { auth: { persistSession: false, autoRefreshToken: false } });
  const { error: eventError } = await admin.from('billing_events').insert({
    provider: 'paddle', provider_event_id: providerEventId, event_name: eventName,
    signature_verified: true, payload, processed: false,
  });
  if (eventError) {
    if (String(eventError.message || '').toLowerCase().includes('duplicate')) return json({ ok: true, duplicate: true });
    return json({ error: 'Could not record billing event.' }, 500);
  }

  let planKey = customPlanKey;
  if (!planKey && priceId) {
    const { data: plans } = await admin.from('plans').select('plan_key').eq('provider_variant_id', priceId).eq('billing_provider', 'paddle').limit(1);
    planKey = String(plans?.[0]?.plan_key || '').trim();
  }
  if (!authUserId || !subscriptionId || !planKey || !email) {
    await admin.from('billing_events').update({ processed: true, processed_at: new Date().toISOString(), error_message: 'Missing auth user, email, subscription, or plan mapping.' }).eq('provider', 'paddle').eq('provider_event_id', providerEventId);
    return json({ ok: true, ignored: true });
  }

  const { data: profile, error: profileError } = await admin.from('user_profiles').upsert({
    auth_user_id: authUserId,
    email,
    full_name: String(custom?.full_name || '').trim() || null,
    account_status: 'active',
    updated_at: new Date().toISOString(),
  }, { onConflict: 'auth_user_id' }).select('id').single();
  if (profileError || !profile?.id) return json({ error: 'Could not sync the billing profile.' }, 500);

  await admin.from('billing_customers').upsert({
    auth_user_id: authUserId,
    user_profile_id: profile.id,
    provider: 'paddle',
    provider_customer_id: customerId || null,
    email,
    updated_at: new Date().toISOString(),
  }, { onConflict: 'auth_user_id' });

  const status = normalizeStatus(eventName, data?.status);
  const periodStart = data?.current_billing_period?.starts_at || data?.started_at || null;
  const periodEnd = data?.current_billing_period?.ends_at || data?.next_billed_at || null;
  const cancelAtPeriodEnd = Boolean(data?.scheduled_change?.action === 'cancel' || data?.cancel_at_period_end);
  const { data: subscription, error: subscriptionError } = await admin.from('billing_subscriptions').upsert({
    auth_user_id: authUserId,
    user_profile_id: profile.id,
    plan_key: planKey,
    provider: 'paddle',
    provider_subscription_id: subscriptionId,
    provider_customer_id: customerId || null,
    status,
    current_period_start: periodStart,
    current_period_end: periodEnd,
    cancel_at_period_end: cancelAtPeriodEnd,
    metadata: payload,
    updated_at: new Date().toISOString(),
  }, { onConflict: 'provider,provider_subscription_id' }).select('id').single();
  if (subscriptionError || !subscription?.id) return json({ error: 'Could not sync the billing subscription.' }, 500);

  if (status === 'active' || status === 'on_trial') {
    await admin.from('user_plan_assignments').update({ status: 'revoked', updated_at: new Date().toISOString() })
      .eq('user_profile_id', profile.id).eq('status', 'active').neq('billing_subscription_id', subscription.id);
  }
  const assignmentStatus = status === 'active' || status === 'on_trial' ? 'active' : status === 'cancelled' || status === 'expired' || status === 'past_due' || status === 'unpaid' ? 'expired' : 'pending';
  const { data: existingAssignment } = await admin.from('user_plan_assignments').select('id').eq('billing_subscription_id', subscription.id).maybeSingle();
  const assignment = {
    user_profile_id: profile.id,
    plan_key: planKey,
    status: assignmentStatus,
    requested_by_email: email,
    starts_at: periodStart || new Date().toISOString(),
    ends_at: periodEnd,
    note: 'Paddle subscription sync',
    billing_subscription_id: subscription.id,
    updated_at: new Date().toISOString(),
  };
  if (existingAssignment?.id) await admin.from('user_plan_assignments').update(assignment).eq('id', existingAssignment.id);
  else await admin.from('user_plan_assignments').insert(assignment);

  await admin.from('billing_events').update({ processed: true, processed_at: new Date().toISOString() }).eq('provider', 'paddle').eq('provider_event_id', providerEventId);
  return json({ ok: true });
});
