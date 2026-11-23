"""Tests for pool_supervisor.py (the acting supervisor loop).

Run:  VIDZEO_RENDER_GATE=off python3 -m pytest test_pool_supervisor.py -q
All spool/state dirs are tmp; the render gate is a stub node script.
"""
import json
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pool_supervisor as ps


def _gate(tmp, active=False):
    p = os.path.join(tmp, "gate-active.js" if active else "gate-idle.js")
    with open(p, "w") as f:
        if active:
            f.write('console.log("render ACTIVE: test"); process.exit(2);\n')
        else:
            f.write('console.log("no render active"); process.exit(0);\n')
    return p


class Env:
    def __init__(self, tmp_path, active=False):
        self.spool = os.path.join(tmp_path, "spool")
        self.state = os.path.join(tmp_path, "state")
        os.makedirs(self.spool)
        os.makedirs(self.state)
        self.clock = [1700000000.0]
        self.sup = ps.Supervisor(
            spool=self.spool, state_dir=self.state,
            render_gate=_gate(tmp_path, active),
            supervisor_id="test-supervisor",
            brief_dir=os.path.join(tmp_path, "briefs"),
            clock=lambda: self.clock[0])
        os.makedirs(os.path.join(tmp_path, "briefs"), exist_ok=True)

    def advance(self, secs):
        self.clock[0] += secs

    def hb(self, wid, answered=0, age_s=0, pending=0):
        with open(os.path.join(
                self.spool, "worker-alive-%s.json" % wid), "w") as f:
            json.dump({"updatedAt": int((self.clock[0] - age_s) * 1000),
                       "worker": "resident-inference", "workerId": wid,
                       "pending": pending, "answered": answered}, f)

    def req(self, rid, expires_in=600, claimed_by=None):
        name = rid + ".request.json"
        if claimed_by:
            name += ".claimed-" + claimed_by
        with open(os.path.join(self.spool, name), "w") as f:
            json.dump({"request": {"id": rid, "job_id": "j",
                                   "operation": "op",
                                   "expires_at": self.clock[0] + expires_in}},
                      f)
        return os.path.join(self.spool, name)

    def tick(self):
        self.sup.n += 1
        return self.sup.tick(self.sup.n)

    def actions(self, status=None):
        acts = self.sup.load_actions()
        if status:
            acts = [a for a in acts if a.get("status") == status]
        return acts

    def status(self):
        try:
            with open(os.path.join(self.spool, "pool-status.json")) as f:
                return json.load(f)
        except OSError:
            return None


@pytest.fixture
def env(tmp_path):
    return Env(str(tmp_path))


def test_target_formula(env):
    env.hb("worker-1", answered=1)
    env.hb("worker-2", answered=1)
    keep, info = env.tick()
    assert keep and info["target"] == 2 and info["pending"] == 0
    for i in range(7):
        env.req("r%02d" % i)
    keep, info = env.tick()
    # Media floor is 4 (pool_scaling.py): ceil(7/3)=3 lifts to 4.
    assert info["pending"] == 7 and info["target"] == 4
    for i in range(7, 25):
        env.req("r%02d" % i)
    keep, info = env.tick()
    assert info["pending"] == 25 and info["target"] == 8


def test_pending_excludes_expired_and_claimed(env):
    env.hb("worker-1")
    env.req("live1")
    env.req("dead1", expires_in=-10)
    env.req("claimed1", claimed_by="worker-1")
    keep, info = env.tick()
    assert info["pending"] == 1


def test_death_needs_confirmation_and_queues_same_worker_id(env):
    env.hb("worker-1", answered=5)
    env.hb("worker-2", answered=3, age_s=100)  # stale >90s: sighting only
    keep, info = env.tick()
    assert env.actions("pending") == []
    assert "worker-2" in env.sup.state.get("death_sightings", {})
    env.advance(200)  # past the 180s confirmation window, still stale
    env.hb("worker-1", answered=6)
    keep, info = env.tick()
    acts = env.actions("pending")
    assert [a["action"] for a in acts] == ["respawn"]
    assert acts[0]["workerId"] == "worker-2"
    assert "death #1" in acts[0]["reason"]
    assert env.status()["workers"] == 1


def test_death_sighting_clears_when_heartbeat_freshens(env):
    # 2026-11-19 06:22 UTC false death: healthy worker, ~2 min poll gap.
    env.hb("worker-1", answered=5)
    env.hb("worker-2", answered=3, age_s=100)
    env.tick()
    assert "worker-2" in env.sup.state.get("death_sightings", {})
    env.advance(60)
    env.hb("worker-2", answered=4)  # agent restarted its poll loop
    env.tick()
    assert "worker-2" not in env.sup.state.get("death_sightings", {})
    assert env.actions("pending") == []


def test_death_at_target_queues_nothing(env):
    # worker-3 has been dead for 31h, but live=2/target=2: the pool is not
    # short, so the death is counted and no respawn is queued (a queued
    # action nobody should act on would wrongly trip the owner tripwire).
    env.hb("worker-1", answered=5)
    env.hb("worker-2", answered=3)
    env.hb("worker-3", answered=26, age_s=200)
    env.tick()
    env.advance(200)
    env.hb("worker-1", answered=6)
    env.hb("worker-2", answered=4)
    env.tick()
    assert env.actions("pending") == []
    assert env.sup.state["deaths"]["worker-3"]["count"] == 1
    assert env.status()["workers"] == 2


def test_count_rule_respawn_when_short_then_scale_up_when_target_rises(
        env):
    # Count-based slot rule (2026-11-21): a dead worker is respawned
    # whenever the pool is SHORT (live + incoming < target), whatever
    # its slot NUMBER. worker-3 dead, live=1, target=2: respawn
    # worker-3 at once (same workerId) — the old slot-number rule left
    # it unfilled at idle and its cancellation then blocked scale-up
    # after the target rose. When backlog lifts target to 4, scale-up
    # fills the rest (worker-2, worker-4) alongside the respawn.
    env.hb("worker-1", answered=5)
    env.hb("worker-3", answered=26, age_s=200)
    env.tick()  # sighting
    env.advance(200)
    env.hb("worker-1", answered=6)
    env.tick()  # confirmed death
    assert env.sup.state["deaths"]["worker-3"]["count"] == 1
    assert [(a["action"], a["workerId"]) for a in env.actions("pending")] \
        == [("respawn", "worker-3")]
    # The scale-up signal names the owed respawn the same tick.
    with open(os.path.join(env.spool,
                           "supervisor-scaleup-needed.json")) as f:
        signal = json.load(f)
    assert signal["live"] == 1 and signal["target"] == 2
    assert [s["workerId"] for s in signal["spawns"]] == ["worker-3"]
    for i in range(7):
        env.req("r%02d" % i)  # pending 7 -> target 4 (media floor)
    env.hb("worker-1", answered=7)
    env.tick()
    assert [(a["action"], a["workerId"]) for a in env.actions("pending")] \
        == [("respawn", "worker-3"), ("spawn", "worker-2"),
            ("spawn", "worker-4")]


def test_false_death_log_guard_busy_worker_not_dead(env):
    # Heartbeat starves while a worker answers back-to-back: fresh
    # ANSWERED lines in worker.log mean busy, not dead.
    env.hb("worker-1", answered=5)
    env.hb("worker-2", answered=3, age_s=100)
    with open(os.path.join(env.spool, "worker.log"), "w") as f:
        f.write("2023-11-14 22:13:20 UTC [worker-2] ANSWERED "
                "abc123 visual_review 12.0s\n")
    env.tick()  # sighting
    env.advance(200)
    env.hb("worker-1", answered=6)
    env.tick()  # confirm would fire — log guard must suppress it
    assert "worker-2" not in env.sup.state.get("deaths", {})
    assert [a for a in env.actions("pending")
            if a["workerId"] == "worker-2"] == []
    # Scale-up must not fill the gap with another worker either: the
    # busy worker-2 counts as capacity (it answered recently and will
    # heartbeat again), so the pool is effectively at target. No action
    # is queued at all; once the busy marker expires the death path
    # re-evaluates worker-2.
    assert env.actions("pending") == []


def test_continuous_death_counted_once(env):
    # One continuous outage is ONE death: after confirmation the worker
    # is not re-sighted, so it cannot accrue 3 "deaths" in 30 minutes
    # and trip the hold on a single outage (worker-3, 2026-11-19).
    env.hb("worker-1", answered=5)
    env.hb("worker-2", answered=3, age_s=100)
    env.tick()
    env.advance(200)
    env.hb("worker-1", answered=6)
    env.tick()
    assert env.sup.state["deaths"]["worker-2"]["count"] == 1
    for _ in range(3):
        env.advance(200)
        env.hb("worker-1", answered=6)
        env.tick()
    assert env.sup.state["deaths"]["worker-2"]["count"] == 1
    assert len([a for a in env.actions("pending")
                if a["action"] == "respawn"]) == 1
    assert env.status()["heldSlots"] == []


def test_death_with_live_claim_is_not_a_death(env):
    env.hb("worker-1", answered=5)
    env.hb("worker-2", answered=3, age_s=500)
    env.req("big1", expires_in=3600, claimed_by="worker-2")
    env.advance(400)
    env.tick()
    # No death sighting and no respawn for the claimed worker (a live claim
    # means slow, not dead). A scale-up spawn for the *other* missing slot
    # is legitimate and is not a respawn.
    assert "worker-2" not in env.sup.state.get("death_sightings", {})
    assert [a for a in env.actions("pending")
            if a["action"] == "respawn"] == []


def test_three_deaths_hold_the_slot(env):
    for round_no in range(3):
        env.hb("worker-1", answered=5)  # live worker keeps heartbeating
        env.hb("worker-2", answered=3, age_s=100)
        env.tick()  # sighting only; slot reserved, no spawn of another id
        assert env.actions("pending") == []
        env.advance(200)
        env.hb("worker-1", answered=5)
        env.hb("worker-2", answered=3, age_s=400)
        env.tick()  # confirmed death
        if round_no < 2:
            acts = env.actions("pending")
            assert [(a["action"], a["workerId"]) for a in acts] == [
                ("respawn", "worker-2")]
            env.hb("worker-1", answered=5)
            env.hb("worker-2", answered=3)  # slot recovers; action cancels
            env.tick()
            assert env.actions("pending") == []
            env.advance(60)
    # Third death in 30 min: no respawn of the held slot itself — but
    # the pool still fills to the FULL target via another free slot
    # (2026-11-21: a held slot no longer shrinks the target to
    # target-minus-one; it is skipped, and worker-3 takes its place).
    assert [(a["action"], a["workerId"]) for a in env.actions("pending")] \
        == [("spawn", "worker-3")]
    assert env.status()["heldSlots"] == ["worker-2"]
    assert env.sup.state["deaths"]["worker-2"]["count"] == 3
    assert env.status()["ownerResponsive"] is True


def test_scale_up_queues_lowest_free_ids(env):
    env.hb("worker-1", answered=1)
    env.hb("worker-2", answered=1)
    for i in range(7):
        env.req("r%02d" % i)
    env.tick()
    # Pending 7 -> target 4 (media floor), live 2: two spawns owed.
    acts = env.actions("pending")
    assert [(a["action"], a["workerId"]) for a in acts] == [
        ("spawn", "worker-3"), ("spawn", "worker-4")]
    for i in range(7, 25):
        env.req("r%02d" % i)
    env.tick()
    acts = env.actions("pending")
    assert [(a["action"], a["workerId"]) for a in acts] == [
        ("spawn", "worker-3"), ("spawn", "worker-4"), ("spawn", "worker-5"),
        ("spawn", "worker-6"), ("spawn", "worker-7"), ("spawn", "worker-8")]


def test_render_active_target_zero_and_no_spawns(env, tmp_path):
    busy = Env(str(tmp_path / "busy"), active=True)
    busy.hb("worker-1", answered=1)
    busy.hb("worker-2", answered=1)
    for i in range(9):
        busy.req("r%02d" % i)
    keep, info = busy.tick()
    assert info["target"] == 0 and info["render_active"] is True
    # Two-tick confirmation (2026-11-21 stop race): the first ACTIVE
    # sighting records the render but queues no stop — one tick's
    # reading must not stop the pool.
    assert busy.actions("pending") == []
    keep, info = busy.tick()
    assert info["target"] == 0 and info["render_active"] is True
    acts = busy.actions("pending")
    assert all(a["action"] == "stop_graceful" for a in acts)
    assert {a["workerId"] for a in acts} == {"worker-1", "worker-2"}


def test_render_active_flap_queues_no_stop(env, tmp_path):
    # Nov 21 04:52 UTC: a single ACTIVE tick queued stops, the render
    # ended before the next tick, and both workers had already exited.
    # ACTIVE on one tick and idle on the next must queue nothing.
    flap = Env(str(tmp_path / "flap"), active=True)
    flap.hb("worker-1", answered=1)
    flap.hb("worker-2", answered=1)
    keep, info = flap.tick()
    assert info["render_active"] is True
    assert flap.actions("pending") == []
    flap.sup.render_gate = _gate(str(tmp_path), active=False)
    keep, info = flap.tick()
    assert info["render_active"] is False
    assert flap.actions("pending") == []
    assert "render_active_since_ms" not in flap.sup.state


def _log_line(clock, wid, text):
    return "%s UTC [%s] %s\n" % (
        time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(clock)), wid, text)


def _queue_stops_active(env, tmp_path):
    """Drive an active-gate Env through both confirmation ticks and
    return its pending stop_graceful actions."""
    env.tick()  # first ACTIVE sighting: no stop yet
    env.tick()  # confirmed ACTIVE: stops queued
    acts = env.actions("pending")
    assert all(a["action"] == "stop_graceful" for a in acts)
    return acts


def test_reconcile_does_not_cancel_seen_stop(env, tmp_path):
    # A stop the worker already saw (STOP line with the action id, as
    # worker-2 logged on Nov 21) must stay pending when the render
    # ends — self-heal marks it done once the worker has exited — not
    # be deleted by reconcile.
    busy = Env(str(tmp_path / "seen"), active=True)
    busy.hb("worker-1", answered=1)
    busy.hb("worker-2", answered=1)
    acts = _queue_stops_active(busy, tmp_path)
    assert {a["workerId"] for a in acts} == {"worker-1", "worker-2"}
    stop1 = [a for a in acts if a["workerId"] == "worker-1"][0]
    with open(os.path.join(busy.spool, "worker.log"), "w") as f:
        f.write(_log_line(busy.clock[0], "worker-1", "STOP " + stop1["id"]))
    busy.sup.render_gate = _gate(str(tmp_path), active=False)
    busy.tick()  # gate idle: reconcile runs
    remaining = busy.actions("pending")
    assert [a["id"] for a in remaining] == [stop1["id"]]
    # worker-2's stop was never seen: retained as a tombstone, not deleted.
    cancelled = [a for a in busy.sup.load_actions()
                 if a.get("status") == "cancelled"]
    assert len(cancelled) == 1 and cancelled[0]["workerId"] == "worker-2"


def test_reconcile_retains_cancelled_stop_as_tombstone(env, tmp_path):
    # A stop no worker saw is cancelled when the render ends — but the
    # entry is RETAINED with status "cancelled" (auditable in the queue
    # file) instead of deleted as before (Nov 21: only a log line
    # recorded the cancellation).
    busy = Env(str(tmp_path / "tomb"), active=True)
    busy.hb("worker-1", answered=1)
    busy.hb("worker-2", answered=1)
    acts = _queue_stops_active(busy, tmp_path)
    assert len(acts) == 2
    busy.sup.render_gate = _gate(str(tmp_path), active=False)
    busy.tick()
    assert busy.actions("pending") == []
    all_acts = busy.sup.load_actions()
    assert len(all_acts) == 2
    for a in all_acts:
        assert a["status"] == "cancelled"
        assert a.get("cancelledAtMs") == busy.sup.now_ms()
        assert a["detail"]["cancelledReason"] == \
            "render ended, worker stays"
    # Tombstones are pruned once a day old, so the file does not grow
    # one entry per render flap forever.
    busy.advance(25 * 3600)
    busy.hb("worker-1", answered=2)
    busy.hb("worker-2", answered=2)
    busy.tick()
    # (The 25h jump also ages worker-1 past the 12h rotation bar, so a
    # rotate action may be pending — the tombstones themselves are gone.)
    assert [a for a in busy.sup.load_actions()
            if a.get("status") == "cancelled"] == []


def test_stop_race_exit_reheals_immediately(env):
    # Nov 21: a worker obeyed a stop_graceful (STOPPED line with the
    # action id) and exited; the pool must queue its respawn on the
    # next tick, not after the 180s death-confirmation wait — and the
    # seen stop itself must stay pending for self-heal to mark done.
    env.hb("worker-1", answered=5)
    env.hb("worker-2", answered=3, age_s=100)  # stale heartbeat
    stop_id = "stop_graceful-worker-2-1700000000000"
    env.sup.save_actions([{
        "id": stop_id, "action": "stop_graceful", "workerId": "worker-2",
        "reason": "render ACTIVE", "createdAtMs": env.sup.now_ms(),
        "status": "pending", "briefPath": "b", "supervisorBriefPath": "s",
        "spoolDir": env.spool, "detail": {}}])
    with open(os.path.join(env.spool, "worker.log"), "w") as f:
        f.write(_log_line(env.clock[0], "worker-2", "STOPPED " + stop_id))
    keep, info = env.tick()
    respawns = [a for a in env.actions("pending")
                if a["action"] == "respawn"]
    assert len(respawns) == 1 and respawns[0]["workerId"] == "worker-2"
    assert env.sup.state["deaths"]["worker-2"]["count"] == 1
    assert "worker-2" not in env.sup.state.get("death_sightings", {})
    assert [a["id"] for a in env.actions("pending")
            if a["action"] == "stop_graceful"] == [stop_id]


def test_stop_race_exit_without_stop_line_still_waits(env):
    # Control for the test above: a stale heartbeat with no STOPPED
    # line is only a death sighting on the first tick.
    env.hb("worker-1", answered=5)
    env.hb("worker-2", answered=3, age_s=100)
    env.tick()
    assert "worker-2" in env.sup.state.get("death_sightings", {})
    assert [a for a in env.actions("pending")
            if a["action"] == "respawn"] == []


def test_reconcile_cancels_spawn_when_worker_recovers(env):
    env.hb("worker-1", answered=1)
    env.hb("worker-2", answered=1)
    for i in range(7):
        env.req("r%02d" % i)
    env.tick()
    # Pending 7 -> target 4: two spawns queued (worker-3, worker-4).
    assert [a["workerId"] for a in env.actions("pending")] == [
        "worker-3", "worker-4"]
    for i in range(7):
        os.remove(os.path.join(env.spool, "r%02d.request.json" % i))
    # Slot filled by a live worker; all three busy (pending=1) so no
    # scale-down stop is queued either.
    env.hb("worker-1", answered=1, pending=1)
    env.hb("worker-2", answered=1, pending=1)
    env.hb("worker-3", answered=0, pending=1)
    env.tick()
    assert env.actions("pending") == []
    assert env.status()["target"] == 2


def test_sweep_deletes_expired_and_rescues_stale_claim(env):
    env.hb("worker-1", answered=1)
    env.hb("worker-2", answered=3, age_s=500)  # dead worker
    env.req("expired", expires_in=-5)
    env.req("expired-claim", expires_in=-5, claimed_by="worker-2")
    stale = env.req("stale-claim", expires_in=3600, claimed_by="worker-2")
    os.utime(stale, (env.clock[0] - 400, env.clock[0] - 400))
    # Claim age is time since the CLAIM, not the request file's mtime:
    # the stale claim needs a CLAIMED log line 400s old (what a real
    # worker writes in the same command as the rename). The mtime
    # above is the request's creation time and proves nothing.
    with open(os.path.join(env.spool, "worker.log"), "w") as f:
        f.write(time.strftime("%Y-%m-%d %H:%M:%S UTC",
                              time.gmtime(env.clock[0] - 400))
                + " [worker-2] CLAIMED stale-claim\n")
    env.req("fresh-claim", expires_in=3600, claimed_by="worker-1")
    env.sup.n = 3  # next tick is a sweep tick (n % 4 == 0)
    env.tick()
    assert not os.path.exists(os.path.join(env.spool, "expired.request.json"))
    assert not os.path.exists(os.path.join(
        env.spool, "expired-claim.request.json.claimed-worker-2"))
    assert os.path.exists(os.path.join(
        env.spool, "stale-claim.request.json"))  # rescued back to pool
    assert os.path.exists(os.path.join(
        env.spool, "fresh-claim.request.json.claimed-worker-1"))


def test_sweep_does_not_rescue_fresh_claim_on_old_request(env):
    # 2026-11-21 rescue bug, reproduced exactly: a request created
    # 1446s ago but claimed only 111s ago was reported as 1446s old
    # (claim file mtime = request creation time — a rename preserves
    # mtime) and rescued mid-composition. Claim age is time since the
    # CLAIM: the fresh CLAIMED line (111s) must win over the old mtime.
    env.hb("worker-1", answered=1)  # live worker to take rescued work
    env.hb("worker-2", answered=3, age_s=500)  # dead claiming worker
    rid = "aa" * 16  # 32-hex request id
    env.req(rid, expires_in=3600, claimed_by="worker-2")
    claim_path = os.path.join(
        env.spool, rid + ".request.json.claimed-worker-2")
    os.utime(claim_path, (env.clock[0] - 1446, env.clock[0] - 1446))
    with open(os.path.join(env.spool, "worker.log"), "w") as f:
        f.write(time.strftime("%Y-%m-%d %H:%M:%S UTC",
                              time.gmtime(env.clock[0] - 111))
                + " [worker-2] CLAIMED " + rid + "\n")
    env.sup.n = 3  # next tick is a sweep tick (n % 4 == 0)
    env.tick()
    assert os.path.exists(claim_path)  # NOT rescued: claim is 111s old


def test_sweep_rescues_unlogged_old_claim_via_ctime(env):
    # No CLAIMED line (unlogged claims happen): the claim file's ctime
    # — which the rename updates — is the fallback. With a real clock
    # the ctime is fresh, so a claim created just now is not rescued
    # even though its request mtime is old. (A genuinely old ctime
    # cannot be faked in a test — utime does not move ctime — so this
    # proves the safe direction: unprovable age never rescues.)
    import time as _time
    env.clock[0] = _time.time()  # real clock: ctime is meaningful
    env.hb("worker-1", answered=1)
    env.hb("worker-2", answered=3, age_s=500)
    rid = "bb" * 16
    env.req(rid, expires_in=3600, claimed_by="worker-2")
    claim_path = os.path.join(
        env.spool, rid + ".request.json.claimed-worker-2")
    os.utime(claim_path, (env.clock[0] - 1446, env.clock[0] - 1446))
    env.sup.n = 3
    env.tick()
    assert os.path.exists(claim_path)  # NOT rescued: ctime is fresh


def test_claim_logged_at_matches_latest_line_only(env):
    # Latest CLAIMED line wins; a line for another worker or another
    # request does not count, and a longer id sharing this prefix does
    # not match.
    import calendar
    with open(os.path.join(env.spool, "worker.log"), "w") as f:
        f.write("2023-11-14 22:13:20 UTC [worker-2] CLAIMED aabbcc\n")
        f.write("2023-11-14 22:15:20 UTC [worker-1] CLAIMED aabbcc\n")
        f.write("2023-11-14 22:16:20 UTC [worker-2] CLAIMED aabbccdd\n")
        f.write("2023-11-14 22:18:20 UTC [worker-2] CLAIMED aabbcc\n")
    assert env.sup.claim_logged_at("worker-2", "aabbcc") == \
        calendar.timegm(time.strptime("2023-11-14 22:18:20",
                                      "%Y-%m-%d %H:%M:%S"))
    assert env.sup.claim_logged_at("worker-1", "aabbcc") == \
        calendar.timegm(time.strptime("2023-11-14 22:15:20",
                                      "%Y-%m-%d %H:%M:%S"))
    assert env.sup.claim_logged_at("worker-2", "missing") is None


def test_stuck_needs_sustained_pending_not_a_burst(env):
    # 2026-11-19 11:18 UTC drill: a 25-probe burst flipped pendingSeen on
    # a 21-minute-old frozen watch and both live workers were falsely
    # queued for replacement. A burst must not fire stuck; only pending
    # sustained for the full 10 minutes may.
    env.hb("worker-1", answered=10)
    with open(os.path.join(env.spool, "worker.log"), "w") as f:
        f.write("2023-01-01 00:00:00 UTC [worker-1] ANSWERED old\n")
    env.tick()  # watch starts, no pending
    env.advance(700)  # frozen for >10 min, but idle (nothing pending)
    env.hb("worker-1", answered=10)
    env.tick()
    env.req("burst1", expires_in=3600)  # sudden burst after long idle
    env.tick()
    assert [a for a in env.actions("pending")
            if a["action"] == "replace_stuck"] == []
    os.remove(os.path.join(env.spool, "burst1.request.json"))
    env.tick()
    assert [a for a in env.actions("pending")
            if a["action"] == "replace_stuck"] == []
    # Same worker, pending now sustained for the full window: it fires.
    env.req("p1", expires_in=3600)
    for _ in range(11):
        env.advance(60)
        env.hb("worker-1", answered=10)
        env.tick()
    acts = [a for a in env.actions("pending")
            if a["action"] == "replace_stuck"]
    assert len(acts) == 1 and acts[0]["workerId"] == "worker-1"


def test_stuck_worker_queued_for_replacement(env):
    env.hb("worker-1", answered=10)
    with open(os.path.join(env.spool, "worker.log"), "w") as f:
        # Dated before the fake clock (2023-11-14), so it is NOT recent.
        f.write("2023-01-01 00:00:00 UTC [worker-1] ANSWERED old\n")
    env.req("p1", expires_in=3600)  # pending work exists through the window
    env.tick()
    for _ in range(11):
        env.advance(60)
        env.hb("worker-1", answered=10)  # frozen count, fresh heartbeat
        env.tick()
    acts = [a for a in env.actions("pending")
            if a["action"] == "replace_stuck"]
    assert len(acts) == 1 and acts[0]["workerId"] == "worker-1"
    assert "pkill" in acts[0]["detail"]


def test_rotation_queued_after_50_answers_when_idle(env):
    # A worker that genuinely gains 50 answers (gradually, a few per
    # tick — a one-claim-at-a-time worker cannot gain dozens in one
    # tick) is rotated. (Was a single +51 jump in 60s; that jump is
    # unphysical and is now treated as a counter discontinuity by the
    # upward-jump guard — see test_upward_answered_jump_... — so the
    # threshold is exercised with realistic gradual gain instead.)
    env.hb("worker-1", answered=10)
    env.hb("worker-2", answered=0)
    env.tick()
    for answered in (15, 20, 25, 30, 35, 40, 45, 50, 55, 60):
        env.advance(15)
        env.hb("worker-1", answered=answered)
        env.hb("worker-2", answered=0)
        env.tick()
    acts = [a for a in env.actions("pending") if a["action"] == "rotate"]
    assert len(acts) == 1 and acts[0]["workerId"] == "worker-1"


def test_owner_tripwire_pauses_liveness_and_resumes_on_ack(env):
    env.hb("worker-1", answered=1)  # pool short (live 1 < target 2), so
    # a respawn action for dead worker-2 (slot 2, within target) is
    # genuinely owed and survives reconciliation; a worker-3 respawn
    # would be cancelled by the slot rule instead.
    env.hb("worker-2", answered=0, age_s=5000)
    stale_status = {"updatedAt": int((env.clock[0] - 900) * 1000),
                    "workers": 1, "target": 2, "pending": 0}
    with open(os.path.join(env.spool, "pool-status.json"), "w") as f:
        json.dump(stale_status, f)
    with open(os.path.join(env.spool, "supervisor-alive.json"), "w") as f:
        json.dump(stale_status, f)
    env.sup.save_actions([{
        "id": "respawn-worker-2-old", "action": "respawn",
        "workerId": "worker-2", "reason": "test",
        "createdAtMs": int((env.clock[0] - 700) * 1000),
        "status": "pending", "briefPath": "b", "supervisorBriefPath": "s",
        "spoolDir": env.spool, "detail": {}}])
    keep, info = env.tick()
    assert info["owner_unresponsive"] is True
    assert env.status() == stale_status  # neither file rewritten
    marker = os.path.join(env.spool, "supervisor-owner-unresponsive.json")
    assert os.path.exists(marker)
    # The owner acts: ack newer than the queued action -> writes resume.
    with open(os.path.join(env.spool, "supervisor-agent-ack.json"),
              "w") as f:
        json.dump({"updatedAt": env.sup.now_ms(),
                   "supervisorAgentId": "test-supervisor",
                   "actionsDone": []}, f)
    keep, info = env.tick()
    assert info["owner_unresponsive"] is False
    assert env.status()["updatedAt"] == env.sup.now_ms()
    assert not os.path.exists(marker)


def test_pid_handover_exits_when_another_live_loop_owns_it(env):
    env.hb("worker-1")
    with open(env.sup.pid_file, "w") as f:
        # pid 1 (init) is a different, live pid from this process.
        json.dump({"pid": 1, "supervisorAgentId": "other",
                   "startedAtMs": env.sup.now_ms()}, f)
    keep, info = env.tick()
    assert keep is False and info.get("handover") is True


def test_pid_takeover_when_pid_file_names_dead_pid(env):
    env.hb("worker-1")
    with open(env.sup.pid_file, "w") as f:
        json.dump({"pid": 999999, "supervisorAgentId": "dead",
                   "startedAtMs": env.sup.now_ms()}, f)
    keep, info = env.tick()
    assert keep is True
    with open(env.sup.pid_file) as f:
        assert json.load(f)["pid"] == os.getpid()


def test_parked_exits(env):
    with open(os.path.join(env.spool, "supervisor-parked.json"), "w") as f:
        json.dump({"parkedAt": env.sup.now_ms(), "reason": "test"}, f)
    keep, info = env.tick()
    assert keep is False and info.get("parked") is True


def test_owner_ack_lease_expires_hollow_ack(env):
    # 2026-11-20: supervisor 761080e9 acked two pending spawn actions
    # and spawned nothing; the old tripwire treated ANY ack newer than
    # the action as "responsive" forever and the hook went silent while
    # the pool sat at 0. An ack is now a 180s lease: once it expires
    # with the action still pending, the tripwire fires again.
    env.hb("worker-1", answered=1)
    env.hb("worker-2", answered=0, age_s=5000)
    env.sup.save_actions([{
        "id": "respawn-worker-2-old", "action": "respawn",
        "workerId": "worker-2", "reason": "test",
        "createdAtMs": int((env.clock[0] - 700) * 1000),
        "status": "pending", "briefPath": "b", "supervisorBriefPath": "s",
        "spoolDir": env.spool, "detail": {}}])
    keep, info = env.tick()
    assert info["owner_unresponsive"] is True
    marker = os.path.join(env.spool, "supervisor-owner-unresponsive.json")
    assert os.path.exists(marker)
    with open(marker) as f:
        doc = json.load(f)
    # The escalation payload spells out the unexecuted spawns.
    assert [(a["action"], a["workerId"])
            for a in doc["pendingSpawnActions"]] == [
                ("respawn", "worker-2")]
    # Fresh ack -> responsive (owner is draining right now).
    with open(os.path.join(env.spool, "supervisor-agent-ack.json"),
              "w") as f:
        json.dump({"updatedAt": env.sup.now_ms(),
                   "supervisorAgentId": "test-supervisor",
                   "actionIds": ["respawn-worker-2-old"]}, f)
    keep, info = env.tick()
    assert info["owner_unresponsive"] is False
    # 200s later the ack's lease (180s) has expired and the action is
    # STILL pending: unresponsive again, liveness pauses again.
    env.advance(200)
    env.hb("worker-1", answered=2)
    keep, info = env.tick()
    assert info["owner_unresponsive"] is True
    assert os.path.exists(marker)


def test_owner_ack_naming_other_actions_does_not_cover_oldest(env):
    # An ack that lists actionIds only covers the actions it names.
    env.hb("worker-1", answered=1)
    env.hb("worker-2", answered=0, age_s=5000)
    env.sup.save_actions([{
        "id": "respawn-worker-2-old", "action": "respawn",
        "workerId": "worker-2", "reason": "test",
        "createdAtMs": int((env.clock[0] - 700) * 1000),
        "status": "pending", "briefPath": "b", "supervisorBriefPath": "s",
        "spoolDir": env.spool, "detail": {}}])
    with open(os.path.join(env.spool, "supervisor-agent-ack.json"),
              "w") as f:
        json.dump({"updatedAt": env.sup.now_ms(),
                   "supervisorAgentId": "test-supervisor",
                   "actionIds": ["some-other-action"]}, f)
    keep, info = env.tick()
    assert info["owner_unresponsive"] is True


def test_owner_ack_predating_action_is_ignored(env):
    # An ack written before the action was queued cannot cover it.
    env.hb("worker-1", answered=1)
    env.hb("worker-2", answered=0, age_s=5000)
    with open(os.path.join(env.spool, "supervisor-agent-ack.json"),
              "w") as f:
        json.dump({"updatedAt": int((env.clock[0] - 800) * 1000),
                   "supervisorAgentId": "test-supervisor",
                   "actionIds": ["respawn-worker-2-old"]}, f)
    env.sup.save_actions([{
        "id": "respawn-worker-2-old", "action": "respawn",
        "workerId": "worker-2", "reason": "test",
        "createdAtMs": int((env.clock[0] - 700) * 1000),
        "status": "pending", "briefPath": "b", "supervisorBriefPath": "s",
        "spoolDir": env.spool, "detail": {}}])
    keep, info = env.tick()
    assert info["owner_unresponsive"] is True


def test_dry_run_writes_nothing(tmp_path):
    spool = os.path.join(str(tmp_path), "spool")
    state = os.path.join(str(tmp_path), "state")
    os.makedirs(spool)
    os.makedirs(state)
    gate = _gate(str(tmp_path))
    with open(os.path.join(spool, "worker-alive-worker-1.json"), "w") as f:
        json.dump({"updatedAt": int(time.time() * 1000),
                   "worker": "resident-inference", "workerId": "worker-1",
                   "pending": 0, "answered": 1}, f)
    with open(os.path.join(spool, "r1.request.json"), "w") as f:
        json.dump({"request": {"id": "r1", "job_id": "j", "operation": "op",
                               "expires_at": time.time() + 600}}, f)
    env = dict(os.environ)
    env["MUSE_INFERENCE_SPOOL_DIR"] = spool
    env["POOL_SUPERVISOR_STATE_DIR"] = state
    env["POOL_SUPERVISOR_RENDER_GATE"] = gate
    import subprocess
    proc = subprocess.run(
        [sys.executable, os.path.join(os.path.dirname(
            os.path.abspath(__file__)), "pool_supervisor.py"), "--dry-run"],
        capture_output=True, text=True, env=env, timeout=30)
    assert proc.returncode == 0, proc.stderr
    doc = json.loads(proc.stdout)
    assert doc["shadow"] is True and doc["dry_run"] is True
    assert doc["pending"] == 1 and doc["live"] == ["worker-1"]
    assert not os.path.exists(os.path.join(spool, "pool-status.json"))
    assert not os.path.exists(os.path.join(spool,
                                           "supervisor-actions.json"))
    assert not os.path.exists(os.path.join(state, "supervisor-state.json"))
    assert not os.path.exists(os.path.join(state, "supervisor-loop.pid"))


def test_shadow_writes_nothing(tmp_path):
    spool = os.path.join(str(tmp_path), "spool")
    state = os.path.join(str(tmp_path), "state")
    os.makedirs(spool)
    os.makedirs(state)
    gate = _gate(str(tmp_path))
    with open(os.path.join(spool, "worker-alive-worker-1.json"), "w") as f:
        json.dump({"updatedAt": int(time.time() * 1000),
                   "worker": "resident-inference", "workerId": "worker-1",
                   "pending": 0, "answered": 1}, f)
    with open(os.path.join(spool, "r1.request.json"), "w") as f:
        json.dump({"request": {"id": "r1", "job_id": "j", "operation": "op",
                               "expires_at": time.time() + 600}}, f)
    env = dict(os.environ)
    env["MUSE_INFERENCE_SPOOL_DIR"] = spool
    env["POOL_SUPERVISOR_STATE_DIR"] = state
    env["POOL_SUPERVISOR_RENDER_GATE"] = gate
    import subprocess
    proc = subprocess.run(
        [sys.executable, os.path.join(os.path.dirname(
            os.path.abspath(__file__)), "pool_supervisor.py"), "--shadow"],
        capture_output=True, text=True, env=env, timeout=30)
    assert proc.returncode == 0, proc.stderr
    doc = json.loads(proc.stdout)
    assert doc["shadow"] is True and doc["pending"] == 1
    assert doc["live"] == ["worker-1"]
    assert not os.path.exists(os.path.join(spool, "pool-status.json"))
    assert not os.path.exists(os.path.join(spool,
                                           "supervisor-actions.json"))
    assert not os.path.exists(os.path.join(state, "supervisor-state.json"))


def test_respawned_worker_with_stale_slot_age_is_not_rotated(env):
    # Live bug 2026-11-20/21: worker-2 (agent c20f030a) was respawned
    # into the worker-2 slot and rotated ~4s after its first heartbeat,
    # because first_seen is keyed by SLOT and still carried the dead
    # incarnation's age (12.1h / 48 answers). A fresh worker must not
    # inherit the previous incarnation's rotation age.
    # (worker-1 is introduced fresh at the sighting tick so its own age
    # stays young: a pending rotate for ANY worker blocks all other
    # rotations, which would mask the bug this test reproduces.)
    env.hb("worker-2", answered=48)
    env.tick()  # first_seen recorded for the worker-2 slot
    env.advance(12.1 * 3600)  # old worker-2 ages past the rotate bar
    env.hb("worker-1", answered=0)  # fresh worker-1, age 0
    env.tick()  # death sighting for worker-2
    env.advance(200)
    env.hb("worker-1", answered=1)
    env.tick()  # death confirmed, respawn queued for worker-2
    assert [a for a in env.actions("pending")
            if a["action"] == "respawn"
            and a["workerId"] == "worker-2"]
    # Replacement agent spawns into the same slot, answered count fresh.
    env.hb("worker-2", answered=0)
    env.tick()
    acts = [a for a in env.actions("pending")
            if a["action"] == "rotate" and a["workerId"] == "worker-2"]
    assert acts == []


def test_worker_rotated_after_12h_age_when_idle(env):
    # Threshold guard: a worker that ITSELF ran ~12h (same incarnation,
    # only 10 answers) is still rotated. The fix must not move the bar.
    env.hb("worker-1", answered=10)
    env.hb("worker-2", answered=1)
    env.tick()
    env.advance(12 * 3600 + 60)
    env.hb("worker-1", answered=10)  # same agent, fresh heartbeat
    env.hb("worker-2", answered=1)
    env.tick()
    acts = [a for a in env.actions("pending")
            if a["action"] == "rotate" and a["workerId"] == "worker-1"]
    assert len(acts) == 1


def test_answered_drop_resets_rotation_age(env):
    # Fast replacement with no death gap: the new agent's heartbeat
    # overwrites the old file and its answered count restarts at 0.
    # A drop below the last-seen count means a new incarnation, so the
    # slot's rotation age resets instead of rotating the fresh worker.
    env.hb("worker-1", answered=48)
    env.hb("worker-2", answered=1)
    env.tick()  # first_seen recorded; worker-1 at 48 answers
    env.advance(12.1 * 3600)
    env.hb("worker-1", answered=0)  # replacement agent, count restarted
    env.hb("worker-2", answered=2)
    env.tick()
    acts = [a for a in env.actions("pending")
            if a["action"] == "rotate" and a["workerId"] == "worker-1"]
    assert acts == []
    # ...and the replacement is rotated once IT ages out, not before.
    env.advance(60)
    env.hb("worker-1", answered=0)
    env.hb("worker-2", answered=2)
    env.tick()
    acts = [a for a in env.actions("pending")
            if a["action"] == "rotate" and a["workerId"] == "worker-1"]
    assert acts == []


def test_upward_answered_jump_does_not_rotate_fresh_worker(env):
    # Live bug 2026-11-21: a fresh worker-1 was rotated ~15s after its
    # first heartbeat. Its heartbeat answered count jumped 0 -> 582 in
    # one tick — a stale/cumulative count (leftover loop, reseeded count
    # file) leaking into the fresh incarnation's heartbeat — and the
    # supervisor counted the whole jump as gain (582 >= 50) and queued
    # a rotate. The drop check cannot catch it (582 is not below the
    # last-seen 0). A one-claim-at-a-time worker cannot gain 582
    # answers in one ~15s tick, so an implausible upward jump is a
    # counter discontinuity, not work done: re-baseline instead of
    # rotating.
    env.hb("worker-1", answered=4)
    env.tick()  # first_seen recorded; worker-1 at 4 answers
    env.advance(12.1 * 3600)  # old worker-1 ages past the rotate bar
    env.hb("worker-1", answered=0)  # replacement agent, count restarted
    env.hb("worker-2", answered=0)  # fresh worker-2, age 0 (a pending
    # rotate for ANY worker blocks all other rotations, so worker-2
    # must stay young or it masks the rotation checked below)
    env.tick()  # drop detected (4 -> 0), rotation age reset
    # The fresh worker's heartbeat now reports the stale cumulative
    # count — the exact misfire.
    env.advance(15)  # one tick later
    env.hb("worker-1", answered=582)
    env.hb("worker-2", answered=0)
    env.tick()
    acts = [a for a in env.actions("pending")
            if a["action"] == "rotate" and a["workerId"] == "worker-1"]
    assert acts == []
    # ...and the replacement IS rotated once IT genuinely gains 50
    # answers (gradually, within the per-tick bound), not before.
    for gained in (587, 592, 597, 602, 607, 612, 617, 622, 627, 632):
        env.advance(15)
        env.hb("worker-1", answered=gained)
        env.hb("worker-2", answered=0)
        env.tick()
    acts = [a for a in env.actions("pending")
            if a["action"] == "rotate" and a["workerId"] == "worker-1"]
    assert len(acts) == 1


def test_legacy_first_seen_record_heals_on_answered_drop(env):
    # Records written before answeredLastSeen existed have no such key;
    # they fall back to answeredAtStart, so a slot whose first_seen
    # already went stale before this fix deployed still heals on the
    # first tick after the loop restarts onto the fix.
    env.hb("worker-1", answered=0)  # fresh agent in an old slot
    env.hb("worker-2", answered=1)
    env.sup.state["first_seen"] = {
        "worker-1": {"firstSeenMs": int((env.clock[0] - 12.1 * 3600) * 1000),
                     "answeredAtStart": 48}}
    env.tick()
    acts = [a for a in env.actions("pending")
            if a["action"] == "rotate" and a["workerId"] == "worker-1"]
    assert acts == []


def test_sighting_does_not_veto_scale_up_of_other_slots(env):
    # The 2026-11-21 live stall, reproduced: live 2 / target 4 /
    # pending 6, worker-3 HELD and worker-4's death only SIGHTED
    # (heartbeat stale, unconfirmed) — under the old rule the held
    # subtraction plus the sightings subtraction zeroed the need and
    # the loop queued NOTHING with ownerResponsive true. Now the
    # sighted slot reserves only itself: scale-up still fills the
    # other free slot (worker-5) in this tick, and the signal says
    # what blocks the rest.
    env.hb("worker-1", answered=5)
    env.hb("worker-2", answered=3)
    env.hb("worker-4", answered=1, age_s=100)  # stale: sighting only
    env.sup.state["held_slots"] = {
        "worker-3": {"heldAtMs": int(env.clock[0] * 1000), "deaths": 3}}
    for i in range(6):
        env.req("r%02d" % i)  # pending 6 -> target 4 (media floor)
    keep, info = env.tick()
    assert info["live"] == 2 and info["target"] == 4
    assert [(a["action"], a["workerId"]) for a in env.actions("pending")] \
        == [("spawn", "worker-5")]
    with open(os.path.join(env.spool,
                           "supervisor-scaleup-needed.json")) as f:
        signal = json.load(f)
    assert signal["live"] == 2 and signal["target"] == 4
    assert [s["workerId"] for s in signal["spawns"]] == ["worker-5"]
    assert any("worker-3" in b for b in signal["blocked"])
    assert any("worker-4" in b for b in signal["blocked"])


def test_stale_cancellation_requeued_when_target_rises(env):
    # A respawn cancelled at idle target 2 ("slot 3 above current
    # target") must be re-evaluated when the target rises: the death
    # path never re-queues a confirmed-dead slot, so the scale-up
    # block must. The cancelled entry stays cancelled (audit, not
    # resurrected); a NEW spawn action for the slot is queued.
    env.hb("worker-1", answered=5)
    env.hb("worker-2", answered=3)
    env.hb("worker-3", answered=26, age_s=1000)  # long dead
    env.sup.state["confirmed_dead"] = ["worker-3"]
    env.sup.state["deaths"] = {"worker-3": {
        "count": 1, "first": int(env.clock[0] * 1000),
        "last": int(env.clock[0] * 1000),
        "times": [int(env.clock[0] * 1000)]}}
    cancelled = {
        "id": "respawn-worker-3-123", "action": "respawn",
        "workerId": "worker-3",
        "reason": "heartbeat stale >90s, no claim (death #1)",
        "createdAtMs": int(env.clock[0] * 1000), "status": "cancelled",
        "briefPath": "b", "supervisorBriefPath": "s",
        "spoolDir": env.spool,
        "detail": {"cancelledBy": "supervisor (test)",
                   "reason": "standing policy: no respawn of worker-3 "
                             "while pool at idle target 2 (slot 3 above "
                             "current target); slot reserved for scale-up"}}
    env.sup.save_actions([cancelled])
    for i in range(6):
        env.req("r%02d" % i)  # pending 6 -> target 4
    env.tick()
    pending = [(a["action"], a["workerId"])
               for a in env.actions("pending")]
    assert ("spawn", "worker-3") in pending
    assert ("spawn", "worker-4") in pending
    assert env.sup.load_actions()[0]["status"] == "cancelled"


def test_held_slot_skipped_but_target_filled_via_higher_slots(env):
    # worker-3 held (3 deaths in 30 min) is never spawned into — but
    # the pool still reaches the FULL target 4 via worker-4 and
    # worker-5, not target-minus-one.
    env.hb("worker-1", answered=5)
    env.hb("worker-2", answered=3)
    env.sup.state["held_slots"] = {
        "worker-3": {"heldAtMs": int(env.clock[0] * 1000), "deaths": 3}}
    for i in range(6):
        env.req("r%02d" % i)
    env.tick()
    assert [(a["action"], a["workerId"]) for a in env.actions("pending")] \
        == [("spawn", "worker-4"), ("spawn", "worker-5")]


def test_reconcile_keeps_high_slot_spawn_when_pool_short(env):
    # A pending spawn for a high slot (worker-5) is NOT cancelled for
    # its slot number while the pool is short: the count (live 2 <
    # target 4) is what owes it. Scale-up adds the other missing slot
    # (worker-3) alongside it.
    env.hb("worker-1", answered=5)
    env.hb("worker-2", answered=3)
    for i in range(6):
        env.req("r%02d" % i)
    env.sup.save_actions([{
        "id": "spawn-worker-5-1", "action": "spawn", "workerId": "worker-5",
        "reason": "test", "createdAtMs": int(env.clock[0] * 1000),
        "status": "pending", "briefPath": "b",
        "supervisorBriefPath": "s", "spoolDir": env.spool,
        "detail": {}}])
    env.tick()
    assert [(a["action"], a["workerId"]) for a in env.actions("pending")] \
        == [("spawn", "worker-5"), ("spawn", "worker-3")]


def test_scaleup_signal_written_and_cleared(env):
    # Signal appears the tick spawns are queued and disappears the
    # tick the pool is back at target.
    env.hb("worker-1", answered=1)
    env.hb("worker-2", answered=1)
    for i in range(7):
        env.req("r%02d" % i)
    keep, info = env.tick()
    assert info["scaleup_signalled"] is True
    signal_path = os.path.join(env.spool,
                               "supervisor-scaleup-needed.json")
    with open(signal_path) as f:
        signal = json.load(f)
    assert signal["live"] == 2 and signal["target"] == 4
    assert [s["workerId"] for s in signal["spawns"]] == [
        "worker-3", "worker-4"]
    assert signal["blocked"] == []
    env.hb("worker-1", answered=2)
    env.hb("worker-2", answered=2)
    env.hb("worker-3", answered=0)
    env.hb("worker-4", answered=0)
    keep, info = env.tick()
    assert info["scaleup_signalled"] is False
    assert not os.path.exists(signal_path)
    assert env.actions("pending") == []


def test_render_active_writes_no_scaleup_signal(env, tmp_path):
    # Render ACTIVE: target 0, no spawns, no signal — the parent must
    # not be summoned to staff a pool the render gate owns.
    busy = Env(str(tmp_path / "busy"), active=True)
    busy.hb("worker-1", answered=1)
    busy.hb("worker-2", answered=1)
    for i in range(9):
        busy.req("r%02d" % i)
    keep, info = busy.tick()
    assert info["target"] == 0 and info["scaleup_signalled"] is False
    keep, info = busy.tick()
    assert busy.actions("pending") != []  # stops queued (two-tick)
    assert not os.path.exists(os.path.join(
        busy.spool, "supervisor-scaleup-needed.json"))
