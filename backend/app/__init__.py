"""途灵 TripMind - 后端应用"""

import sys

__version__ = "1.0.0"


def _force_utf8_stdio() -> None:
    """把标准输出/错误流强制成 UTF-8。

    为什么需要这个
    --------------
    本项目到处用 emoji 打日志(🚀 📥 💰 ...)。在**终端**里跑没问题:
    Python 在 Windows 控制台上走 WriteConsoleW,按 UTF-8 写。但只要输出
    被**重定向**——管道、`> log.txt`、后台运行、CI——stdout 就变成一个
    普通文件对象,编码取 `locale.getpreferredencoding()`,中文 Windows 下
    是 GBK。于是 `print("🚀 启动")` 抛:

        UnicodeEncodeError: 'gbk' codec can't encode character '\\U0001f680'

    这个异常发生在 FastAPI 的 startup 事件里,后果是**整个应用启动失败**,
    而不是少打一行日志。排查时很容易误以为是端口/依赖问题。

    放在 `app/__init__.py` 而不是 run.py:任何入口(服务、脚本、评测)
    只要 `import app` 就生效,不会漏。

    errors="replace" 是兜底:万一某个字符连 UTF-8 之外还有问题,
    宁可显示成 '?' 也不要让日志把主流程搞崩。
    """
    for stream in (sys.stdout, sys.stderr):
        # 被重定向成管道/文件时 reconfigure 可用;极少数不可配置的流跳过
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            # 流已关闭或本身不支持改编码 —— 不影响主流程,忽略
            pass


_force_utf8_stdio()
