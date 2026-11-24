"""Tests for pool_selfheal.py (the pool-selfheal cron decision core).

Run:  VIDZEO_RENDER_GATE=off python3 -m pytest test_pool_selfheal.py -q
All spool/state dirs are tmp; the render gate is a stub node script.
The planner never spawns anything and --check/--dry-run never write.
"""
import json
import os
import subprocess
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pool_selfheal as sh


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
        self.tmp = str(tmp_path)
        self.spool = os.path.join(self.tmp, "spool")
        self.state = os.path.join(self.tmp, "state")
        os.makedirs(self.spool)
        os.makedirs(self.state)
        self.now = time.time()
        self.gate = _gate(self.tmp, active)
        self.brief_dir = os.path.join(self.tmp, "briefs")
        os.makedirs(self.brief_dir, exist_ok=True)
        with open(os.path.join(self.brief_dir, "WORKER_BRIEF.md"),
                  "w") as f:
            f.write("# worker brief\n")

    def hb(self, wid, answered=0, age_s=0, pending=0):
        with open(os.path.join(
                self.spool, "worker-alive-%s.json" % wid), "w") as f:
            json.dump({"updatedAt": int((self.now - age_s) * 1000),
                       "worker": "resident-inference", "workerId": wid,
                       "pending": pending, "answered": answered}, f)

    def req(self, rid, expires_in=600):
        with open(os.path.join(self.spool, rid + ".request.json"),
                  "w") as f:
            json.dump({"request": {"id": rid, "job_id": "j",
                                   "operation": "op",
                                   "expires_at": self.now + expires_in}},
                      f)

    def queue(self, actions):
        with open(os.path.join(self.spool, "supervisor-actions.json"),
                  "w") as f:
            json.dump({"updatedAt": int(self.now * 1000),
                       "actions": actions}, f)

    def action(self, action, wid, age_s=0, detail=None, aid=None):
        return {
            "id": aid or "%s-%s-%d" % (action, wid,
                                       int((self.now - age_s) * 1000)),
            "action": action, "workerId": wid, "reason": "test",
            "createdAtMs": int((self.now - age_s) * 1000),
            "status": "pending", "briefPath": "b",
            "supervisorBriefPath": "s", "spoolDir": self.spool,
            "detail": detail or {}}

    def loop_pid(self, pid=None, age_s=0):
        pid = os.getpid() if pid is None else pid
        path = os.path.join(self.state, "supervisor-loop.pid")
        with open(path, "w") as f:
            json.dump({"pid": pid, "supervisorAgentId": "test",
                       "startedAtMs": int((self.now - age_s) * 1000)}, f)
        old = self.now - age_s
        os.utime(path, (old, old))

    def plan(self, **kw):
        return sh.plan(self.spool, self.state, self.gate, self.brief_dir,
                       now=self.now, **kw)


@pytest.fixture
def env(tmp_path):
    return Env(str(tmp_path))


def test_idle_pool_plans_two_spawns_and_loop_restart(env):
    # Nothing live, no loop pid file, gate idle: the 2026-11-20 shape.
    result = env.plan()
    assert result["target"] == 2 and result["live"] == []
    assert [s["workerId"] for s in result["spawns"]] == [
        "worker-1", "worker-2"]
    for spawn in result["spawns"]:
        assert spawn["briefPath"].endswith("WORKER_BRIEF.md")
        assert spawn["taskLine"].startswith(
            "Your workerId is %s. Spool dir is " % spawn["workerId"])
    assert result["loop"]["restart_needed"] is True
    assert result["loop"]["restart_reason"] == "no_pid_file"
    assert "pool_supervisor.py" in result["loop"]["start_command"]


def test_media_pending_plans_four_spawns(env):
    for i in range(9):
        env.req("r%02d" % i)
    result = env.plan()
    assert result["target"] == 4
    assert [s["workerId"] for s in result["spawns"]] == [
        "worker-1", "worker-2", "worker-3", "worker-4"]


def test_render_active_plans_no_spawns_but_loop_restart(env, tmp_path):
    busy = Env(str(tmp_path / "busy"), active=True)
    for i in range(9):
        busy.req("r%02d" % i)
    result = busy.plan()
    assert result["render_active"] is True
    assert result["target"] == 0
    assert result["spawns"] == []
    # Restarting the loop during a render is safe: the loop itself
    # respects the gate and never spawns while it is ACTIVE.
    assert result["loop"]["restart_needed"] is True


def test_freshness_reprobe_drops_spawns_when_render_starts(env, tmp_path):
    # Gate says idle on the first probe, ACTIVE on the re-probe: a
    # render started between the two reads — the 08:46 Nov 21 shape,
    # when test 19's render resumed after a VM reboot and the planner
    # spawned two workers the loop then had to stop.
    marker = os.path.join(str(tmp_path), "render-started")
    gate = os.path.join(str(tmp_path), "gate-flip.js")
    with open(gate, "w") as f:
        f.write(
            "const fs=require('fs');\n"
            "if (fs.existsSync(%s)) { console.log('render ACTIVE');"
            " process.exit(2); }\n"
            "fs.writeFileSync(%s, 'x');\n"
            "console.log('no render active'); process.exit(0);\n"
            % (json.dumps(marker), json.dumps(marker)))
    env.gate = gate
    result = env.plan()
    # First probe idle -> spawns were planned, then the freshness
    # re-probe saw the render start and dropped them all.
    assert result["render_active"] is True
    assert result["spawns"] == []
    assert "freshness re-probe" in " ".join(result["reasons"])
    assert "dropping" in (result["render_gate_note"] or "")


def test_freshness_reprobe_keeps_spawns_when_still_idle(env):
    # Gate idle on both probes: the normal path — spawns proceed.
    result = env.plan()
    assert result["render_active"] is False
    assert [s["workerId"] for s in result["spawns"]] == [
        "worker-1", "worker-2"]


def test_parked_plans_nothing(env):
    with open(os.path.join(env.spool, "supervisor-parked.json"),
              "w") as f:
        json.dump({"parkedAt": int(env.now * 1000), "reason": "test"}, f)
    result = env.plan()
    assert result["parked"] is True
    assert result["spawns"] == []
    assert result["loop"]["restart_needed"] is False
    assert result["mark_done"] == [] and result["mark_cancelled"] == []


def test_live_worker_adopted_not_respawned(env):
    env.hb("worker-1", answered=5)
    env.queue([env.action("respawn", "worker-1", age_s=700)])
    result = env.plan()
    assert [s["workerId"] for s in result["spawns"]] == ["worker-2"]
    assert [c["workerId"] for c in result["mark_cancelled"]] == ["worker-1"]


def test_queued_spawn_executed_not_duplicated_by_scale_up(env):
    env.queue([env.action("spawn", "worker-2", age_s=700)])
    result = env.plan()
    # Live 0, target 2: queued spawn worker-2 + scale-up worker-1.
    assert [s["workerId"] for s in result["spawns"]] == [
        "worker-2", "worker-1"]
    assert result["spawns"][0]["actionId"] is not None
    assert result["spawns"][1]["actionId"] is None


def test_held_slot_skipped_but_target_filled_via_other_slot(env):
    env.hb("worker-1", answered=5)
    with open(os.path.join(env.state, "supervisor-state.json"),
              "w") as f:
        json.dump({"held_slots": {"worker-2": {"heldAtMs": 0}}}, f)
    result = env.plan()
    # The held slot is never spawned — but the pool still fills to the
    # FULL target 2 via worker-3 (2026-11-21: a held slot no longer
    # shrinks the target to target-minus-one).
    assert result["target"] == 2
    assert [s["workerId"] for s in result["spawns"]] == ["worker-3"]


def test_spawn_cancelled_when_pool_at_target_not_for_slot_number(env):
    env.hb("worker-1", answered=5)
    env.hb("worker-2", answered=3)
    env.queue([env.action("spawn", "worker-3", age_s=700)])
    result = env.plan()
    # Pool at target (live 2 == target 2): the spawn is cancelled —
    # for the COUNT, not the slot number.
    assert result["spawns"] == []
    assert [c["workerId"] for c in result["mark_cancelled"]] == ["worker-3"]
    # ...while the same high-slot spawn IS executed when the pool is
    # short: a slot above target is owed whenever live < target.
    # (It alone fills the gap to target 2, so no second spawn is due.)
    env2 = Env(str(env.tmp) + "-short")
    env2.hb("worker-1", answered=5)
    env2.queue([env2.action("spawn", "worker-3", age_s=700)])
    result2 = env2.plan()
    assert [s["workerId"] for s in result2["spawns"]] == ["worker-3"]
    assert result2["mark_cancelled"] == []


def test_plan_marks_spawns_parent_only(env):
    # Spawns must be executed by a top-level parent agent: cron-spawned
    # workers die at the 300s timeout, so the plan labels itself.
    result = env.plan()
    assert result["spawn_executor"] == "parent-only"
    assert any("parent-only" in r for r in result["reasons"])
    assert any("300s" in r for r in result["reasons"])


def test_scale_up_fills_past_held_slot_to_full_target(env):
    # Live 2, held worker-3, pending 6 -> target 4: the pool fills to
    # 4 via worker-4 and worker-5, not 3.
    env.hb("worker-1", answered=5)
    env.hb("worker-2", answered=3)
    with open(os.path.join(env.state, "supervisor-state.json"),
              "w") as f:
        json.dump({"held_slots": {"worker-3": {"heldAtMs": 0}}}, f)
    for i in range(6):
        env.req("r%02d" % i)
    result = env.plan()
    assert result["target"] == 4
    assert [s["workerId"] for s in result["spawns"]] == [
        "worker-4", "worker-5"]


def test_write_scaleup_signal_roundtrip(env):
    # No workers, gate idle: the signal names the owed spawns; once
    # the pool is staffed, a second call clears it.
    result = sh.write_scaleup_signal_from_plan(
        env.spool, env.state, env.gate, env.brief_dir,
        executor_id="test-exec", now=env.now)
    assert result["signalled"] is True
    assert [s["workerId"] for s in result["spawns"]] == [
        "worker-1", "worker-2"]
    with open(os.path.join(env.spool,
                           "supervisor-scaleup-needed.json")) as f:
        doc = json.load(f)
    assert doc["live"] == 0 and doc["target"] == 2
    env.hb("worker-1", answered=1)
    env.hb("worker-2", answered=1)
    result2 = sh.write_scaleup_signal_from_plan(
        env.spool, env.state, env.gate, env.brief_dir,
        executor_id="test-exec", now=env.now)
    assert result2["signalled"] is False
    assert not os.path.exists(os.path.join(
        env.spool, "supervisor-scaleup-needed.json"))


def test_write_scaleup_signal_nothing_when_parked(env):
    with open(os.path.join(env.spool, "supervisor-parked.json"),
              "w") as f:
        json.dump({"parkedAt": int(env.now * 1000), "reason": "test"}, f)
    result = sh.write_scaleup_signal_from_plan(
        env.spool, env.state, env.gate, env.brief_dir,
        executor_id="test-exec", now=env.now)
    assert result["signalled"] is False
    assert result["parked"] is True
    assert not os.path.exists(os.path.join(
        env.spool, "supervisor-scaleup-needed.json"))


def test_lock_held_blocks_spawns(env):
    with open(os.path.join(env.state, "selfheal-lock.json"), "w") as f:
        json.dump({"agentId": "other-executor",
                   "startedAtMs": int(env.now * 1000)}, f)
    result = env.plan(executor_id="this-run")
    assert result["lock_held"] is True
    assert result["lock_holder"] == "other-executor"
    assert result["spawns"] == []


def test_stale_lock_does_not_block(env):
    with open(os.path.join(env.state, "selfheal-lock.json"), "w") as f:
        json.dump({"agentId": "dead-executor",
                   "startedAtMs": int((env.now - 900) * 1000)}, f)
    result = env.plan(executor_id="this-run")
    assert result["lock_held"] is False
    assert len(result["spawns"]) == 2


def test_loop_alive_no_restart(env):
    env.loop_pid(age_s=0)  # fresh pid file naming this live process
    result = env.plan()
    assert result["loop"]["alive"] is True
    assert result["loop"]["restart_needed"] is False
    assert result["loop"]["pid"] == os.getpid()


def test_loop_dead_pid_restart(env):
    env.loop_pid(pid=999999, age_s=0)
    result = env.plan()
    assert result["loop"]["alive"] is False
    assert result["loop"]["restart_reason"] == "pid_dead"
    assert result["loop"]["restart_needed"] is True


def test_loop_wedged_pid_file_stale_restart(env):
    # Pid alive but pid file not rewritten in >120s: the loop is wedged
    # (not ticking); a new loop takes over via the pid-file handover.
    env.loop_pid(age_s=600)
    result = env.plan()
    assert result["loop"]["alive"] is False
    assert result["loop"]["restart_reason"] == "pid_file_stale"


def test_stop_action_marked_done_when_worker_gone(env):
    env.hb("worker-1", answered=5)
    env.queue([env.action("stop_graceful", "worker-2", age_s=60)])
    result = env.plan()
    # The stop already took effect (worker-2's heartbeat is gone): mark
    # it done. Scale-up then refills the slot — live 1 < target 2.
    assert [d["workerId"] for d in result["mark_done"]] == ["worker-2"]
    assert [s["workerId"] for s in result["spawns"]] == ["worker-2"]


def test_scale_down_grace_cancels_stop(env):
    # A worker that ANSWERED in the last 300s is mid-job, not idle: the
    # queued stop_idle must be cancelled, not executed.
    env.hb("worker-1", answered=5)
    env.hb("worker-2", answered=3)
    with open(os.path.join(env.spool, "worker.log"), "w") as f:
        f.write(time.strftime(
            "%Y-%m-%d %H:%M:%S UTC", time.gmtime(env.now - 60))
            + " [worker-2] ANSWERED abc visual_review 12.0s\n")
    env.queue([env.action("stop_idle", "worker-2", age_s=60)])
    result = env.plan()
    assert [c["workerId"] for c in result["mark_cancelled"]] == ["worker-2"]
    assert "grace" in result["mark_cancelled"][0]["reason"]


def test_replace_stuck_spawns_after_stop_with_pkill(env):
    env.hb("worker-1", answered=10)
    env.hb("worker-2", answered=3)
    env.queue([env.action("replace_stuck", "worker-1", age_s=60,
                          detail={"pkill": ["pkill -f 'worker1/heartbeat.sh'"]})])
    result = env.plan()
    assert len(result["spawns"]) == 1
    spawn = result["spawns"][0]
    assert spawn["workerId"] == "worker-1"
    assert spawn["after_stop"] is True
    assert spawn["pkill_detached_heartbeat"] == [
        "pkill -f 'worker1/heartbeat.sh'"]
    # The replacement does not count toward scale-up: live 2, target 2.
    assert result["mark_done"] == []


def test_lock_and_mark_roundtrip(env):
    ok, holder = sh.acquire_lock(env.state, "exec-1", env.now)
    assert ok is True and holder == "exec-1"
    ok2, holder2 = sh.acquire_lock(env.state, "exec-2", env.now)
    assert ok2 is False and holder2 == "exec-1"
    env.queue([env.action("spawn", "worker-1", age_s=60, aid="spawn-1")])
    assert sh.mark_action(env.spool, "spawn-1", "done", "exec-1",
                          "spawned") is True
    assert sh.mark_action(env.spool, "spawn-1", "done", "exec-1") is False
    assert sh.release_lock(env.state, "exec-2") is False
    assert sh.release_lock(env.state, "exec-1") is True
    ok3, _ = sh.acquire_lock(env.state, "exec-2", env.now)
    assert ok3 is True


def test_dry_run_writes_nothing(tmp_path):
    spool = os.path.join(str(tmp_path), "spool")
    state = os.path.join(str(tmp_path), "state")
    os.makedirs(spool)
    os.makedirs(state)
    gate = _gate(str(tmp_path))
    with open(os.path.join(spool, "worker-alive-worker-1.json"),
              "w") as f:
        json.dump({"updatedAt": int(time.time() * 1000),
                   "worker": "resident-inference", "workerId": "worker-1",
                   "pending": 0, "answered": 1}, f)
    before = set(os.listdir(spool)) | set(os.listdir(state))
    env = dict(os.environ)
    env["MUSE_INFERENCE_SPOOL_DIR"] = spool
    env["POOL_SUPERVISOR_STATE_DIR"] = state
    env["POOL_WATCH_RENDER_GATE"] = gate
    proc = subprocess.run(
        [sys.executable, os.path.join(os.path.dirname(
            os.path.abspath(__file__)), "pool_selfheal.py"), "--dry-run"],
        capture_output=True, text=True, env=env, timeout=30)
    assert proc.returncode == 0, proc.stderr
    doc = json.loads(proc.stdout)
    assert doc["live"] == ["worker-1"] and doc["target"] == 2
    assert [s["workerId"] for s in doc["spawns"]] == ["worker-2"]
    assert doc["loop"]["restart_needed"] is True  # no pid file in tmp
    after = set(os.listdir(spool)) | set(os.listdir(state))
    assert after == before  # nothing created, nothing modified
