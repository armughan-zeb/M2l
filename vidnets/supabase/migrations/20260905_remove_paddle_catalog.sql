-- Vidzeo billing provider cutover: Lemon Squeezy is the only active checkout.
-- Existing Paddle IDs are never valid Lemon variants, so clear them rather
-- than risking a cross-provider checkout. Fill numeric Lemon variant IDs
-- after the catalog is created in Lemon Squeezy.
update public.plans
set billing_provider = 'lemon_squeezy',
    provider_product_id = null,
    provider_variant_id = null,
    checkout_url = null,
    updated_at = now()
where billing_provider = 'paddle';

notify pgrst, 'reload schema';
