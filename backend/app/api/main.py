"""FastAPI主应用"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from ..config import get_settings, validate_config, print_config
from ..store import get_user_store
from .routes import trip, poi, knowledge, map as map_routes, auth

# 获取配置
settings = get_settings()

# 创建FastAPI应用
app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="途灵 TripMind - 基于HelloAgents框架的智能旅行规划API",
    docs_url="/docs",
    redoc_url="/redoc"
)

# 配置CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.get_cors_origins_list(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
# auth 放在最前面:它不依赖任何别的路由,而且出问题时(登录不通)
# 是整个应用的第一个阻塞点,放在列表开头便于在 /docs 里一眼看到。
app.include_router(auth.router, prefix="/api")
app.include_router(trip.router, prefix="/api")
app.include_router(poi.router, prefix="/api")
app.include_router(map_routes.router, prefix="/api")
# 知识库(RAG)。在此之前的唯一入口是 scripts/ingest_knowledge.py,
# 前端没有接入点 —— 所以这个 router 是「知识库页面」的前置条件。
app.include_router(knowledge.router, prefix="/api")


def _bootstrap_accounts() -> None:
    """建库/建引导管理员/清过期会话。

    放在 startup 里而不是"第一次请求时懒执行":懒执行会让**并发的前几个
    请求**同时走到建账号那一步,而建账号不是幂等的(会撞唯一索引)。
    启动时做一次就没有这个问题。

    ⚠️ 三件事都必须**不阻塞启动**:任何一件失败都只打日志。理由是
    「服务起不来」比「引导管理员没建成」严重得多 —— 前者连登录页都打不开,
    后者还能靠 `scripts/manage_users.py` 补救。
    """
    try:
        store = get_user_store()
    except Exception as exc:
        print(f"⚠️  账号库初始化失败,登录功能将不可用: {type(exc).__name__}: {exc}")
        return

    try:
        purged = store.purge_expired()
        if purged:
            print(f"🧹 已清理 {purged} 个过期会话")
    except Exception as exc:
        print(f"⚠️  清理过期会话失败(不影响启动): {type(exc).__name__}: {exc}")

    try:
        created = store.ensure_bootstrap_admin(settings.admin_username, settings.admin_password)
    except Exception as exc:
        print(f"⚠️  引导管理员创建失败: {type(exc).__name__}: {exc}")
        return

    if created is not None:
        print("\n" + "=" * 60)
        print("👤 已创建引导管理员账号(仅在库中无任何账号时执行一次)")
        print(f"   用户名: {created['username']}")
        print("   密码  : 见 backend/.env 的 TRIPMIND_ADMIN_PASSWORD")
        print("           (未配置时为默认值 admin123 —— 请立即在「账号管理」里修改)")
        print("=" * 60 + "\n")


@app.on_event("startup")
async def startup_event():
    """应用启动事件"""
    print("\n" + "="*60)
    print(f"🚀 {settings.app_name} v{settings.app_version}")
    print("="*60)
    
    # 打印配置信息
    print_config()
    
    # 验证配置
    try:
        validate_config()
        print("\n✅ 配置验证通过")
    except ValueError as e:
        print(f"\n❌ 配置验证失败:\n{e}")
        print("\n请检查.env文件并确保所有必要的配置项都已设置")
        raise

    # 账号体系。必须在校验之后、打印地址之前 —— 建出来的引导账号是
    # "怎么登进去"的一部分,要和 API 地址印在同一屏里。
    _bootstrap_accounts()

    print("\n" + "="*60)
    # 用 settings.port 而不是写死端口 —— 否则改了 PORT 这里会继续打印旧地址
    print(f"📚 API文档: http://127.0.0.1:{settings.port}/docs")
    print(f"📖 ReDoc文档: http://127.0.0.1:{settings.port}/redoc")
    print("="*60 + "\n")


@app.on_event("shutdown")
async def shutdown_event():
    """应用关闭事件"""
    print("\n" + "="*60)
    print("👋 应用正在关闭...")
    print("="*60 + "\n")


@app.get("/")
async def root():
    """根路径"""
    return {
        "name": settings.app_name,
        "version": settings.app_version,
        "status": "running",
        "docs": "/docs",
        "redoc": "/redoc"
    }


@app.get("/health")
async def health():
    """健康检查"""
    return {
        "status": "healthy",
        "service": settings.app_name,
        "version": settings.app_version
    }


if __name__ == "__main__":
    import uvicorn
    
    uvicorn.run(
        "app.api.main:app",
        host=settings.host,
        port=settings.port,
        reload=True
    )

