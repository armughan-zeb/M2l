import test from 'node:test';
import assert from 'node:assert/strict';
import { normaliseStatus } from '../supabase/functions/_shared/polar-subscription-status.ts';
const now = Date.parse('2026-10-23T00:00:00Z');
const future = '2026-11-23T00:00:00Z';
const past = '2026-09-23T00:00:00Z';
test('canceling future renewal keeps paid access', () => {
  assert.equal(normaliseStatus('subscription.canceled', 'canceled', future, true, now), 'active');
  assert.equal(normaliseStatus('subscription.updated', 'canceled', future, true, now), 'active');
});
test('a cancellation after the paid period expires does not grant access', () => {
  assert.equal(normaliseStatus('subscription.canceled', 'active', past, true, now), 'cancelled');
});
test('revocation and failed payment never regain access from a future date', () => {
  assert.equal(normaliseStatus('subscription.revoked', 'active', future, true, now), 'expired');
  assert.equal(normaliseStatus('subscription.past_due', 'active', future, true, now), 'past_due');
});
test('missing period uses provider active status, never invents paid access', () => {
  assert.equal(normaliseStatus('subscription.canceled', 'active', null, true, now), 'active');
  assert.equal(normaliseStatus('subscription.canceled', 'canceled', null, true, now), 'cancelled');
});
