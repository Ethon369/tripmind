"""LLM服务模块"""

from hello_agents import HelloAgentsLLM
from ..config import get_settings
from ..observability import MeteredLLM

# 全局LLM实例
_llm_instance = None


def get_llm() -> HelloAgentsLLM:
    """
    获取LLM实例(单例模式)

    返回的是 MeteredLLM —— 继承自框架的 HelloAgentsLLM,行为完全一致,
    只是把框架丢掉的 response.usage 接住,用于统计 token 与成本。
    计量只在 collect_usage() 上下文里生效,否则与原生类无差别。

    Returns:
        HelloAgentsLLM实例(实际类型 MeteredLLM)
    """
    global _llm_instance

    if _llm_instance is None:
        settings = get_settings()

        # 会自动从环境变量读取配置(LLM_API_KEY / LLM_BASE_URL / LLM_MODEL_ID)
        _llm_instance = MeteredLLM()

        print(f"✅ LLM服务初始化成功")
        print(f"   提供商: {_llm_instance.provider}")
        print(f"   模型: {_llm_instance.model}")

    return _llm_instance


def reset_llm():
    """重置LLM实例(用于测试或重新配置)"""
    global _llm_instance
    _llm_instance = None

