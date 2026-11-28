#!/usr/bin/env python3
"""Validate a resident inference worker's answer against its request schema.

The worker must run this BEFORE delivering an answer (before renaming the
tmp answer file into place). It mirrors the old server-side check in
``vidnets/server/services/agent_bridge.py``: the same size/text rules,
and the same validator — ``_json_schema_error`` vendored verbatim from
``vidnets/server/services/openrouter_llm.py`` (3be5190), which 92ab202
deleted along with the Vidnets server. A worker-side PASS means the
answer matches its schema, and a worker-side FAIL means it does not.

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
import math
import os
import re
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


def _json_schema_error(value, schema: dict, path: str = "$", max_errors: int = 1) -> str:
    """Small local validator for the JSON Schema subset used by OpenRouter.

    Vendored verbatim from vidnets/server/services/openrouter_llm.py
    (3be5190). 92ab202 deleted that module with the Vidnets server, so
    the validator now carries its own copy instead of importing it.
    Keeping this dependency-free makes schema checks work in the bundled
    Windows runtime as well as the developer environment.
    """
    if not isinstance(schema, dict):
        return f"{path}: schema must be an object"
    if isinstance(value, (int, float)) and not isinstance(value, bool) and not math.isfinite(value):
        return f"{path}: JSON numbers must be finite"
    expected = schema.get("type")
    expected_types = expected if isinstance(expected, list) else [expected]
    type_ok = not expected
    for item in expected_types:
        if item == "object" and isinstance(value, dict): type_ok = True
        elif item == "array" and isinstance(value, list): type_ok = True
        elif item == "string" and isinstance(value, str): type_ok = True
        elif item == "integer" and isinstance(value, int) and not isinstance(value, bool): type_ok = True
        elif item == "number" and isinstance(value, (int, float)) and not isinstance(value, bool): type_ok = True
        elif item == "boolean" and isinstance(value, bool): type_ok = True
        elif item == "null" and value is None: type_ok = True
    if not type_ok:
        return f"{path}: expected {expected}"
    if "enum" in schema and value not in schema["enum"]:
        return f"{path}: value is outside the allowed choices"
    if isinstance(value, dict):
        required = schema.get("required") or []
        missing = [key for key in required if key not in value]
        if missing:
            return f"{path}: missing required fields {', '.join(map(str, missing))}"
        properties = schema.get("properties") or {}
        if schema.get("additionalProperties") is False:
            unexpected = set(value) - set(properties)
            if unexpected:
                return f"{path}: unexpected fields {', '.join(sorted(map(str, unexpected)))}"
        for key, child in properties.items():
            if key in value:
                error = _json_schema_error(value[key], child, f"{path}.{key}", max_errors)
                if error:
                    return error
    elif isinstance(value, list):
        if len(value) < int(schema.get("minItems", 0)):
            return f"{path}: too few items"
        if "maxItems" in schema and len(value) > int(schema["maxItems"]):
            return f"{path}: too many items"
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, child_value in enumerate(value):
                error = _json_schema_error(child_value, item_schema, f"{path}[{index}]", max_errors)
                if error:
                    return error
    elif isinstance(value, str):
        if len(value) < int(schema.get("minLength", 0)):
            return f"{path}: text is too short"
        if "maxLength" in schema and len(value) > int(schema["maxLength"]):
            return f"{path}: text is too long"
        pattern = schema.get("pattern")
        if pattern:
            try:
                if re.search(str(pattern), value) is None:
                    return f"{path}: text does not match the required pattern"
            except re.error:
                return f"{path}: schema contains an invalid pattern"
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < float(schema["minimum"]):
            return f"{path}: number is below the allowed range"
        if "maximum" in schema and value > float(schema["maximum"]):
            return f"{path}: number is above the allowed range"
    return ""


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
        issue = _json_schema_error(value, contract.get("schema") or {})
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
