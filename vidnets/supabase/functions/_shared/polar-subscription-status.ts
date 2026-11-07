/** A cancellation of renewal keeps access through the already-paid period. */
export function normaliseStatus(
  eventName: string,
  value: unknown,
  periodEnd: string | null = null,
  cancelAtPeriodEnd = false,
  now = Date.now(),
): string {
  const status = String(value || '').toLowerCase().replace(/-/g, '_');
  if (eventName === 'subscription.revoked') return 'expired';
  if (eventName === 'subscription.past_due') return 'past_due';
  if (eventName === 'subscription.active' || eventName === 'subscription.uncanceled') return 'active';
  const canceled = eventName === 'subscription.canceled' || status === 'canceled' || status === 'cancelled';
  if (canceled) {
    const end = Date.parse(periodEnd || '');
    if ((cancelAtPeriodEnd || eventName === 'subscription.canceled') && end > now) {
      return status === 'on_trial' ? 'on_trial' : 'active';
    }
    if (!Number.isFinite(end) && ['active', 'on_trial'].includes(status)) return status;
    return 'cancelled';
  }
  if (['active', 'on_trial', 'paused', 'past_due', 'unpaid', 'revoked', 'expired'].includes(status)) return status;
  return eventName.startsWith('subscription.') ? 'pending' : status || 'pending';
}
