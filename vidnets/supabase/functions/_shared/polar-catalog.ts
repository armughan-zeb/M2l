export async function verifyPolarPlan(plan: any, allowArchived = false) {
  const productId = String(plan.provider_product_id || plan.provider_variant_id || '');
  if (!/^[0-9a-f-]{36}$/i.test(productId)) throw new Error('Product mapping unavailable');
  const response = await fetch(`${(Deno.env.get('POLAR_API_BASE_URL') || 'https://api.polar.sh').replace(/\/$/, '')}/v1/products/${productId}`, {
    headers: { Authorization: `Bearer ${Deno.env.get('POLAR_ACCESS_TOKEN') || ''}` },
    signal: AbortSignal.timeout(10000),
  });
  if (!response.ok) throw new Error('Provider catalog unavailable');
  const product = await response.json();
  const interval = plan.billing_period === 'yearly' ? 'year' : plan.billing_period === 'monthly' ? 'month' : null;
  const expected = Math.round(Number(plan.price_monthly_display) * 100 * (interval === 'year' ? 12 : 1));
  const price = (product.prices || []).find((p: any) => !p.is_archived && p.amount_type === 'fixed'
    && String(p.price_currency).toLowerCase() === 'usd' && Number(p.price_amount) === expected
    && (p.recurring_interval || product.recurring_interval || null) === interval);
  if (!price || product.id !== productId || (!allowArchived && product.is_archived)
      || Number(product.recurring_interval_count || 1) !== 1) throw new Error('Price or billing interval does not match the advertised plan');
  return { product, price, amount: expected, productId };
}
