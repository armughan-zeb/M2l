const { groupBy, makeReporter, supabaseFetch } = require('./audit-utils');

function isCurrentlyActive(assignment) {
  if (!assignment || assignment.status !== 'active') return false;
  const endsAt = assignment.ends_at ? Date.parse(assignment.ends_at) : 0;
  return !endsAt || endsAt > Date.now();
}

function latestByCreated(rows) {
  return [...rows].sort((a, b) => String(b.created_at || '').localeCompare(String(a.created_at || '')))[0] || null;
}

async function main() {
  const reporter = makeReporter();
  const [
    profiles,
    mirrorProfiles,
    plans,
    assignments,
    devices,
    auditLogs,
  ] = await Promise.all([
    supabaseFetch('user_profiles?select=*&order=updated_at.desc'),
    supabaseFetch('profiles?select=*'),
    supabaseFetch('plans?select=*&order=sort_order.asc'),
    supabaseFetch('user_plan_assignments?select=*&order=created_at.desc'),
    supabaseFetch('rpc/list_user_devices', {
      method: 'POST',
      body: JSON.stringify({ p_user_id: null }),
    }),
    supabaseFetch('admin_audit_logs?select=*&order=created_at.desc&limit=500'),
  ]);

  const planKeys = new Set(plans.map((plan) => plan.plan_key));
  const profilesById = new Map(profiles.map((profile) => [profile.id, profile]));
  const mirrorsByAuthId = new Map(mirrorProfiles.map((profile) => [profile.id, profile]));
  const assignmentsByProfile = groupBy(assignments, (row) => row.user_profile_id);
  const devicesByProfile = groupBy(devices, (row) => row.user_profile_id);
  const auditsByTarget = groupBy(auditLogs, (row) => row.target_user_profile_id);

  for (const assignment of assignments) {
    if (!profilesById.has(assignment.user_profile_id)) {
      reporter.red('assignment_without_profile', 'Plan assignment points at a missing user_profile.', {
        assignment_id: assignment.id,
        user_profile_id: assignment.user_profile_id,
      });
    }
    if (!planKeys.has(assignment.plan_key)) {
      reporter.red('assignment_unknown_plan', 'Plan assignment references an unknown plan key.', {
        assignment_id: assignment.id,
        plan_key: assignment.plan_key,
      });
    }
    if (assignment.status === 'active' && assignment.ends_at && Date.parse(assignment.ends_at) <= Date.now()) {
      reporter.red('expired_assignment_still_active', 'Expired plan assignment is still marked active.', {
        assignment_id: assignment.id,
        plan_key: assignment.plan_key,
        ends_at: assignment.ends_at,
      });
    }
  }

  for (const profile of profiles) {
    const userAssignments = assignmentsByProfile.get(profile.id) || [];
    const activeRows = userAssignments.filter((row) => row.status === 'active');
    const pendingRows = userAssignments.filter((row) => row.status === 'pending');
    const currentActiveRows = activeRows.filter(isCurrentlyActive);
    const latestActive = latestByCreated(currentActiveRows);
    const latestPending = latestByCreated(pendingRows);
    const mirror = profile.auth_user_id ? mirrorsByAuthId.get(profile.auth_user_id) : null;
    const userDevices = devicesByProfile.get(profile.id) || [];
    const activeDevices = userDevices.filter((device) => device.status === 'active');
    const userAudits = auditsByTarget.get(profile.id) || [];

    if (activeRows.length > 1) {
      reporter.red('multiple_active_assignments', 'User has more than one active assignment.', {
        email: profile.email,
        active_count: activeRows.length,
        plans: activeRows.map((row) => row.plan_key),
      });
    }

    if (pendingRows.length > 1) {
      reporter.red('multiple_pending_assignments', 'User has more than one pending plan request.', {
        email: profile.email,
        pending_count: pendingRows.length,
        plans: pendingRows.map((row) => row.plan_key),
      });
    }

    if (profile.account_status === 'active' && !latestActive) {
      reporter.yellow('active_account_without_active_plan', 'Account is active but has no current active plan.', {
        email: profile.email,
        pending_plan: latestPending?.plan_key || null,
      });
    }

    if (profile.account_status === 'suspended' && latestActive) {
      reporter.red('suspended_account_with_active_plan', 'Suspended account still has an active plan.', {
        email: profile.email,
        plan_key: latestActive.plan_key,
      });
    }

    if (activeDevices.length > 2) {
      reporter.red('device_limit_exceeded', 'User has more than 2 active devices.', {
        email: profile.email,
        active_devices: activeDevices.length,
      });
    }

    const activeFingerprints = activeDevices.map((device) => String(device.device_fingerprint || '').trim().toLowerCase()).filter(Boolean);
    if (new Set(activeFingerprints).size !== activeFingerprints.length) {
      reporter.red('duplicate_active_device_fingerprint', 'User has duplicate active device fingerprints.', {
        email: profile.email,
      });
    }

    if (profile.auth_user_id && !mirror) {
      reporter.yellow('missing_profile_mirror', 'Authenticated user has no compatibility profiles row.', {
        email: profile.email,
        auth_user_id: profile.auth_user_id,
      });
    }

    if (mirror) {
      const expectedMirrorStatus = profile.account_status === 'suspended'
        ? 'suspended'
        : latestActive
          ? 'active'
          : latestPending
            ? 'pending'
            : 'free';
      const expectedMirrorPlan = latestActive?.plan_key || 'free';
      const expectedRequestedPlan = latestPending?.plan_key || '';

      if ((mirror.status || 'free') !== expectedMirrorStatus) {
        reporter.red('mirror_status_mismatch', 'profiles.status does not match canonical entitlement state.', {
          email: profile.email,
          mirror_status: mirror.status,
          expected_status: expectedMirrorStatus,
        });
      }
      if ((mirror.plan || 'free') !== expectedMirrorPlan) {
        reporter.red('mirror_plan_mismatch', 'profiles.plan does not match the active canonical plan.', {
          email: profile.email,
          mirror_plan: mirror.plan,
          expected_plan: expectedMirrorPlan,
        });
      }
      if ((mirror.requested_plan || '') !== expectedRequestedPlan) {
        reporter.yellow('mirror_requested_plan_mismatch', 'profiles.requested_plan does not match pending canonical request.', {
          email: profile.email,
          mirror_requested_plan: mirror.requested_plan || '',
          expected_requested_plan: expectedRequestedPlan,
        });
      }
    }

    if ((activeRows.length || pendingRows.length) && !userAudits.length) {
      reporter.yellow('missing_audit_history', 'User has plan state but no recent admin audit rows in the sampled audit log.', {
        email: profile.email,
      });
    }
  }

  reporter.green('audit_completed', 'Entitlement audit completed.', {
    profiles: profiles.length,
    assignments: assignments.length,
    devices: devices.length,
    mirror_profiles: mirrorProfiles.length,
    audit_rows_sampled: auditLogs.length,
  });

  const summary = reporter.summary({
    profiles: profiles.length,
    assignments: assignments.length,
    devices: devices.length,
  });
  console.log(JSON.stringify(summary, null, 2));
  if (!summary.ok) process.exit(1);
}

main().catch((error) => {
  console.error(`[audit-entitlements] ${error.message}`);
  process.exit(1);
});
