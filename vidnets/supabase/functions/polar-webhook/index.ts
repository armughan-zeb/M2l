import { createClient } from 'https://esm.sh/@supabase/supabase-js@2';
import { normaliseStatus } from '../_shared/polar-subscription-status.ts';

const corsHeaders = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Headers': 'content-type, webhook-id, webhook-signature, webhook-timestamp, x-polar-signature, polar-signature, x-signature',
  'Access-Control-Allow-Methods': 'POST, OPTIONS',
};
const encoder = new TextEncoder();

function env(name: string): string { return (Deno.env.get(name) || '').trim(); }
function json(body: Record<string, unknown>, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { ...corsHeaders, 'Content-Type': 'application/json' } });
}
function safeEqual(left: string, right: string): boolean {
  if (!left || left.length !== right.length) return false;
  let diff = 0;
  for (let i = 0; i < left.length; i += 1) diff |= left.charCodeAt(i) ^ right.charCodeAt(i);
  return diff === 0;
}
function bytesToHex(bytes: Uint8Array): string {
  return Array.from(bytes).map((byte) => byte.toString(16).padStart(2, '0')).join('');
}
function bytesToBase64(bytes: Uint8Array): string {
  let binary = '';
  const chunkSize = 0x8000;
  for (let i = 0; i < bytes.length; i += chunkSize) binary += String.fromCharCode(...bytes.subarray(i, i + chunkSize));
  return btoa(binary);
}
async function hmac(secretBytes: Uint8Array, value: string): Promise<Uint8Array> {
  const keyBuffer = new ArrayBuffer(secretBytes.byteLength);
  new Uint8Array(keyBuffer).set(secretBytes);
  const key = await crypto.subtle.importKey('raw', keyBuffer, { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']);
  return new Uint8Array(await crypto.subtle.sign('HMAC', key, encoder.encode(value)));
}
function decodeBase64(value: string): Uint8Array | null {
  try {
    const binary = atob(value);
    return Uint8Array.from(binary, (char) => char.charCodeAt(0));
  } catch { return null; }
}

/**
 * Polar uses the Standard Webhooks format. Polar's dashboard gives us the
 * human-readable secret, while the standard SDK expects a base64-wrapped
 * secret and decodes it before HMAC verification. Legacy Polar signatures are accepted during the migration
 * window so an endpoint can be rotated without dropping a delivery.
 */
async function verifySignature(request: Request, rawBody: string, secret: string): Promise<boolean> {
  const webhookId = (request.headers.get('webhook-id') || '').trim();
  const webhookTimestamp = (request.headers.get('webhook-timestamp') || '').trim();
  const webhookSignature = (request.headers.get('webhook-signature') || '').trim();
  if (webhookId && webhookTimestamp && webhookSignature) {
    const timestamp = Number(webhookTimestamp);
    if (!Number.isFinite(timestamp) || Math.abs(Date.now() / 1000 - timestamp) > 300) return false;
    // The Standard Webhooks libraries receive base64(secret) and decode it
    // internally. The equivalent raw HMAC key here is the configured secret
    // bytes themselves (including Polar's `polar_whs_` prefix).
    const standardKey = encoder.encode(secret);
    const expected = bytesToBase64(await hmac(standardKey, `${webhookId}.${webhookTimestamp}.${rawBody}`));
    const valid = webhookSignature.split(/\s+/).some((token) => {
      const [version, value] = token.split(',', 2);
      return version === 'v1' && safeEqual(value || '', expected);
    });
    if (valid) return true;
  }

  const legacySignature = (request.headers.get('x-polar-signature') || request.headers.get('polar-signature') || request.headers.get('x-signature') || '').trim();
  if (!legacySignature) return false;
  const secretBytes = encoder.encode(secret);
  const digest = await hmac(secretBytes, rawBody);
  const normalized = legacySignature.replace(/^sha256[=:]/i, '').trim();
  return safeEqual(normalized.toLowerCase(), bytesToHex(digest)) || safeEqual(normalized, bytesToBase64(digest));
}

function firstString(...values: unknown[]): string {
  for (const value of values) {
    const text = String(value ?? '').trim();
    if (text) return text;
  }
  return '';
}

async function syncSubscription(adminClient: any, input: {
  authUserId: string; email: string; fullName: string; customerId: string;
  subscriptionId: string; planKey: string; status: string;
  periodStart: string | null; periodEnd: string | null;
  cancelAtPeriodEnd: boolean; metadata: Record<string, any>;
}) {
  const { data: profile, error: profileError } = await adminClient
    .from('user_profiles')
    .upsert({
      auth_user_id: input.authUserId,
      email: input.email,
      full_name: input.fullName || null,
      account_status: 'active',
      updated_at: new Date().toISOString(),
    }, { onConflict: 'auth_user_id' })
    .select('id')
    .single();
  if (profileError || !profile?.id) throw profileError || new Error('Could not create billing profile');

  const { error: customerError } = await adminClient.from('billing_customers').upsert({
    auth_user_id: input.authUserId,
    user_profile_id: profile.id,
    provider: 'polar',
    provider_customer_id: input.customerId || null,
    email: input.email,
    updated_at: new Date().toISOString(),
  }, { onConflict: 'auth_user_id' });
  if (customerError) throw customerError;

  const { data: subscription, error: subscriptionError } = await adminClient.from('billing_subscriptions').upsert({
    auth_user_id: input.authUserId,
    user_profile_id: profile.id,
    plan_key: input.planKey,
    provider: 'polar',
    provider_subscription_id: input.subscriptionId,
    provider_customer_id: input.customerId || null,
    status: input.status,
    current_period_start: input.periodStart,
    current_period_end: input.periodEnd,
    cancel_at_period_end: input.cancelAtPeriodEnd,
    metadata: input.metadata || {},
    updated_at: new Date().toISOString(),
  }, { onConflict: 'provider,provider_subscription_id' }).select('id').single();
  if (subscriptionError || !subscription?.id) throw subscriptionError || new Error('Could not save Polar subscription');

  let assignmentStatus = input.status === 'active' || input.status === 'on_trial' ? 'active'
    : ['cancelled', 'expired', 'past_due', 'unpaid'].includes(input.status) ? 'expired' : 'pending';
  const { data: lifetime, error: lifetimeError } = await adminClient.from('user_plan_assignments')
    .select('id').eq('user_profile_id', profile.id).eq('plan_key', 'lifetime_pro')
    .eq('status', 'active').is('ends_at', null).limit(1);
  if (lifetimeError) throw lifetimeError;
  // Preserve the recurring payment ledger, but never let a later recurring
  // webhook displace an already-paid lifetime entitlement.
  if (lifetime?.length && assignmentStatus === 'active') assignmentStatus = 'revoked';
  if (assignmentStatus === 'active') {
    const { error } = await adminClient.from('user_plan_assignments')
      .update({ status: 'revoked', updated_at: new Date().toISOString() })
      .eq('user_profile_id', profile.id).eq('status', 'active')
      .neq('plan_key', 'lifetime_pro');
    if (error) throw error;
  }
  const { data: existingAssignment } = await adminClient.from('user_plan_assignments')
    .select('id').eq('billing_subscription_id', subscription.id).maybeSingle();
  const assignment = {
    user_profile_id: profile.id, plan_key: input.planKey, status: assignmentStatus,
    requested_by_email: input.email, starts_at: input.periodStart || new Date().toISOString(),
    ends_at: input.periodEnd, note: 'Polar subscription sync',
    billing_subscription_id: subscription.id, updated_at: new Date().toISOString(),
  };
  const { error: assignmentError } = existingAssignment?.id
    ? await adminClient.from('user_plan_assignments').update(assignment).eq('id', existingAssignment.id)
    : await adminClient.from('user_plan_assignments').insert(assignment);
  if (assignmentError) throw assignmentError;
}

Deno.serve(async (request) => {
  if (request.method === 'OPTIONS') return new Response('ok', { headers: corsHeaders });
  if (request.method !== 'POST') return json({ error: 'Method not allowed' }, 405);

  const supabaseUrl = env('SUPABASE_URL');
  const serviceRoleKey = env('SUPABASE_SERVICE_ROLE_KEY');
  const webhookSecret = env('POLAR_WEBHOOK_SECRET');
  if (!supabaseUrl || !serviceRoleKey || !webhookSecret) return json({ error: 'Webhook is not configured.' }, 503);

  const rawBody = await request.text();
  if (!(await verifySignature(request, rawBody, webhookSecret))) return json({ error: 'Invalid signature.' }, 401);

  let payload: Record<string, any>;
  try { payload = JSON.parse(rawBody); } catch { return json({ error: 'Invalid JSON payload.' }, 400); }
  const eventName = firstString(payload?.type, payload?.event, payload?.event_type, 'unknown').toLowerCase();
  const data = payload?.data || payload?.payload || {};
  const metadata = data?.metadata || data?.customer?.metadata || data?.product?.metadata || {};
  const providerEventId = firstString(request.headers.get('webhook-id'), payload?.id, payload?.event_id, `${eventName}:${data?.id || `body-${rawBody.length}`}`);
  const authUserId = firstString(metadata?.auth_user_id, data?.external_customer_id, data?.customer?.external_id);
  const email = firstString(data?.customer_email, data?.customer?.email, data?.user_email).toLowerCase();
  const fullName = firstString(data?.customer_name, data?.customer?.name, data?.customer?.billing_name);
  const customerId = firstString(data?.customer_id, data?.customer?.id);
  const subscriptionId = firstString(data?.id, data?.subscription_id, data?.subscription?.id);
  const productId = firstString(data?.product_id, data?.product?.id, data?.product_price?.product_id);
  const isSubscriptionEvent = eventName.startsWith('subscription.');
  const planKeyFromMetadata = firstString(metadata?.plan_key).toLowerCase();

  const adminClient = createClient(supabaseUrl, serviceRoleKey, { auth: { persistSession: false, autoRefreshToken: false } });
  const { error: eventError } = await adminClient.from('billing_events').insert({
    provider: 'polar', provider_event_id: providerEventId, event_name: eventName,
    signature_verified: true, payload, processed: false,
  });
  if (eventError) {
    const duplicate = String(eventError.message || '').toLowerCase().includes('duplicate') || eventError.code === '23505';
    if (!duplicate) return json({ error: 'Could not record billing event.' }, 500);
    const { data: previous } = await adminClient.from('billing_events')
      .select('processed').eq('provider', 'polar').eq('provider_event_id', providerEventId).maybeSingle();
    if (previous?.processed) return json({ ok: true, duplicate: true });
  }

  if (eventName === 'order.paid' && String(data?.billing_reason || '').toLowerCase() === 'purchase') {
    const orderId = firstString(data?.id);
    const paid = data?.paid === true || String(data?.status || '').toLowerCase() === 'paid';
    const netAmount = Number(data?.net_amount);
    const { data: grant, error: grantError } = await adminClient
      .from('polar_lifetime_products').select('plan_key')
      .eq('provider_product_id', productId).maybeSingle();
    if (grantError) return json({ error: 'Lifetime product mapping is unavailable.' }, 503);
    if (grant?.plan_key === 'lifetime_pro') {
      if (!paid || !authUserId || !orderId || !/^[0-9a-f-]{36}$/i.test(authUserId)
          || !Number.isInteger(netAmount) || netAmount < 14900
          || firstString(data?.currency).toLowerCase() !== 'usd') {
        await adminClient.from('billing_events').update({ error_message: 'Paid lifetime order lacks verified identity or payment.' })
          .eq('provider', 'polar').eq('provider_event_id', providerEventId);
        return json({ error: 'Lifetime order requires reconciliation.' }, 500);
      }
      const { error: grantError } = await adminClient.rpc('apply_polar_lifetime_order', {
        p_auth_user_id: authUserId, p_email: email, p_full_name: fullName,
        p_provider_customer_id: customerId, p_provider_order_id: orderId,
        p_provider_product_id: productId, p_amount_cents: Number(data?.total_amount || 0),
        p_currency_code: firstString(data?.currency, 'USD'), p_metadata: payload,
      });
      if (grantError) {
        await adminClient.from('billing_events').update({ error_message: grantError.message })
          .eq('provider', 'polar').eq('provider_event_id', providerEventId);
        return json({ error: 'Paid lifetime order could not be activated yet.' }, 500);
      }
      await adminClient.from('billing_events').update({ processed: true, processed_at: new Date().toISOString(), error_message: null })
        .eq('provider', 'polar').eq('provider_event_id', providerEventId);
      return json({ ok: true, lifetime: true });
    }
  }

  if (eventName === 'order.refunded' && String(data?.billing_reason || '').toLowerCase() === 'purchase') {
    const orderId = firstString(data?.id);
    // Polar tracks the refundable principal separately from refunded tax.
    const total = Number(data?.net_amount || 0);
    const refunded = Number(data?.refunded_amount || 0);
    if (orderId && total > 0 && refunded >= total) {
      const { data: lifetimeProduct, error: productError } = await adminClient
        .from('polar_lifetime_products').select('plan_key').eq('provider_product_id', productId).maybeSingle();
      if (productError) return json({ error: 'Lifetime product mapping is unavailable.' }, 503);
      if (lifetimeProduct?.plan_key !== 'lifetime_pro') {
        await adminClient.from('billing_events').update({ processed: true, processed_at: new Date().toISOString() })
          .eq('provider', 'polar').eq('provider_event_id', providerEventId);
        return json({ ok: true, ignored: true });
      }
      const { data: purchase, error: lookupError } = await adminClient.from('billing_lifetime_purchases')
        .select('id').eq('provider', 'polar').eq('provider_order_id', orderId).maybeSingle();
      if (lookupError) return json({ error: 'Lifetime purchase lookup failed.' }, 503);
      // Retry out-of-order deliveries after order.paid has been processed.
      if (!purchase?.id) return json({ error: 'Paid lifetime order has not been recorded yet.' }, 503);
      const { error: refundError } = await adminClient.rpc('refund_polar_lifetime_order', { p_provider_order_id: orderId });
      if (refundError) return json({ error: 'Lifetime refund access update failed.' }, 500);
    }
    await adminClient.from('billing_events').update({ processed: true, processed_at: new Date().toISOString() })
      .eq('provider', 'polar').eq('provider_event_id', providerEventId);
    return json({ ok: true, lifetime_refund: total > 0 && refunded >= total });
  }

  let planKey = '';
  if (productId) {
    const { data: productPlans } = await adminClient.from('plans').select('plan_key').eq('billing_provider', 'polar').or(`provider_product_id.eq.${productId},provider_variant_id.eq.${productId}`).limit(1);
    planKey = firstString(productPlans?.[0]?.plan_key).toLowerCase();
  }
  if (planKeyFromMetadata && planKey && planKeyFromMetadata !== planKey) {
    return json({error:'Subscription product and plan metadata do not match.'},422);
  }
  // Other checkout/order events are audit-only. Recurring access is granted
  // from subscription events; lifetime access comes only from order.paid.
  if (!isSubscriptionEvent || !authUserId || !subscriptionId || !planKey) {
    await adminClient.from('billing_events').update({ processed: true, processed_at: new Date().toISOString(), error_message: !isSubscriptionEvent ? null : 'Missing auth user, subscription, or Polar product mapping.' }).eq('provider', 'polar').eq('provider_event_id', providerEventId);
    return json({ ok: true, ignored: true });
  }

  const periodStart = firstString(data?.current_period_start, data?.started_at, data?.created_at) || null;
  const periodEnd = firstString(data?.current_period_end, data?.ends_at, data?.renews_at) || null;
  const cancelled = Boolean(data?.cancel_at_period_end || data?.cancel_at || eventName === 'subscription.canceled');
  const status = normaliseStatus(eventName, data?.status, periodEnd, cancelled);
  try {
    await syncSubscription(adminClient, {
      authUserId, email, fullName, customerId, subscriptionId, planKey, status,
      periodStart, periodEnd, cancelAtPeriodEnd: cancelled, metadata: payload,
    });
  } catch (syncError) {
    const message = syncError instanceof Error ? syncError.message : String(syncError || 'Unknown sync error');
    await adminClient.from('billing_events').update({ error_message: message }).eq('provider', 'polar').eq('provider_event_id', providerEventId);
    return json({ error: 'Subscription was received but entitlement sync failed.' }, 500);
  }
  await adminClient.from('billing_events').update({ processed: true, processed_at: new Date().toISOString() }).eq('provider', 'polar').eq('provider_event_id', providerEventId);
  return json({ ok: true });
});
