"""配置管理模块"""

import os
from pathlib import Path
from typing import List
from pydantic import AliasChoices, Field, model_validator
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
    # ⚠️ 这一项**不能**只用裸名 `MILVUS_URI`:
    #    pymilvus 自己也读这个环境变量,而且是在 **import 时**读的
    #    (`pymilvus/settings.py`: `MILVUS_URI = os.getenv("MILVUS_URI", ...)`),
    #    然后把它**当远端地址**解析。所以当我们要用本地文件跑 Milvus Lite
    #    (uri 写 `xxx.db`)时,`MILVUS_URI` 这个名字一旦出现在环境里,
    #    pymilvus 在初始化阶段就会先抛
    #        Illegal uri: [...], expected form 'http[s]://...'
    #    —— 根本走不到它后面"本地文件 → 起 Lite"那个分支
    #    (`pymilvus/orm/connections.py`,两者相隔约 200 行)。
    #
    #    `MILVUS_URI` 仍留在别名列表里做兜底:**远端部署**用它本来就是
    #    http URL,与 pymilvus 的用法不冲突,老部署脚本不用改。
    #    本地文件场景请用 `TRIPMIND_MILVUS_URI`(声明在前的优先)。
    milvus_uri: str = Field(
        default="http://localhost:19530",
        validation_alias=AliasChoices("TRIPMIND_MILVUS_URI", "MILVUS_URI", "milvus_uri"),
    )

    # collection 名字里带维度,是为了防止"换了 embedding 模型但忘了重建库":
    # 维度不匹配时 Milvus 不会报错,而是写入/检索结果全乱 —— 属于静默失败。
    # 各 namespace 一个 collection(不是分区):分层清楚,且 stats 能分开看。
    rag_collection_poi: str = "trip_poi_facts_1024"
    rag_collection_guides: str = "trip_city_guides_1024"
    # 用户上传的攻略单独一层,**不混进内置攻略**:
    # 「重灌内置库」会 drop 整个 collection —— 混在一起的话,用户上传的
    # 攻略会被一起清掉。分层之后互不影响。
    rag_collection_uploaded: str = "trip_uploaded_guides_1024"
    knowledge_dir: str = "./data/knowledge_base"

    # 单个上传文件的字节上限。PDF 与 Markdown 的真实攻略远到不了这个量级;
    # 上限主要防的是误传视频/安装包把内存与 SQLite 撑爆。
    max_upload_bytes: int = 5 * 1024 * 1024

    # 注入 prompt 的条数。太多会挤占上下文、太少覆盖不到 —— 4 条是权衡后的取值
    rag_top_k: int = 4

    # ---------- 管理接口鉴权 ----------
    #
    # 项目**没有用户体系**(行程靠链接分享,这是产品设定,不是缺失),
    # 所以也不需要完整的登录 + RBAC。但有几类接口一旦公开就等同于把
    # 后台交出去 —— 实测确认过:部署上线后从公网可以直接
    #   · POST /api/knowledge/ingest  (带 recreate 就是**清库**)
    #   · POST /api/knowledge/docs/parse  (未鉴权的文件上传)
    #   · DELETE /api/knowledge/docs/{id}
    #   · POST /api/knowledge/rag-toggle  (任意人可开关 RAG)
    #   · DELETE /api/trip/plans/{id}     (删任意人的行程)
    # 所以给这些**写操作**加一个共享口令(`X-Admin-Token` 请求头)。
    #
    # 只读接口(status / search / sources / 行程列表与详情)不加:
    # 它们要么是前端正常展示要用的,要么本来就是要分享出去的。
    #
    # ⚠️ 没有配置时是 **fail-closed** —— 见 `app/api/deps.py` 的
    #    `require_admin()`:不配就返回 503,而不是放行。公开部署上
    #    「忘了配」不该等于「任何人都能清库」。
    #
    # 用 AliasChoices 是因为字段叫 `admin_token`,而 .env 里想写成
    # `TRIPMIND_ADMIN_TOKEN`(带前缀不易和别人的变量撞)。
    admin_token: str = Field(
        default="",
        validation_alias=AliasChoices("TRIPMIND_ADMIN_TOKEN", "admin_token"),
    )

    # ---------- Embedding(硅基流动,OpenAI 兼容) ----------
    embed_model_type: str = "dashscope"
    embed_model_name: str = "BAAI/bge-m3"
    embed_api_key: str = ""
    embed_base_url: str = "https://api.siliconflow.cn/v1"
    # 断言用:维度对不上说明模型换了人,collection 必须重建
    embed_dim_expected: int = 1024

    # ---------- 本地存储 ----------
    #
    # ⚠️ `TRIPMIND_DB` 这个名字**曾经是一个静默失效的配置项**。
    #
    # pydantic-settings 默认按**字段名**匹配环境变量,而这个字段叫 `db_path` ——
    # 所以 `.env` 里写 `TRIPMIND_DB=...` 会被当成未知变量。更糟的是
    # `extra = "ignore"` 让它**不报任何错**:用户以为改了数据库位置,
    # 实际一直用的是默认的 `./data/tripmind.db`。
    #
    # 是给 Docker 配数据卷时发现的:卷挂在别处、库却still 写在容器里,
    # 一重建容器数据就没了 —— 而日志里一句提示都没有。
    #
    # 现在两种写法都认(AliasChoices 会依次尝试),旧的部署脚本不用改。
    db_path: str = Field(
        default="./data/tripmind.db",
        validation_alias=AliasChoices("TRIPMIND_DB", "DB_PATH", "db_path"),
    )
    runs_dir: str = "./data/runs"
    frozen_dir: str = "./data/frozen"

    # ---------- 功能开关(供评测做 A/B 对比) ----------
    enable_rag: bool = False
    agent_mode: str = "pipeline"

    @model_validator(mode="after")
    def _assert_lite_uri_not_shadowed(self):
        """`MILVUS_URI` 里如果是**本地路径**,就必须拦住。

        判断依据只有一条:`MILVUS_URI` 的值是不是能被当成远端地址解析。
        因为 pymilvus 在 import 时就读它、并**无条件**按远端地址解析
        (`pymilvus/orm/connections.py` 里 `__parse_address_from_uri`),
        一旦 `urlparse(值).netloc` 为空就抛
        `Illegal uri: [...], expected form 'http[s]://...'`。

        注意它**与本项目自己用哪个 uri 无关** —— 就算我们把值放在
        `TRIPMIND_MILVUS_URI` 里,pymilvus 照样会去解析 `MILVUS_URI`。
        所以这里只看那一个变量,不看 `self.milvus_uri`。

        报错信息完全指不到"名字撞了"这件事,排查时会一直以为是路径写错,
        所以宁可**在启动时**用一句说得清的话失败(与"启动时集中校验、
        快速失败"的约定一致),也不要等用户点开知识库才看到莫名其妙的报错。
        """
        shadow = (os.environ.get("MILVUS_URI") or "").strip()
        # 有 "://" 就说明是给 pymilvus 用的远端地址,正常用法,放行
        if not shadow or "://" in shadow:
            return self
        raise ValueError(
            f"环境变量 MILVUS_URI={shadow!r} 看起来是**本地路径**,"
            f"但它是 pymilvus 自己的配置项,要求 http(s) URL。\n"
            f"    pymilvus 在 import 时就会读它并**按远端地址解析**"
            f"(pymilvus/settings.py),\n"
            f"    于是会先抛 'Illegal uri',根本走不到它里面"
            f"'本地文件 → 起 Milvus Lite'那个分支。\n"
            f"    改法:本地文件路径改用 TRIPMIND_MILVUS_URI,"
            f"把 MILVUS_URI 从环境里去掉(或改成 http://host:19530 这样的远端地址)。"
        )

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


# ===========================================================================
# ENABLE_RAG 的运行时开关
# ===========================================================================
#
# .env 里的 ENABLE_RAG 默认是 **关** 的 —— 那是给评测做 A/B 对比用的基线。
# 但「用户上传了攻略却根本没被用上」是更糟的体验:界面上显示上传成功,
# 生成行程时知识库却一点没参与,而且**没有任何提示**。
#
# 所以加一个**运行时覆盖**:上传攻略时自动打开,重启即恢复 .env 的值。
# 刻意不写回 .env —— .env 是评测基线的唯一来源,让业务流程去改它,
# 下次跑评测时基线就已经被业务操作污染了。这也是它必须放在内存的原因。

_rag_runtime_override: bool | None = None


def rag_state() -> dict:
    """当前开关状态与来源,给状态接口用。

    source 区分 'env'(来自 .env,评测基线)与 'runtime'(业务流程打开的),
    界面据此提示"这是临时开启的,重启后恢复"。
    """
    if _rag_runtime_override is None:
        return {"enabled": bool(settings.enable_rag), "source": "env"}
    return {"enabled": _rag_runtime_override, "source": "runtime"}


def rag_enabled() -> bool:
    """当前实际生效的开关。**所有读 ENABLE_RAG 的地方都应改用它**,
    而不是直接读 `settings.enable_rag` —— 否则运行时覆盖会漏掉那一处,
    症状是「界面显示已开启,生成行程时却没用知识库」。"""
    return rag_state()["enabled"]


def set_rag_enabled(enabled: bool) -> dict:
    """设置运行时开关,返回与 `rag_state()` 相同的结构。"""
    global _rag_runtime_override
    _rag_runtime_override = bool(enabled)
    return rag_state()


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

