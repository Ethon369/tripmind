"""配置项的环境变量名 —— 确保文档里写的名字真的起作用

**为什么值得单独一个文件**

配置类项目最常见的静默故障不是"值填错了",而是**名字根本没被读到**:
pydantic-settings 按字段名匹配环境变量,写错一个词就等于没配;
而 `extra = "ignore"` 让它**连个警告都不给**。

本项目真踩过一次:`.env` 和 `.env.example` 里一直写着 `TRIPMIND_DB`,
但字段名是 `db_path` —— 于是"改数据库位置"这个操作**从来没生效过**,
一直用的都是默认路径。它是给 Docker 配数据卷时才暴露的:
卷挂在别处、库还是写在容器里,一重建容器数据就没了,而日志里一句提示都没有。

所以这里分两层钉住:
1. 具体别名能用(`TRIPMIND_DB` 和 `DB_PATH` 都生效)
2. **`.env.example` 里出现的每一个键,都必须有人认识它** —— 这一层才能
   拦住"以后又加了个想当然的变量名"
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.config import Settings

# 不是 config 字段、但**确实会被读到**的环境变量。它们由别处消费:
#
#   LLM_*   —— hello_agents.HelloAgentsLLM 直接从 os.environ 读(见
#              venv/.../hello_agents/core/llm.py 的 _resolve_credentials)
#   OPENAI_* / DEEPSEEK_* / DASHSCOPE_API_KEY 等按厂商取 key 的兜底名,同上
#   VITE_*  —— 前端构建期变量,后端完全不看
#   HOST / PORT / LOG_LEVEL —— 前两个是 config 字段,LOG_LEVEL 在
#              app/__init__.py 里给 logging 用
#
# 往这个集合里加东西时请三思:加错一项,就等于把一次静默失效合法化了。
_EXTERNAL_ENV_KEYS = {
    "LLM_MODEL_ID",
    "LLM_API_KEY",
    "LLM_BASE_URL",
    "LLM_TIMEOUT",
    "OPENAI_API_KEY",
    "DEEPSEEK_API_KEY",
    "DASHSCOPE_API_KEY",
    "LOG_LEVEL",
}

_ENV_EXAMPLE = Path(__file__).resolve().parents[1] / ".env.example"


def _keys_in_env_example() -> list[str]:
    if not _ENV_EXAMPLE.exists():  # pragma: no cover - 文件被删时才有意义
        pytest.skip(f"没有 {_ENV_EXAMPLE}")
    keys: list[str] = []
    for line in _ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        keys.append(line.split("=", 1)[0].strip())
    return keys


# ===========================================================================
# 一、db_path 的别名(那次事故的具体回归)
# ===========================================================================


class TestDbPathAliases:
    def test_TRIPMIND_DB生效(self, monkeypatch):
        """**这条就是那次事故的回归测试。**

        `.env.example` 从第一天起就写着 `TRIPMIND_DB`,但它一直没生效 ——
        字段名是 `db_path`,而 `extra="ignore"` 把未知名变量悄悄吃掉了。
        """
        monkeypatch.setenv("TRIPMIND_DB", "/data/tripmind.db")
        monkeypatch.delenv("DB_PATH", raising=False)
        assert Settings().db_path == "/data/tripmind.db"

    def test_DB_PATH仍然生效(self, monkeypatch):
        """按字段名写的老用法不能被破坏 —— 已经这么配的部署不该失效。"""
        monkeypatch.setenv("DB_PATH", "/legacy/x.db")
        monkeypatch.delenv("TRIPMIND_DB", raising=False)
        assert Settings().db_path == "/legacy/x.db"

    def test_都不设时走默认(self, monkeypatch):
        monkeypatch.delenv("TRIPMIND_DB", raising=False)
        monkeypatch.delenv("DB_PATH", raising=False)
        assert Settings().db_path == "./data/tripmind.db"

    def test_两个都设时TRIPMIND_DB优先(self, monkeypatch):
        """同时存在时按声明顺序取 —— 项目文档用的是 TRIPMIND_DB,
        它应当压过偶然出现在环境里的 DB_PATH。"""
        monkeypatch.setenv("TRIPMIND_DB", "/from-tripmind.db")
        monkeypatch.setenv("DB_PATH", "/from-db-path.db")
        assert Settings().db_path == "/from-tripmind.db"


# ===========================================================================
# 二、审计:示例文件里不许有"没人认识"的键
# ===========================================================================


def _aliases_of(info) -> set[str]:
    """取出一个字段声明的所有环境变量别名。

    ⚠️ `AliasChoices` **不能直接迭代**(它不是 list)。得取它的 `.choices`;
    直接 `for x in alias` 会抛 TypeError —— 这个测试第一版就是这么写的。
    """
    raw = getattr(info, "validation_alias", None)
    if raw is None:
        return set()
    choices = getattr(raw, "choices", None)
    if choices is None:
        choices = (raw,)
    return {str(c).upper() for c in choices}


class TestEnvExampleKeysAreRecognised:
    def test_每个键都能被某个组件读到(self):
        """**这一层才拦得住"以后又加了个想当然的变量名"。**

        上一条只管 db_path。如果有人再往 `.env.example` 里加一个
        `KNOWLEDGE_ROOT=...` 而字段其实叫 `knowledge_dir`,读者会照样改、
        照样静默失效 —— 这条测试会在那时变红。
        """
        fields = {name.lower() for name in Settings.model_fields}
        aliases: set[str] = set()
        for _name, info in Settings.model_fields.items():
            aliases |= _aliases_of(info)

        unknown = [
            key
            for key in _keys_in_env_example()
            if key.lower() not in fields
            and key.upper() not in aliases
            and key.upper() not in _EXTERNAL_ENV_KEYS
        ]
        assert not unknown, (
            "这些键在 .env.example 里,但没有任何组件会读它们 —— "
            "写进 .env 的人会以为改生效了,实际不会:\n  "
            + "\n  ".join(unknown)
        )

    def test_示例里不该出现真实密钥(self):
        """模板文件是要提交的,不能把真 key 顺手带进去。

        ⚠️ 占位符本身也是 `sk-` 开头(`sk-your-llm-api-key-here`),
        所以不能只匹配 `sk-`;要排除掉明显是占位符的那些。
        """
        text = _ENV_EXAMPLE.read_text(encoding="utf-8")
        placeholders = ("your", "here", "xxx", "placeholder", "example", "change", "<", "填入")
        suspicious = [
            value
            for value in re.findall(r"=\s*\"?(sk-[A-Za-z0-9_.\-]{12,})\"?", text)
            if not any(hint in value.lower() for hint in placeholders)
        ]
        assert not suspicious, f".env.example 里疑似有真实 key: {suspicious}"
