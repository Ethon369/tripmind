"""密码哈希与会话令牌 —— 只用标准库实现。

为什么不用 `passlib` / `bcrypt`
-------------------------------
1. **依赖风险**。项目跑在 Python 3.14 上,已经为版本兼容放弃过 torch。
   `bcrypt` 是 C 扩展,换 Python 版本就要等上游出 wheel;`passlib` 更是
   多年没有新版本,且对 `bcrypt` 的版本挑得很细 —— 拿不到匹配的组合时,
   它**不是报错而是静默降级**(会打一行 warning 然后退回更弱的算法)。
   密码这块"悄悄变弱"比装不上更糟。
2. **标准库够用**。`hashlib.pbkdf2_hmac` 由 OpenSSL 提供实现,600k 轮
   SHA-256 的强度是 OWASP 的推荐档位。本项目是单机 SQLite 的小应用,
   登录不是性能瓶颈 —— 一次 0.3 秒换来"零新增依赖"是划算的。

**为什么轮数写进哈希串里**。格式是 `pbkdf2_sha256$轮数$盐$摘要`。
  把轮数存进去,`verify_password()` 才能用**当初加密时那个轮数**来校验;
  否则将来调高轮数会让所有老密码当场失效(用户会觉得"密码明明是对的")。
  调高轮数只需改配置,老哈希在用户下次改密时自然迁移过来。

**为什么不校验盐/摘要的长度**。`bytes.fromhex` 对长度不敏感,
  而 `compare_digest` 对不同长度的输入直接返回 False —— 一个被截断的
  哈希串会走到"校验失败"这个正确结果上,不需要额外防御。

会话令牌为什么存摘要而不是原文
--------------------------------
`new_session_token()` 给客户端的是一串 256 位随机值,库里存的是它的
SHA-256。这样一来**库被读走也拿不到可直接使用的会话**(比如备份泄露、
或者某个只读接口把表内容带出去了)。这里用普通 SHA-256 而不是 pbkdf2:
令牌本身就是高熵随机串,不存在"弱口令"可猜,慢哈希只会让每个请求都变慢。
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

# 算法标识写进哈希串的开头,将来换算法(比如 argon2)时能平滑共存:
# 老串按老算法校验,新密码用新算法写。
_ALGO = "pbkdf2_sha256"
_SALT_BYTES = 16
_DIGEST_BYTES = 32


def hash_password(password: str, iterations: int | None = None) -> str:
    """把明文密码哈希成可入库的字符串。

    Args:
        password: 明文密码。**不在这里做强度校验** —— 那是 `UserStore`
            的职责(它才知道配置里的最小长度),混在一起会让
            "哈希"和"校验入参"两件事在中途分不开。
        iterations: 覆盖默认轮数。测试会传一个很小的值,
            否则每建一个账号就要 0.3 秒,整套测试会从几秒变成几十秒。
    """
    if iterations is None:
        from ..config import get_settings

        iterations = int(get_settings().password_hash_iterations)

    salt = secrets.token_bytes(_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"{_ALGO}${iterations}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """校验明文密码是否匹配哈希串。

    任何解析失败都返回 False,不抛异常 —— 调用方(登录接口)只需要
    "过 / 不过"这一个二值结果,让它去区分"哈希串损坏"和"密码错误"
    没有意义:两种情况的对外表现都必须是**同样的 401**,否则就泄露了
    "这个账号存在但是数据坏了"这类信息。
    """
    if not password or not stored:
        return False

    try:
        algo, raw_iterations, salt_hex, digest_hex = stored.split("$")
        if algo != _ALGO:
            return False
        iterations = int(raw_iterations)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
    except (ValueError, AttributeError, TypeError):
        return False

    if iterations <= 0:
        return False

    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    # 常量时间比较:虽然这里比对的是"用户输入算出来的摘要"而不是原始密码,
    # 但短路比较同样会随匹配前缀长度变化耗时,没有理由不用 compare_digest。
    return hmac.compare_digest(actual, expected)


def new_session_token() -> str:
    """生成一个会话令牌。32 字节熵 → 43 个 URL 安全字符。

    用 `token_urlsafe` 而不是 `token_hex`:它可以直接放进
    `Authorization: Bearer <值>`,不需要再做转义,也不会在复制粘贴时
    因为肉眼分不清 `0/O`、`1/l` 而抄错。
    """
    return secrets.token_urlsafe(32)


def fingerprint(token: str) -> str:
    """令牌指纹 —— 入库的是这个,不是令牌原文。理由见模块文档末尾。"""
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


def password_policy_error(password: str, *, min_length: int = 6, username: str = "") -> str | None:
    """检查密码是否可用,返回错误说明;通过时返回 None。

    刻意**只要求长度**、不强制大小写数字混合。理由是 NIST SP 800-63B 的建议:
    复杂度规则会把用户推向 `Passw0rd!` 这类可预测的变体,而真正的强度来自长度。
    这里加的两条额外约束都是"明显不该出现"的:

    - 密码不能与用户名相同(哪怕加了空格)。这类账号等于没有密码。
    - 不能全是同一种字符。`aaaaaa` 能过长度检查但没有任何熵。

    返回 `str | None` 而不是抛异常,是为了让调用方能把它直接塞进
    HTTP 400 的 detail 里 —— 校验信息是**要回给用户看**的,
    不是给日志看的。
    """
    if not password:
        return "密码不能为空"
    if len(password) < min_length:
        return f"密码至少 {min_length} 位"
    if username and password.strip().lower() == username.strip().lower():
        return "密码不能与用户名相同"
    if len(set(password)) == 1:
        return "密码不能是同一个字符重复"
    return None
