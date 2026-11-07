const fs = require('fs');
const path = require('path');

const root = path.resolve(__dirname, '..');

function parseEnv(filePath) {
  const values = {};
  if (!fs.existsSync(filePath)) return values;
  for (const rawLine of fs.readFileSync(filePath, 'utf8').split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line || line.startsWith('#') || !line.includes('=')) continue;
    const index = line.indexOf('=');
    const key = line.slice(0, index).trim();
    let value = line.slice(index + 1).trim();
    if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) {
      value = value.slice(1, -1);
    }
    values[key] = value;
  }
  return values;
}

function assert(condition, message) {
  if (!condition) {
    throw new Error(message);
  }
}

async function supabaseFetch(url, serviceKey, route, options = {}) {
  const response = await fetch(`${url}/rest/v1/${route}`, {
    ...options,
    headers: {
      apikey: serviceKey,
      Authorization: `Bearer ${serviceKey}`,
      'Content-Type': 'application/json',
      ...(options.headers || {}),
    },
  });
  const text = await response.text();
  let payload = null;
  try {
    payload = text ? JSON.parse(text) : null;
  } catch (_error) {
    payload = text;
  }
  if (!response.ok) {
    throw new Error(`${route} failed (${response.status}): ${typeof payload === 'string' ? payload : JSON.stringify(payload)}`);
  }
  return payload;
}

async function main() {
  const env = { ...parseEnv(path.join(root, '.env.public')), ...parseEnv(path.join(root, '.env')) };
  const url = String(env.SUPABASE_URL || '').replace(/\/$/, '');
  const serviceKey = String(env.SUPABASE_SERVICE_ROLE_KEY || '');

  assert(url, 'SUPABASE_URL is missing.');
  assert(serviceKey, 'SUPABASE_SERVICE_ROLE_KEY is required for this admin smoke test.');

  const profiles = await supabaseFetch(
    url,
    serviceKey,
    'user_profiles?select=id,auth_user_id,email,account_status,device_fingerprint&limit=20'
  );
  assert(Array.isArray(profiles), 'user_profiles response was not an array.');

  const assignments = await supabaseFetch(
    url,
    serviceKey,
    'user_plan_assignments?select=id,user_profile_id,plan_key,status,starts_at,ends_at&status=in.(active,pending)&limit=50'
  );
  assert(Array.isArray(assignments), 'user_plan_assignments response was not an array.');

  const devices = await supabaseFetch(
    url,
    serviceKey,
    'rpc/list_user_devices',
    {
      method: 'POST',
      body: JSON.stringify({ p_user_id: null }),
    }
  );
  assert(Array.isArray(devices), 'list_user_devices RPC did not return an array.');

  const mirrorRows = await supabaseFetch(
    url,
    serviceKey,
    'profiles?select=id,email,plan,status,requested_plan,device_fingerprint&limit=20'
  );
  assert(Array.isArray(mirrorRows), 'profiles response was not an array.');

  const openByProfile = new Map();
  for (const assignment of assignments) {
    if (!['active', 'pending'].includes(assignment.status)) continue;
    const key = `${assignment.user_profile_id}:${assignment.status}`;
    openByProfile.set(key, (openByProfile.get(key) || 0) + 1);
  }
  for (const [key, count] of openByProfile.entries()) {
    assert(count <= 1, `More than one open assignment found for ${key}.`);
  }

  const activeDevicesByProfile = new Map();
  for (const device of devices) {
    if (device.status !== 'active') continue;
    activeDevicesByProfile.set(device.user_profile_id, (activeDevicesByProfile.get(device.user_profile_id) || 0) + 1);
  }
  for (const [profileId, count] of activeDevicesByProfile.entries()) {
    assert(count <= 2, `Profile ${profileId} has ${count} active devices; limit is 2.`);
  }

  const profileByAuthId = new Map(profiles.filter((p) => p.auth_user_id).map((p) => [p.auth_user_id, p]));
  for (const mirror of mirrorRows) {
    const canonical = profileByAuthId.get(mirror.id);
    if (!canonical) continue;
    const hasActiveAssignment = assignments.some(
      (assignment) => assignment.user_profile_id === canonical.id && assignment.status === 'active'
    );
    if (hasActiveAssignment) {
      assert(mirror.status === 'active', `Mirror profile ${mirror.email} is not active while canonical assignment is active.`);
      assert(mirror.plan && mirror.plan !== 'free', `Mirror profile ${mirror.email} has no active plan key.`);
    }
  }

  console.log(JSON.stringify({
    ok: true,
    profiles: profiles.length,
    open_assignments: assignments.length,
    devices: devices.length,
    mirror_profiles: mirrorRows.length,
    active_device_limit: 2,
  }, null, 2));
}

main().catch((error) => {
  console.error(`[supabase-auth-smoke] ${error.message}`);
  process.exit(1);
});
