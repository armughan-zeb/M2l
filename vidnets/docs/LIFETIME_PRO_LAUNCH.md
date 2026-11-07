# Lifetime Pro launch handoff

Lifetime Pro billing was activated on 23 October 2026 for the $149 one-time
launch offer. Existing recurring plans remain active for subscribers and are
hidden from new purchases. The launch product stays private in Polar so the
website's authenticated checkout can enforce account linking and cancellation
of previous recurring renewal.

## Commercial offer

| Offer | Polar product | Checkout price |
| --- | --- | ---: |
| Regular Lifetime Pro | One-time, standard | $1,499 |
| Public launch Lifetime Pro | One-time, no code required | $149 before tax |

The Polar products were created privately on 30 September 2026:

- Launch product: `4bfeae29-c17e-4165-843c-b73a05822a68`
- Standard product: `ff9ff216-d021-4aae-b4f2-5a966b380084`
- Former $599 launch product, archived with no orders: `5b74ee87-6b59-40d3-afd2-c3dddd1e78e4`

The public site will link only to the $149 launch product. The standard product
is reserved for a later price switch. No code is needed or advertised for the
$149 offer. Lifetime checkout disables discount-code entry.

## Safe activation order

1. The scoped lifetime migration `202609300001_lifetime_pro.sql` and hardening
   migrations `202610230001_lifetime_assignment_switch.sql` and
   `202610230002_lifetime_rpc_permissions.sql` are applied and recorded.
   Other local migrations remain separate. Do **not** run a blanket
   `supabase db push`: it would apply unrelated changes.
2. Deploy `create-checkout` and `polar-webhook` Edge Functions only after the
   schema is ready. The Polar dashboard has a production billing webhook
   endpoint at `/functions/v1/polar-webhook`; verify that `order.paid`,
   `order.refunded`, and existing subscription events are enabled. Keep its
   signature secret server-side.
3. The two one-time Polar products above were verified. Keep them private to
   route purchases through the website's authenticated checkout. Existing
   recurring products are also private, preserving current subscriptions.
4. Register the real product UUIDs and activate the local catalog in one
   database transaction. Substitute the two UUIDs below; never commit live
   secrets or customer information to source control.

   ```sql
   begin;
   insert into public.polar_lifetime_products(provider_product_id, plan_key, offer_kind)
   values
     ('<LAUNCH_PRODUCT_UUID>', 'lifetime_pro', 'launch'),
     ('<STANDARD_PRODUCT_UUID>', 'lifetime_pro', 'standard')
   on conflict (provider_product_id) do update set
     plan_key = excluded.plan_key, offer_kind = excluded.offer_kind;

   update public.plans
   set provider_product_id = '<LAUNCH_PRODUCT_UUID>',
       is_available_for_purchase = true, updated_at = now()
   where plan_key = 'lifetime_pro';
   update public.pricing_catalog set is_active = true
   where plan_key = 'lifetime_pro';
   update public.plans set is_available_for_purchase = false, updated_at = now()
   where billing_period in ('monthly', 'yearly');
   update public.pricing_catalog set is_active = false
   where billing_period in ('monthly', 'yearly');
   commit;
   ```

5. Publish the matching site and desktop updates. The site always requests
   `lifetime_pro`; the server rejects a checkout until its registered Polar
   product is present and purchase availability is enabled.
6. Confirm checkout displays $149 with no code field, and
   the verified `order.paid` event grants `lifetime_pro` to the signed-in user.
   Confirm a full refund revokes it; a partial refund must not. Confirm an
   existing subscriber is asked to cancel renewal before lifetime checkout.
7. Once the lifetime purchase and webhook path work, archive the old recurring
   Polar products for new buyers. Do not delete them or mark the database
   plans inactive: existing subscriptions need those records to renew and
   retain access.

## Entitlement behavior

The $149 and $1,499 products map to the same `lifetime_pro` entitlement:
current Pro feature limits, unlimited exports, and no expiration date. Paid
orders grant access; checkout redirects do not. The order ledger makes repeat
webhook deliveries idempotent. Full refunds revoke the lifetime assignment and
restore an active, paid recurring assignment if one exists. Cancelling an old
subscription's renewal preserves its access through the paid period.

If Polar is unavailable, do not switch the public site to a broken offer.
Keep the staged database rows and the existing subscriber access unchanged.
