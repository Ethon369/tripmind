"""登录失败节流。

为什么需要它(而共享口令时代不需要)
------------------------------------
旧设计的注释里写过「没有防暴力破解的限流,口令是 24 字节随机,穷举不现实」
—— 那句话在**共享随机口令**的前提下是成立的。换成账号密码之后前提变了:

  · 密码由人设定,熵远低于 24 字节随机值;
  · 用户名是可以枚举的(注册时会告诉你"已被占用");
  · 登录接口在公网上直接可达。

所以现在"不做限流"和"把密码交给撞库"是同一件事。这个模块就是补这一课。

设计上的取舍
------------
1. **进程内内存,不落库。** 与项目在 SQLite 上的一贯取舍一致:
   窗口是几分钟级别的,重启丢掉可接受。代价是**多 worker 时各自计数**
   —— 而本项目的 `--workers` 必须是 1(见 CLAUDE.md 的部署约束),
   所以这个前提是成立的。将来真要扩容,这里得换成 Redis。

2. **两个维度分别计数。**
   · 按「用户名」:防的是"盯着一个账号猛试密码"。
   · 按「来源 IP」:防的是"每换一个用户名试一次"。只做前者的话,
     攻击者用一个密码去撞一万个用户名,每个账号只失败一次,永远不触发。

3. **只统计失败,不统计成功。** 正常用户输错一两次很常见,
   而"登录成功"本身就该把该账号的失败计数清零。

4. **锁定是"延迟"而不是"封禁"。** 超过阈值后每次尝试都要等
   `lockout_sec` 才被受理 —— 也就是说攻击者的速率被压到
   `阈值/锁定窗口` 次每小时,而真正的用户输错几次等一会儿就能进,
   不需要管理员手工解封(没有后台可解封)。

⚠️ 这个模块**不返回"账号是否存在"** 的信息:锁定信息里只有
   "再等多少秒",与账号是否存在无关。
"""

from __future__ import annotations

import threading
import time

# 默认参数:5 次失败 → 锁 5 分钟。
# 取值理由:正常手指打滑很少连续 5 次;而 5 分钟足以把在线爆破的速率
# 压到没有实际意义(5 分钟 5 次 = 每小时 60 次,要撞开一个 6 位以上
# 非弱口令的密码在数量级上不可能)。
DEFAULT_MAX_ATTEMPTS = 5
DEFAULT_LOCKOUT_SEC = 300


class LoginGuard:
    """失败计数与锁定。线程安全(端点跑在线程池里,必须加锁)。"""

    def __init__(
        self,
        *,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        lockout_sec: int = DEFAULT_LOCKOUT_SEC,
        clock=time.monotonic,
    ) -> None:
        self.max_attempts = max(1, int(max_attempts))
        self.lockout_sec = max(1, int(lockout_sec))
        # 注入时钟是为了测试能"快进"时间,而不是真的 sleep 五分钟。
        self._clock = clock
        self._lock = threading.Lock()
        # key -> (失败次数, 最近一次失败时刻)
        self._failures: dict[str, tuple[int, float]] = {}

    def retry_after(self, *keys: str) -> int:
        """还有多少秒才能再试。0 表示可以试。

        传入多个 key 时取**最严格**的那个:同一个请求同时受
        "这个用户名"和"这个 IP"两条线约束,任一被锁就不能放行。
        """
        now = self._clock()
        worst = 0
        with self._lock:
            for key in keys:
                if not key:
                    continue
                entry = self._failures.get(key)
                if entry is None:
                    continue
                count, last = entry
                if count < self.max_attempts:
                    continue
                elapsed = now - last
                remaining = self.lockout_sec - elapsed
                if remaining > 0:
                    worst = max(worst, int(remaining) + 1)
        return worst

    def record_failure(self, *keys: str) -> None:
        now = self._clock()
        with self._lock:
            for key in keys:
                if not key:
                    continue
                count, _ = self._failures.get(key, (0, now))
                self._failures[key] = (count + 1, now)

    def record_success(self, *keys: str) -> None:
        """登录成功 → 清掉这些 key 的计数。

        只清传进来的 key。**不会**连 IP 一起清:同一个 IP 上有个账号
        登录成功,不该顺带把"这个 IP 在撞别的账号"的记录也抹掉。
        所以调用方传的是用户名,不是 IP。
        """
        with self._lock:
            for key in keys:
                self._failures.pop(key, None)

    def reset(self) -> None:
        """清空全部计数。测试用,避免用例之间互相污染。"""
        with self._lock:
            self._failures.clear()

    def prune(self) -> int:
        """丢掉彻底过期的记录,防止字典无限增长。

        只有"早已过了锁定期"的条目才会被清 —— 还在锁定窗口内的必须留着。
        在一个长期运行的进程里,这个 dict 的键是
        "被攻击过/被输错过"的用户名与 IP,不清就会一直涨。
        """
        now = self._clock()
        with self._lock:
            stale = [
                key
                for key, (_, last) in self._failures.items()
                if now - last > self.lockout_sec * 2
            ]
            for key in stale:
                self._failures.pop(key, None)
        return len(stale)


# 全局单例。与 `get_plan_store()` / `get_llm()` 同样风格。
_guard = LoginGuard()

# 注册单独一个实例,和登录**不共享计数**。
#
# 为什么不共用:两个动作的威胁模型不同 —— 登录是"撞已有账号的密码",
# 注册是"批量建垃圾账号"。共用一个计数器的话,某个人登错两次密码、
# 另外几个人注册,会互相挤占配额,出现"我没输错密码却说登录失败次数过多"。
#
# 阈值也不同:注册没有"密码打错"这种正常场景,正常用户一次就该成功,
# 所以给得比登录更紧(3 次/10 分钟锁 10 分钟)。
_register_guard = LoginGuard(max_attempts=3, lockout_sec=600)


def get_login_guard() -> LoginGuard:
    return _guard


def get_register_guard() -> LoginGuard:
    return _register_guard
