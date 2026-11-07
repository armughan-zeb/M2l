import { createClient } from 'https://esm.sh/@supabase/supabase-js@2';

const corsHeaders = { 'Access-Control-Allow-Origin': '*', 'Access-Control-Allow-Headers': 'content-type, x-signature' };
const textEncoder = new TextEncoder();

function env(name: string): string { return (Deno.env.get(name) || '').trim(); }
function json(body: Record<string, unknown>, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { ...corsHeaders, 'Content-Type': 'application/json' } });
}
function bytesToHex(bytes: Uint8Array): string {
  return Array.from(bytes).map((byte) => byte.toString(16).padStart(2, '0')).join('');
}
async function hmacHex(secret: string, body: string): Promise<string> {
  const key = await crypto.subtle.importKey('raw', textEncoder.encode(secret), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']);
  return bytesToHex(new Uint8Array(await crypto.subtle.sign('HMAC', key, textEncoder.encode(body))));
}
function safeEqual(left: string, right: string): boolean {
  if (!left || left.length !== right.length) return false;
  let diff = 0;
  for (let i = 0; i < left.length; i += 1) diff |= left.charCodeAt(i) ^ right.charCodeAt(i);
  return diff === 0;
}

Deno.serve(async (request) => {
  if (request.method === 'OPTIONS') return new Response('ok', { headers: corsHeaders });
  if (request.method !== 'POST') return json({ error: 'Method not allowed' }, 405);

  const supabaseUrl = env('SUPABASE_URL');
  const serviceRoleKey = env('SUPABASE_SERVICE_ROLE_KEY');
  const webhookSecret = env('LEMON_SQUEEZY_WEBHOOK_SECRET');
  if (!supabaseUrl || !serviceRoleKey || !webhookSecret) return json({ error: 'Webhook is not configured.' }, 503);

  const rawBody = await request.text();
  const signature = (request.headers.get('x-signature') || '').trim().toLowerCase();
  const expected = await hmacHex(webhookSecret, rawBody);
  if (!safeEqual(signature, expected)) return json({ error: 'Invalid signature.' }, 401);

  let payload: Record<string, any>;
  try {
    payload = JSON.parse(rawBody);
  } catch {
    return json({ error: 'Invalid JSON payload.' }, 400);
  }
  const eventName = String(payload?.meta?.event_name || 'unknown');
  const providerEventId = String(payload?.meta?.event_id || `${eventName}:${payload?.data?.id || `body-${rawBody.length}`}`);
  const data = payload?.data || {};
  const attributes = data?.attributes || {};
  const custom = payload?.meta?.custom_data || attributes?.first_subscription_item?.custom_data || {};
  const authUserId = String(custom?.auth_user_id || '').trim();
  const planKeyFromCustom = String(custom?.plan_key || '').trim().toLowerCase();
  const status = String(attributes?.status || (eventName.includes('cancelled') ? 'cancelled' : 'active')).toLowerCase();
  const subscriptionId = String(data?.id || attributes?.subscription_id || '').trim();
  const customerId = String(attributes?.customer_id || '').trim();
  const variantId = String(attributes?.variant_id || attributes?.product_id || '').trim();
  const email = String(attributes?.user_email || attributes?.email || '').trim().toLowerCase();

  const adminClient = createClient(supabaseUrl, serviceRoleKey, { auth: { persistSession: false, autoRefreshToken: false } });
  const { error: eventError } = await adminClient.from('billing_events').insert({
    provider: 'lemon_squeezy', provider_event_id: providerEventId, event_name: eventName,
    signature_verified: true, payload, processed: false,
  });
  if (eventError) {
    if (String(eventError.message || '').toLowerCase().includes('duplicate')) return json({ ok: true, duplicate: true });
    return json({ error: 'Could not record billing event.' }, 500);
  }

  let planKey = planKeyFromCustom;
  if (!planKey && variantId) {
    const { data: plans } = await adminClient.from('plans').select('plan_key').eq('provider_variant_id', variantId).limit(1);
    planKey = String(plans?.[0]?.plan_key || '').trim();
  }
  if (!authUserId || !subscriptionId || !planKey) {
    await adminClient.from('billing_events').update({ processed: true, processed_at: new Date().toISOString(), error_message: 'Missing auth user, subscription, or plan mapping.' }).eq('provider', 'lemon_squeezy').eq('provider_event_id', providerEventId);
    return json({ ok: true, ignored: true });
  }

  const periodStart = attributes?.starts_at || attributes?.created_at || null;
  const periodEnd = attributes?.ends_at || attributes?.renews_at || null;
  const { error: syncError } = await adminClient.rpc('apply_lemon_subscription', {
    p_auth_user_id: authUserId, p_email: email, p_full_name: '', p_provider_customer_id: customerId,
    p_provider_subscription_id: subscriptionId, p_plan_key: planKey, p_status: status,
    p_period_start: periodStart, p_period_end: periodEnd,
    p_cancel_at_period_end: Boolean(attributes?.cancelled || attributes?.cancelled_at), p_metadata: payload,
  });
  if (syncError) {
    await adminClient.from('billing_events').update({ error_message: syncError.message }).eq('provider', 'lemon_squeezy').eq('provider_event_id', providerEventId);
    return json({ error: 'Subscription was received but entitlement sync failed.' }, 500);
  }
  await adminClient.from('billing_events').update({ processed: true, processed_at: new Date().toISOString() }).eq('provider', 'lemon_squeezy').eq('provider_event_id', providerEventId);
  return json({ ok: true });
});
