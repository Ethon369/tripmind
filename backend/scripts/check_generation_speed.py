"""生成一次行程并打印分阶段耗时 —— 用于验证性能改动的效果。

会花掉一点 LLM 额度(实测约 ¥0.09)。用北京 3 日,和改动前的基线同形状。
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8001"
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def call(method: str, path: str, body: dict | None = None, token: str | None = None):
    req = urllib.request.Request(f"{BASE}{path}", method=method)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        req.add_header("Content-Type", "application/json")
    try:
        with _opener.open(req, data, timeout=400) as r:
            raw = r.read().decode()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, raw


st, res = call("POST", "/api/auth/login", {"username": "admin", "password": "admin123"})
assert st == 200, res
token = res["token"]

payload = {
    "city": "北京",
    "start_date": "2026-10-01",
    "end_date": "2026-10-03",
    "travel_days": 3,
    "transportation": "公共交通",
    "accommodation": "舒适型酒店",
    "preferences": ["历史文化"],
    "free_text_input": "",
}

print("生成北京 3 日 …\n")
t0 = time.perf_counter()
st, body = call("POST", "/api/trip/plan", payload, token=token)
elapsed = time.perf_counter() - t0

print("=" * 72)
print(f"HTTP {st}   总耗时 {elapsed:.1f} 秒")
print("=" * 72)
print(f"success = {body.get('success')}   message = {body.get('message')}")

data = body.get("data") or {}
days = data.get("days") or []
at = [a for d in days for a in (d.get("attractions") or [])]
print(f"天数={len(days)}  景点={len(at)}  天气={len(data.get('weather_info') or [])}  "
      f"预算={((data.get('budget') or {}).get('total'))}")

for a in at[:5]:
    loc = a.get("location") or {}
    print(f"   · {a.get('name')}  ({loc.get('longitude')}, {loc.get('latitude')})")

pid = body.get("plan_id")
if pid:
    _, detail = call("GET", f"/api/trip/plans/{pid}", token=token)
    meta = detail.get("meta") or {}
    print(f"\n落库 status = {meta.get('status')}")
    print(f"tokens={meta.get('total_tokens')}  llm_calls={meta.get('llm_calls')}  "
          f"cost=¥{meta.get('cost_cny')}")
