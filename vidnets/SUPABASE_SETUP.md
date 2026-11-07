# Vidnets Studio Supabase Setup

## 1. Create the Supabase project

1. Create a new Supabase project.
2. Open the project dashboard and copy:
   - Project URL
   - Publishable / anon key
   - Service role key

## 2. Run the Vidzeo SQL

Run these migrations in order:

1. `supabase/migrations/20260423_000001_vidzeo_studio_beta.sql`
2. `supabase/migrations/20260423_000002_vidzeo_security_enforcement.sql`

The second migration adds:

- `public.profiles`
- `public.pricing_catalog`
- `public.generation_quota_reservations`
- strict device lock RPCs
- free quota RPCs
- expiry reconciliation RPCs
- sync triggers so `user_profiles` and `user_plan_assignments` stay mirrored into `public.profiles`

## 3. Configure Google Auth in Supabase

In Supabase:

1. Go to `Authentication -> Providers -> Google`.
2. Enable Google.
3. Paste your Google OAuth client ID and client secret.
4. Save the provider.

Also check `Authentication -> URL Configuration` and set your app URLs:

- Site URL: `https://vidnets.com/Vidzeo/index.html`
- Additional redirect URLs:
  - `http://localhost:5000/auth/browser-callback`
  - `https://vidnets.com/Vidzeo/index.html`

If you deploy later, add your production domain there too.

## 4. Create the Google OAuth client

In Google Cloud:

1. Open the Google Auth Platform / Credentials area.
2. Configure the consent screen branding. Set the application name to
   **Vidnets Studio** (or **Vidnets Studio v1**). If Google shows the Supabase
   project hostname on the account chooser, this branding field is still using
   its default value; it cannot be changed by Electron or by the Supabase URL.
3. Make sure these scopes are available:
   - `openid`
   - `userinfo.email`
   - `userinfo.profile`
4. Create an OAuth client with type `Web application`.
   Do not use `Desktop app` for the current Vidzeo flow, because the sign-in page runs on `http://localhost:5000` and Google requires authorized JavaScript origins for that.

Add these authorized JavaScript origins:

- `http://localhost:5000`
- `http://127.0.0.1:5000`

Add this authorized redirect URI:

- `https://YOUR_PROJECT_REF.supabase.co/auth/v1/callback`

Replace `YOUR_PROJECT_REF` with your real Supabase project reference.

If you use local Supabase CLI development later, also add:

- `http://127.0.0.1:54321/auth/v1/callback`

## 5. Fill in `.env`

Create `.env` from `.env.example` and set:

```env
SUPABASE_URL=https://YOUR_PROJECT_REF.supabase.co
SUPABASE_ANON_KEY=YOUR_SUPABASE_ANON_KEY
SUPABASE_SERVICE_ROLE_KEY=YOUR_SUPABASE_SERVICE_ROLE_KEY
GOOGLE_CLIENT_ID=YOUR_GOOGLE_WEB_CLIENT_ID
VIDZEO_ADMIN_EMAILS=admin@yourdomain.com

ADMIN_PANEL_EMAIL=admin@yourdomain.com
ADMIN_PANEL_PASSWORD=choose-a-strong-password
ADMIN_PANEL_NAME=Vidzeo Admin
FLASK_SECRET_KEY=choose-a-long-random-secret
```

## 6. Configure billing and pricing

The desktop app does not collect card details. Its Pricing buttons open the
first-party Vidnets pricing page. The website requires the user to sign in with
Google, then asks the `create-checkout` Edge Function to create a Polar
hosted checkout for the selected catalog product. No provider API key or card
data is shipped to the website or desktop package.

1. Apply `supabase/migrations/202608250007_billing_ecosystem.sql` followed by `supabase/migrations/202609060001_polar_billing.sql`. The latter makes Polar the active provider and adds the verified webhook entitlement RPC.
2. Fill `billing_provider`, `provider_product_id`, and `provider_variant_id` for each active `public.plans` row with the Polar product UUID. The product UUID is public catalog data; never put an access token or webhook secret in this table.

   ```sql
   update public.plans set
     billing_provider = 'polar',
     provider_product_id = '<POLAR_PRODUCT_UUID>',
     provider_variant_id = '<POLAR_PRODUCT_UUID>'
   where plan_key = 'basic_monthly';
   -- repeat for basic_yearly, plus_monthly, plus_yearly, max_monthly, max_yearly
   ```
3. Deploy both functions:

```text
supabase functions deploy create-checkout --project-ref uioqutamidsjepyqgrlj --use-api
supabase functions deploy polar-webhook --project-ref uioqutamidsjepyqgrlj --use-api
```

4. Set function secrets. Do not paste these into the website or chat:

```text
supabase secrets set --project-ref uioqutamidsjepyqgrlj POLAR_ACCESS_TOKEN=... POLAR_ORGANIZATION_ID=835ef172-a0d1-4aad-afef-dafdc24028a4 POLAR_API_BASE_URL=https://api.polar.sh POLAR_WEBHOOK_SECRET=...
```

5. In Polar → Organization Settings → Webhooks, add the URL
   `https://uioqutamidsjepyqgrlj.supabase.co/functions/v1/polar-webhook` with
   Raw format and subscribe to checkout, order, customer, and subscription
   events. Copy the generated secret into `POLAR_WEBHOOK_SECRET` only in
   Supabase function secrets.
6. Keep the checkout return URL and Google OAuth redirect allow-list pointed at
   `https://vidnets.com`; the website initiates OAuth in the current browser
   and returns to the pricing page before calling `create-checkout`.
7. In Supabase → Edge Functions → `polar-webhook` → Settings, turn **Verify JWT
   with legacy secret** off. Polar authenticates this endpoint with Standard
   Webhooks signature headers; requiring a Supabase JWT would block webhooks.

The Polar webhook verifies the raw-body Standard Webhooks signature, records an
idempotent billing event, upserts the subscription, and synchronizes
`user_plan_assignments`. No provider secret is distributed to desktop users.

## 7. How Vidzeo is wired now

- The desktop app now opens your default browser for Google sign-in on a localhost Vidzeo auth page.
- That localhost auth page uses Google Identity Services plus Supabase Auth for `Continue with Google`.
- After sign-in, the Electron app sends the Supabase access token to the Flask backend.
- The backend verifies the token, upserts the user into `public.user_profiles`, and reads plan state.
- Admin plan actions write to `public.user_plan_assignments`.
- The new SQL trigger layer mirrors those plan changes into `public.profiles`, which powers the device lock, quota, and expiry RPCs.
- The admin panel can use direct backend login, or local no-login mode with `ADMIN_PANEL_BYPASS_LOGIN=true`.

## 8. How to test

1. Start the backend or launch the app.
2. Open the desktop landing screen and click `Continue with Google`.
3. After the first sign-in, confirm the user appears in `public.user_profiles`.
4. Open `http://localhost:5000/admin/`.
5. Log in with the direct admin credentials from `.env`.
6. Assign an active plan to the test user.
7. Reopen the desktop app and confirm the studio unlocks.

## 9. Important note

`main.py` now reads `.env`, and Electron now passes `.env` values through to the backend process. That means the same Supabase configuration works whether you:

- launch the desktop app
- run the backend directly
- use the admin launcher
