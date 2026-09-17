"""配置管理模块"""

import os
from pathlib import Path
from typing import List
from pydantic_settings import BaseSettings
from dotenv import load_dotenv

# 加载环境变量
# 首先尝试加载当前目录的.env
load_dotenv()

# 然后尝试加载HelloAgents的.env(如果存在)
helloagents_env = Path(__file__).parent.parent.parent.parent / "HelloAgents" / ".env"
if helloagents_env.exists():
    load_dotenv(helloagents_env, override=False)  # 不覆盖已有的环境变量


class Settings(BaseSettings):
    """应用配置"""

    # 应用基本配置
    app_name: str = "途灵 TripMind"
    app_version: str = "1.0.0"
    debug: bool = False

    # 服务器配置
    host: str = "0.0.0.0"
    # 8000 被本机 Docker 容器占了(它会连着 IPv6 的 [::]:8000 一起抢),
    # 结果是浏览器访问 localhost:8000 连到 Docker 那边、返回空响应。
    # 换 8001 避开。前端配套地址见 frontend/.env 与 vite.config.ts。
    port: int = 8001

    # CORS配置 - 使用字符串,在代码中分割
    cors_origins: str = "http://localhost:5173,http://localhost:3000,http://127.0.0.1:5173,http://127.0.0.1:3000"

    # 高德地图API配置
    amap_api_key: str = ""

    # Unsplash API配置
    unsplash_access_key: str = ""
    unsplash_secret_key: str = ""

    # LLM配置 (从环境变量读取,由HelloAgents管理)
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4"

    # 日志配置
    log_level: str = "INFO"

    # ---------- RAG 向量库:Milvus ----------
    # 为什么是 Milvus 而不是框架原生支持的 Qdrant:
    # 本机 D:\devlop\Milvus 已经装好一套 Milvus standalone,起服务零成本。
    # 而框架**完全不支持 Milvus**(memory/storage 下只有 qdrant/neo4j/document 三个),
    # 且 create_rag_pipeline() 签名里没有 store 参数、无条件 new QdrantVectorStore,
    # 没有任何注入点。所以检索层由 app/services/knowledge_service.py 自己写。
    milvus_uri: str = "http://localhost:19530"

    # collection 名字里带维度,是为了防止"换了 embedding 模型但忘了重建库":
    # 维度不匹配时 Milvus 不会报错,而是写入/检索结果全乱 —— 属于静默失败。
    # 两个 namespace 各一个 collection(不是分区):分层清楚,且 stats 能分开看。
    rag_collection_poi: str = "trip_poi_facts_1024"
    rag_collection_guides: str = "trip_city_guides_1024"
    knowledge_dir: str = "./data/knowledge_base"

    # 注入 prompt 的条数。太多会挤占上下文、太少覆盖不到 —— 4 条是权衡后的取值
    rag_top_k: int = 4

    # ---------- Embedding(硅基流动,OpenAI 兼容) ----------
    embed_model_type: str = "dashscope"
    embed_model_name: str = "BAAI/bge-m3"
    embed_api_key: str = ""
    embed_base_url: str = "https://api.siliconflow.cn/v1"
    # 断言用:维度对不上说明模型换了人,collection 必须重建
    embed_dim_expected: int = 1024

    # ---------- 本地存储 ----------
    db_path: str = "./data/tripmind.db"
    runs_dir: str = "./data/runs"
    frozen_dir: str = "./data/frozen"

    # ---------- 功能开关(供评测做 A/B 对比) ----------
    enable_rag: bool = False
    agent_mode: str = "pipeline"

    class Config:
        env_file = ".env"
        case_sensitive = False
        extra = "ignore"  # 忽略额外的环境变量

    def get_cors_origins_list(self) -> List[str]:
        """获取CORS origins列表"""
        return [origin.strip() for origin in self.cors_origins.split(',')]


# 创建全局配置实例
settings = Settings()


def get_settings() -> Settings:
    """获取配置实例"""
    return settings


# 验证必要的配置
def validate_config():
    """验证配置是否完整"""
    errors = []
    warnings = []

    if not settings.amap_api_key:
        errors.append("AMAP_API_KEY未配置")

    # HelloAgentsLLM会自动从LLM_API_KEY读取,不强制要求OPENAI_API_KEY
    llm_api_key = os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not llm_api_key:
        warnings.append("LLM_API_KEY或OPENAI_API_KEY未配置,LLM功能可能无法使用")

    if errors:
        error_msg = "配置错误:\n" + "\n".join(f"  - {e}" for e in errors)
        raise ValueError(error_msg)

    if warnings:
        print("\n⚠️  配置警告:")
        for w in warnings:
            print(f"  - {w}")

    return True


# 打印配置信息(用于调试)
def print_config():
    """打印当前配置(隐藏敏感信息)"""
    print(f"应用名称: {settings.app_name}")
    print(f"版本: {settings.app_version}")
    print(f"服务器: {settings.host}:{settings.port}")
    print(f"高德地图API Key: {'已配置' if settings.amap_api_key else '未配置'}")

    # 检查LLM配置
    llm_api_key = os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
    llm_base_url = os.getenv("LLM_BASE_URL") or settings.openai_base_url
    llm_model = os.getenv("LLM_MODEL_ID") or settings.openai_model

    print(f"LLM API Key: {'已配置' if llm_api_key else '未配置'}")
    print(f"LLM Base URL: {llm_base_url}")
    print(f"LLM Model: {llm_model}")
    print(f"日志级别: {settings.log_level}")

