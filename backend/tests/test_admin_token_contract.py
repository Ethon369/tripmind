"""前后端「管理口令」契约的测试。

## 为什么需要这个文件

管理口令这件事被**两个地方各写了一遍**:

- 后端:`backend/app/api/routes/*.py` 里挂在具体路由上的
  `dependencies=[Depends(require_admin)]`
- 前端:`frontend/src/services/api.ts` 里的 `ADMIN_GUARDED` 清单

两边必须逐条对上,而且**对不上的方式都是静默的**:

- 后端挂了、前端没带 → 该接口在网页上永远 401。用户看到的是
  "管理口令不正确",但口令明明是对的 —— 这种 bug 会让人去怀疑口令,
  而真正的原因是前端漏了一条正则。
- 前端带了、后端没挂 → 请求会多一个自定义头。功能不受影响,
  所以一直不会有人发现,直到某个跨域部署上多出一次预检失败。

`backend/tests/test_admin_auth.py` 只能保证后端那一半;
这半边没有测试的话,清单就是靠"改的时候记得同步"维系的。

## 关于解析 TS 源码

不引入 JS 解析器(没必要为一次断言装依赖),用手写扫描读 `ADMIN_GUARDED`。
注意**不能**用 `/(.*?)/` 这类正则去抓正则字面量:字面量里可以有
**未转义的**斜杠,比如 `[^/]+` 里那个 —— 那样解析会在字符类内部就截断,
得到一堆看着像对、实际是错的东西。所以下面按字符扫,并跟踪字符类深度。

缺文件时 `skip` 而不是失败:后端镜像里没有 `frontend/`,在容器里跑测试
时这个契约无从谈起,那时 skip 是正确语义。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.test_admin_auth import MUST_BE_PUBLIC, guarded_endpoints

# backend/tests/xxx.py → parents[2] 是仓库根
REPO_ROOT = Path(__file__).resolve().parents[2]
API_TS = REPO_ROOT / "frontend" / "src" / "services" / "api.ts"
STORAGE_TS = REPO_ROOT / "frontend" / "src" / "constants" / "storage.ts"

pytestmark = pytest.mark.skipif(
    not API_TS.exists(),
    reason=f"前端源码不在({API_TS})——多半是在只含后端的镜像里跑测试",
)


# ---------------------------------------------------------------------------
# 解析 ADMIN_GUARDED
# ---------------------------------------------------------------------------

# 条目的开头:`['POST', /`
_ENTRY_START = re.compile(r"\[\s*'(?P<method>[A-Z]+)'\s*,\s*/")


def _read_regex_literal(src: str, pos: int) -> tuple[str, int]:
    """从 `src[pos] == '/'` 读一个 JS 正则字面量,返回 (源码, 结束后的下标)。

    「结束」= 不在字符类里、且未被转义的斜杠。
    """
    assert src[pos] == "/", "调用方保证 pos 落在斜杠上"
    pos += 1
    out: list[str] = []
    in_class = False
    while pos < len(src):
        ch = src[pos]
        if ch == "\\":  # 转义:连同下一个字符一起收下,不做判定
            out.append(ch)
            pos += 1
            if pos < len(src):
                out.append(src[pos])
                pos += 1
            continue
        if ch == "[":
            in_class = True
        elif ch == "]":
            in_class = False
        elif ch == "/" and not in_class:
            return "".join(out), pos + 1
        out.append(ch)
        pos += 1
    raise AssertionError("ADMIN_GUARDED 里有一个没有闭合的正则字面量")


def frontend_guarded() -> list[tuple[str, re.Pattern[str]]]:
    """前端的 `ADMIN_GUARDED` → `[(方法, 编译好的正则)]`。

    范围用两个文本锚点框出来,不做括号配对 —— 数组里那些 `[^/]`
    会让朴素的括号计数出错,而锚点是稳定的。
    """
    src = API_TS.read_text(encoding="utf-8")
    start = src.index("const ADMIN_GUARDED")
    end = src.index("export function needsAdminToken", start)
    region = src[start:end]

    entries: list[tuple[str, re.Pattern[str]]] = []
    cursor = 0
    while (m := _ENTRY_START.search(region, cursor)) is not None:
        source, cursor = _read_regex_literal(region, m.end() - 1)
        # JS 的 `\/` 在 Python 的 re 里也是字面斜杠,直接编译即可;
        # 两边共用的语法只有 ^ $ [^x]+ ,不存在语义差异。
        entries.append((m.group("method"), re.compile(source)))
    return entries


def _sample_path(endpoint_path: str) -> str:
    """`/api/trip/plans/{plan_id}` → `/api/trip/plans/sample-id-123`。

    前端拿到的是真实 id,所以要用一个不含斜杠的样例去试它的正则。
    """
    return re.sub(r"\{[^}]+\}", "sample-id-123", endpoint_path)


def _split(endpoint: str) -> tuple[str, str]:
    method, _, path = endpoint.partition(" ")
    return method, path


def _backend_guarded() -> set[str]:
    """后端的受保护端点。**从真实路由表推**,不读金名单 ——
    这样两边不会一起漂移(金名单本身已被 test_admin_auth 校验过)。
    """
    return {k for k, v in guarded_endpoints().items() if v}


# ---------------------------------------------------------------------------
# 1. 两份清单必须逐条对上
# ---------------------------------------------------------------------------


class Test前后端受保护清单一致:
    def test_后端每个受保护端点前端都会附口令(self):
        """漏一条的后果:该接口在网页上**永远** 401,而口令是对的。"""
        entries = frontend_guarded()
        missing: list[str] = []

        for endpoint in sorted(_backend_guarded()):
            method, path = _split(endpoint)
            sample = _sample_path(path)
            # 用 search 而不是 match:和 JS 的 RegExp.test() 语义一致
            if not any(m == method and re.search(sample) for m, re in entries):
                missing.append(endpoint)

        assert not missing, (
            "这些后端要求口令的接口,前端的 ADMIN_GUARDED 里没有对应条目:\n  "
            + "\n  ".join(missing)
            + "\n→ 网页上调它们会被拒(401),而用户无从知道是前端漏带了头。"
            "\n→ 请到 frontend/src/services/api.ts 的 ADMIN_GUARDED 补上。"
        )

    def test_前端清单里没有多余条目(self):
        """多一条的后果:给一个不需要口令的接口白带自定义头,
        跨域部署时多一次失败的预检 —— 不报错的坏味道。"""
        guarded = _backend_guarded()
        samples = [(_split(e)[0], _sample_path(_split(e)[1])) for e in guarded]

        extra: list[str] = []
        for method, pattern in frontend_guarded():
            if not any(m == method and pattern.search(s) for m, s in samples):
                extra.append(f"{method} {pattern.pattern}")

        assert not extra, (
            "前端 ADMIN_GUARDED 里这些条目在后端并没有对应的受保护端点:\n  "
            + "\n  ".join(extra)
        )

    def test_前端清单不会误伤公开接口(self):
        """这条防的是"正则写得太宽"。

        比如把 `/^\\/api\\/knowledge\\/docs$/` 手滑写成
        `/^\\/api\\/knowledge\\//` —— 上面两个测试都过得去(它确实覆盖了
        所有受保护端点),但它同时会把 `/status`、`/search` 也带上口令头,
        而且**下一个**新增的公开接口会自动中招。
        """
        public = [(_split(e)[0], _sample_path(_split(e)[1])) for e in MUST_BE_PUBLIC]

        hurt: list[str] = []
        for method, pattern in frontend_guarded():
            for m, sample in public:
                if m == method and pattern.search(sample):
                    hurt.append(f"{method} {sample} ← {pattern.pattern}")

        assert not hurt, (
            "这些公开接口被前端的 ADMIN_GUARDED 命中了(会给它们带上口令头):\n  "
            + "\n  ".join(hurt)
        )

    def test_前端清单没有重复条目(self):
        entries = frontend_guarded()
        assert len(entries) == len(set(entries)), "ADMIN_GUARDED 里有重复条目"

    def test_解析出来的条目数与预期相符(self):
        """防"扫描器坏了" —— 如果 `_read_regex_literal` 提前截断,
        上面的测试会全部变成"没有命中",看起来像通过了。
        """
        assert len(frontend_guarded()) == 10, (
            "ADMIN_GUARDED 应有 10 条(8 个知识库 + 2 个行程)。"
            "数量变了要同时改这里 —— 更可能的情况是解析器坏了。"
        )


# ---------------------------------------------------------------------------
# 2. 前端确实会把请求头发出去
# ---------------------------------------------------------------------------


class Test前端确实会发送请求头:
    """清单对了不等于头发出去了 —— 还要确认两个发送点都接上了。

    发送点有两个,因为 `parseKnowledgeDoc` 走的是原生 fetch
    (multipart 的 boundary 必须由浏览器自己算),它**不经过** axios 拦截器。
    这正是最容易漏的地方:新加一个受保护接口、顺手用 fetch 写,
    于是一切看起来都对,只有那个接口 401。
    """

    def test_axios拦截器会附上X_Admin_Token(self):
        src = API_TS.read_text(encoding="utf-8")
        # 断言在"拦截器会判断该不该附"和"附的是这个头名"两件事上
        assert "needsAdminToken(config.method, config.url)" in src
        assert "config.headers.set('X-Admin-Token'" in src, (
            "axios 拦截器里没有设置 X-Admin-Token —— 所有受保护接口都会 401"
        )

    def test_绕过axios的fetch也带了口令(self):
        src = API_TS.read_text(encoding="utf-8")
        # 锚点用完整模板串:ADMIN_GUARDED 里也有同一个路径,
        # 但那里是转义过的 `\/api\/knowledge\/docs\/parse`,搜不到这个子串。
        anchor = "API_BASE_URL}/api/knowledge/docs/parse"
        assert anchor in src, "parseKnowledgeDoc 的 URL 变了,测试的锚点要跟着改"
        call = src[src.index(anchor) :][:600]
        assert "headers: adminHeaders()" in call, (
            "parseKnowledgeDoc 用的是原生 fetch,必须在自己的 options 里带 "
            "adminHeaders() —— 它不走 axios 拦截器。"
        )

    def test_口令存在sessionStorage不是localStorage(self):
        """localStorage 会把口令长期留在磁盘上;共享电脑上等于把写权限留给了下一个人。"""
        api = API_TS.read_text(encoding="utf-8")
        store = STORAGE_TS.read_text(encoding="utf-8")

        assert "sessionStorage.setItem(STORAGE_KEYS.ADMIN_TOKEN" in api
        assert "sessionStorage.getItem(STORAGE_KEYS.ADMIN_TOKEN" in api
        # 只查"真的在用",不查字符串出现 —— 两个文件都在注释里讨论过
        # localStorage 为什么不行,那也是这篇说明的一部分。
        used = re.compile(r"\blocalStorage\s*[.\[=]")
        assert not used.search(api), "api.ts 里不该用 localStorage"
        assert not used.search(store), "storage.ts 里只放 sessionStorage 的键"

    def test_口令不放URL查询参数(self):
        """URL 会进浏览器历史、进 nginx access log、进 Referer。

        用 X-Admin-Token 请求头是这个设计的一半;另一半是它**只**出现在头里。
        """
        src = API_TS.read_text(encoding="utf-8")
        # 出现在 params / query 里的写法
        assert not re.search(r"admin_token\s*[:=]", src), (
            "口令不能作为查询参数或请求体字段传递,只能是请求头"
        )
