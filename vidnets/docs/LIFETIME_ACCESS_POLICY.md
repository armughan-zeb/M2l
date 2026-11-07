# Lifetime-only access policy

Effective: 23 October 2026. This policy replaces the public Free Starter and new recurring-plan rollout documented earlier.

## Public offer

- Lifetime Pro costs USD $149 once at launch. Applicable tax is shown by Polar before payment. External AI/media provider usage is separate.
- Monthly and yearly products are archived for new sales. Existing subscriptions, renewals, paid periods and lifetime entitlements are preserved.
- An existing recurring subscriber must cancel future renewal in the Polar portal before buying Lifetime Pro. Cancellation preserves their paid period.
- Anyone can download the Windows app. Downloading, creating a Google account or signing in grants no generation access.
- The sign-in screen and locked studio show a Purchase Lifetime Pro button linking to https://vidnets.com/#pricing. The purchase account must match the app account. After a confirmed payment, refresh access in the app.
- Checkout accepts only `lifetime_pro`. Old saved recurring checkout selections are discarded by the website.

## Grant a private trial

1. Open the owner's local admin panel at http://127.0.0.1:5055/ using the development workspace with its private service-role configuration. Customer packages contain no admin credentials.
2. Ask the person to sign in once so their account appears. Select the correct email in the Users view.
3. Enter an optional assignment note and click **Grant Admin Trial**.
4. Ask them to refresh access in Vidnets. Their private trial permits five exports per UTC calendar month, up to one minute each, on one device. There is no automatic billing or paid conversion.
5. To end a trial, select that trial account, choose Admin Trial and Revoked in Plan Assignment, then save. Do not revoke a paid customer's plan to end a trial; paid access is protected against accidental trial replacement.

No expiry period was requested. Admin trials continue until the owner revokes them; their export allowance resets monthly. The owner must explicitly revoke any trial that should end sooner.

## Enforcement and compatibility

- Migration `202610230005_lifetime_only_admin_trials.sql` hides all non-lifetime sales, renames the internal `free_trial` plan Admin Trial, disables automatic grants and rejects consumer requests for private/retired plans.
- Old installers can still call `ensure_vidnets_free_access()`, but it now creates only a user profile. It never grants access.
- Only recognizable automatic Free Starter assignments are revoked. Owner-granted trials and paid assignments are preserved.
- Admin assignment uses the existing service-role-only backend behind the local panel's host, session, header and Origin protections. An active paid assignment cannot be replaced by a trial.
- Generation, queued jobs and editor exports continue to require authoritative cloud access. Trial quota reservations, failure release and idempotent completion remain enabled.
- Polar webhooks continue processing genuine existing subscription renewals and paid orders. Product archival does not cancel subscriber renewals.

## Verification record

See `artifacts/lifetime_only_20261023/` for the live catalog, Polar archival results, database verification with rolled-back test identities, regression checks, release build logs and pricing preview. A new paid purchase is not performed during these checks.
