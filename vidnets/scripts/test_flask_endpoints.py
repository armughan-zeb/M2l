import json
import urllib.request
import sys

BASE_URL = "http://127.0.0.1:5000"

def post_json(endpoint, payload):
    url = f"{BASE_URL}{endpoint}"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))

def get_json(endpoint):
    url = f"{BASE_URL}{endpoint}"
    req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))

print("=== Testing Flask Server Endpoints ===")

# 1. Queue Status
status_code, res = get_json("/api/queue/status")
print(f"1. /api/queue/status: {status_code} (items in queue: {len(res.get('items', []))})")
assert status_code == 200, f"Queue status failed: {res}"

# 2. Director Plan
plan_payload = {
    "topic": "British Royal Family",
    "script": "The British royal family combines ancient tradition with a modern public role. Buckingham Palace serves as the iconic backdrop for state visits and guarded ceremonies.",
    "category": "history",
}
status_code, res = post_json("/api/director/plan", plan_payload)
print(f"2. /api/director/plan: {status_code} (scenes: {len(res.get('edit_plan', {}).get('scenes', []))})")
assert status_code == 200 and res.get("success"), f"Director plan failed: {res}"
edit_plan = res["edit_plan"]

# 3. Director Validate
status_code, res = post_json("/api/director/validate", {"manifest": edit_plan})
print(f"3. /api/director/validate: {status_code} (valid: {res.get('valid')})")
assert status_code == 200 and res.get("valid"), f"Director validate failed: {res}"

# 4. Director Tweak
status_code, res = post_json("/api/director/tweak", {"edit_plan": edit_plan, "instruction": "Make scene 1 more energetic"})
print(f"4. /api/director/tweak: {status_code} (success: {res.get('success')})")
assert status_code == 200 and res.get("success"), f"Director tweak failed: {res}"

# 5. Director Review
status_code, res = post_json("/api/director/review", {"manifest": edit_plan})
print(f"5. /api/director/review: {status_code} (council status: {res.get('status')})")
assert status_code == 200 and res.get("success"), f"Director review failed: {res}"

# 6. Director Media-First
status_code, res = post_json("/api/director/media-first", {"manifest": edit_plan})
print(f"6. /api/director/media-first: {status_code} (version: {res.get('manifest', {}).get('version')})")
assert status_code == 200 and res.get("success"), f"Director media-first failed: {res}"

print("\nAll Director and Queue HTTP Endpoints Verified on Live Flask Server!")
