"""Tests for WORKER_BRIEF.md (the resident inference worker spawn brief).

Run:  python3 -m pytest test_worker_brief.py -q
Doc-only: the brief is prose the worker agents read, not code — but the
rotate gap (2026-11-20: a queued rotate sat ~105 min because no worker
checked the action queue) was exactly a missing instruction, so these
tests pin the instruction in place. They assert the brief names the
action file and the exact fields the loop writes (pool_supervisor.py
queue_action), and that the graceful-stop rule still says finish the
current answer first.
"""
import os

import pytest

BRIEF = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "WORKER_BRIEF.md")


@pytest.fixture(scope="module")
def brief():
    with open(BRIEF) as f:
        # Collapse line wraps: the brief wraps prose at ~70 cols, so
        # assert on the unwrapped text, not on raw line breaks.
        return " ".join(f.read().split())


def test_brief_names_action_queue_file(brief):
    assert "supervisor-actions.json" in brief


def test_brief_names_exact_action_fields(brief):
    # Field names as written by pool_supervisor.py queue_action: the
    # match key is "action" (not "type"), the worker key is "workerId",
    # and a queued entry has status "pending".
    assert "`action`" in brief
    assert "`workerId`" in brief
    assert "`pending`" in brief


def test_brief_names_stop_action_values(brief):
    # The real stop vocabulary: rotate, stop_graceful, stop_idle (and
    # replace_stuck, which also means "you stop"). There is no bare
    # "stop" action.
    for action in ("rotate", "stop_graceful", "stop_idle",
                   "replace_stuck"):
        assert "`%s`" % action in brief


def test_brief_action_queue_is_read_only(brief):
    # The loop and the supervisor own the queue; a worker that also
    # wrote it would race the loop's atomic rewrites.
    assert "Read the file only" in brief
    assert "never claim, edit, or mark the entry done" in brief


def test_brief_stop_rule_still_graceful(brief):
    # The queue extends "the parent tells you to" — it must not weaken
    # the finish-current-answer-first rule.
    assert "finish the current answer" in brief
    assert "delete your claim file if the answer was not delivered" \
        in brief
    assert "STOPPED" in brief
