"""API 层的公共依赖(目前只有管理口令鉴权)。

**为什么不引完整的登录 + RBAC**:项目没有用户体系 —— 行程本来就靠链接分享,
这是产品设定而不是缺失。为一个演示站引入用户表、会话、密码哈希,收益远小于
成本和它带来的新攻击面。真正需要挡住的只有一件事:

    **不能让人在公网上随手把数据删了 / 把库清了 / 传文件上来。**

所以这里用最轻的方式解决它:一个共享口令,只挂在**写操作**上。
读接口(状态、检索、行程列表与详情)保持公开 —— 它们要么是前端正常展示要用的,
要么本来就是给分享用的。

### 取舍说清楚(别把它当成"安全了")

- 口令是**明文走 HTTP** 的。对外部署**必须**先上 HTTPS,否则等于没设。
- 口令一旦泄露,**没法只吊销某一个人** —— 只能换口令、所有人重新录入。
  这正是它比真鉴权弱的地方,也是为什么它只适合这种规模。
- 它挡不住"能登录到服务器的人" —— 那是操作系统层面的事。
- 没有防暴力破解的限流。口令是 24 字节随机(建议 `openssl rand -hex 24`),
  穷举不现实,所以这里没做而不是忘了做。

### 为什么 fail-closed

未配置 `TRIPMIND_ADMIN_TOKEN` 时返回 **503** 而不是放行。理由很直接:
公开部署上"忘了配"和"任何人都能清库"不能是同一个状态。
代价是本地开发想用知识库页也得配一个值(随便填就行)——
比起某天发现库被人清了,这点麻烦值得。
"""

from __future__ import annotations

import secrets

from fastapi import Header, HTTPException

from ..config import get_settings


def require_admin(x_admin_token: str | None = Header(default=None)) -> None:
    """校验管理口令。作为 FastAPI 依赖挂在需要保护的端点上。

    用法::

        @router.post("/ingest", dependencies=[Depends(require_admin)])
        def knowledge_ingest(...): ...

    三种结果,都是客户端能读懂的状态码:

        503  服务端**没配**口令 —— 是部署问题,不是客户端问题,
             所以不用 401/403(那会让用户以为是自己的口令错了)
        401  口令不对或没带
        204  通过(返回 None,调用方不看返回值,只用它的副作用)

    鉴权失败时**不告诉调用方"口令是空的还是错的"** ——
    反正口令猜不出来,没必要多给信息。
    """
    expected = (get_settings().admin_token or "").strip()

    if not expected:
        raise HTTPException(
            status_code=503,
            detail=(
                "管理接口未启用:服务端没有配置 TRIPMIND_ADMIN_TOKEN。"
                "请在 backend/.env 里配置一个随机口令后重启后端"
                "（生成方式:openssl rand -hex 24）。"
            ),
            headers={"X-Admin-Required": "config"},
        )

    provided = (x_admin_token or "").strip()

    # ⚠️ 用 compare_digest 而不是 `!=`:`==` 会短路,比较耗时随"前缀对上的
    #    位数"变化,理论上可被逐字节爆破(时序攻击)。虽然这里的威胁模型
    #    (演示站 + 随机口令)下很难被实际利用,但这是一行就能做对的事。
    if not provided or not secrets.compare_digest(provided, expected):
        raise HTTPException(
            status_code=401,
            detail="管理口令不正确。请在页面右上角的「管理口令」里重新输入。",
            headers={"X-Admin-Required": "token"},
        )
