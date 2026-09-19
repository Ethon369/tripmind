"""验证共享的 MCPTool 能不能并发调用。

**为什么必须实测**:读代码看到 `async with MCPClient(...)` 每次新建连接,
所以判断是"无共享会话、并发安全"。但判断错了的代价是**静默的错误数据**
—— 三个城市同时查,结果互相串了,而行程里只会写成"景点张冠李戴",
不会有任何报错。这种错不该靠读代码下结论。

做法:三个线程同时用**同一个 MCPTool 实例**查三个不同城市的景点,
然后检查每份结果里是不是只出现了自己那个城市 —— 串了就说明不能并发。

**不花 LLM 额度**(只走高德 MCP 工具)。
"""

from __future__ import annotations

import concurrent.futures as cf
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.services.mcp_launcher import build_mcp_env, resolve_uvx_command  # noqa: E402

from hello_agents.tools import MCPTool  # noqa: E402

CITIES = ["北京故宫", "上海外滩", "成都宽窄巷子"]

# 只做一件事:确认**同一个实例**被三个线程同时用
print("创建共享 MCPTool …")
t0 = time.perf_counter()
tool = MCPTool(
    name="amap",
    description="高德地图服务",
    server_command=resolve_uvx_command(),
    env=build_mcp_env(get_settings().amap_api_key),
    auto_expand=True,
)
tool.expandable = True
print(f"  创建耗时 {time.perf_counter() - t0:.1f}s")
print(f"  可用工具数: {len(getattr(tool, '_available_tools', None) or [])}\n")

results: dict[str, tuple[float, str]] = {}
barrier = threading.Barrier(len(CITIES))


def query(city: str) -> None:
    # 让三个线程尽量同时冲进去 —— 不加 barrier 的话第一个可能已经跑完了,
    # 那样测的其实是顺序执行,并发问题永远不会暴露。
    barrier.wait()
    t = time.perf_counter()
    try:
        out = tool.run(
            {
                "action": "call_tool",
                "tool_name": "maps_text_search",
                "arguments": {"keywords": city, "city": city[:2]},
            }
        )
    except Exception as exc:  # noqa: BLE001
        out = f"__EXCEPTION__ {type(exc).__name__}: {exc}"
    results[city] = (time.perf_counter() - t, out)


print(f"三个线程同时查:{CITIES}\n")
t_all = time.perf_counter()
with cf.ThreadPoolExecutor(max_workers=3) as ex:
    list(ex.map(query, CITIES))
total = time.perf_counter() - t_all

print(f"并发总耗时 {total:.1f}s\n")
ok = True
for city, (secs, out) in results.items():
    # 检查串味:结果里应当出现自己城市的名字,而不是别的城市
    own = city[:2]
    others = [c[:2] for c in CITIES if c != city and c[:2] in out]
    bad = out.startswith("__EXCEPTION__") or others
    ok = ok and not bad
    head = out.replace("\n", " ")[:110]
    flag = "❌" if bad else "✅"
    print(f"{flag} {city}  {secs:.1f}s  长度 {len(out)}")
    print(f"     {head}")
    if others:
        print(f"     ⚠️ 结果里出现了别的城市:{others} —— 串味了!")
    if out.startswith("__EXCEPTION__"):
        print(f"     ⚠️ {out[:200]}")

print()
if ok:
    print("✅ 结论:三个线程各自拿到正确的结果,共享 MCPTool 可以并发使用")
else:
    print("❌ 结论:**不能**这样并发 —— 结果串了或抛异常,别并行")
