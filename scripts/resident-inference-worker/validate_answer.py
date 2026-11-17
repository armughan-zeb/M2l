#!/usr/bin/env python3
"""Validate a resident inference worker's answer against its request schema.

The worker must run this BEFORE delivering an answer (before renaming the
tmp answer file into place). It mirrors the server-side check in
``vidnets/server/services/agent_bridge.py`` exactly: the same size/text
rules, and the same validator — ``_json_schema_error`` imported from
``vidnets/server/services/openrouter_llm.py`` — so a worker-side PASS
means the server will accept the answer, and a worker-side FAIL means
the server would have rejected it terminally.

Usage (run with the Vidnets venv python):
    validate_answer.py <answer-file> <request-file>

- <answer-file>: the tmp file holding the RAW answer (JSON text for
  structured answers, plain text otherwise).
- <request-file>: the spool request file (<id>.request.json, or the
  claimed <id>.request.json.claimed-<workerId> file) or a bridge
  request.json. The schema is read from its ``response_format``.

Exit codes:
    0  valid — safe to deliver (rename the tmp file into place).
    1  invalid — DO NOT DELIVER. The issue is printed on stdout
       (first 300 chars, same as the server). Fix the flagged fields
       to valid values from the schema and re-validate.
    2  cannot validate (missing/unreadable files, validator import
       failure) — DO NOT DELIVER. Fail closed: write an error file
       and log it instead of delivering an unvalidated answer.

A request with no schema (plain-text answer) is always valid.
"""

import json
import os
import sys

MAX_BYTES = 2_000_000


def _find_response_format(data):
    """Return the response_format dict from a spool or bridge request file."""
    if not isinstance(data, dict):
        return {}
    candidates = []
    inner = data.get("request")
    if isinstance(inner, dict):
        candidates.append(inner.get("response_format"))
        candidates.append(inner.get("responseFormat"))
    candidates.append(data.get("response_format"))
    candidates.append(data.get("responseFormat"))
    for cand in candidates:
        if isinstance(cand, dict) and cand:
            return cand
    return {}


def _load_json_schema_error():
    """Import the server's own schema validator (single source of truth).

    Imported as ``server.services.openrouter_llm`` (package import, not a
    bare top-level import): the module has a class-level relative import
    that fails when it is loaded as a top-level module.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.dirname(os.path.dirname(here))
    vidnets_dir = os.path.join(repo_root, "vidnets")
    if vidnets_dir not in sys.path:
        sys.path.insert(0, vidnets_dir)
    from server.services.openrouter_llm import _json_schema_error  # noqa: E402

    return _json_schema_error


def validate(answer_path, request_path):
    """Return (True, '') when the answer is deliverable, else (False, issue)."""
    try:
        with open(answer_path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        return False, "cannot read answer file: %s" % exc
    if not raw.strip():
        return False, "Agent response must be nonempty text or JSON under 2 MB."
    if len(raw) > MAX_BYTES:
        return False, "Agent response must be nonempty text or JSON under 2 MB."
    try:
        with open(request_path, "r", encoding="utf-8") as fh:
            request_data = json.load(fh)
    except (OSError, ValueError) as exc:
        return False, "cannot read request file: %s" % exc

    response_format = _find_response_format(request_data)
    contract = (response_format or {}).get("json_schema")
    if contract:
        try:
            value = json.loads(raw.decode("utf-8"))
        except ValueError:
            return False, "This request requires valid JSON matching its schema."
        try:
            json_schema_error = _load_json_schema_error()
        except Exception as exc:  # fail closed: never deliver unvalidated
            return False, "validator unavailable: %s" % exc
        issue = json_schema_error(value, contract.get("schema") or {})
        if issue:
            return False, "Agent response does not match the schema: " + str(issue)[:300]
        return True, ""
    if (response_format or {}).get("type") == "json_object":
        try:
            if not isinstance(json.loads(raw.decode("utf-8")), dict):
                raise ValueError()
        except ValueError:
            return False, "This request requires a JSON object."
    return True, ""


def main(argv):
    if len(argv) != 3:
        print("usage: validate_answer.py <answer-file> <request-file>", file=sys.stderr)
        return 2
    ok, issue = validate(argv[1], argv[2])
    if ok:
        print("valid")
        return 0
    print(issue)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
