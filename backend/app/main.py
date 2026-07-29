import os
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from contextlib import asynccontextmanager
from loguru import logger

from app.core.config import settings
from app.core.exceptions import AppException, app_exception_handler, global_exception_handler
from app.services.init_db import init_db
from app.services.scheduler import scheduler_manager
from app.core.logger import setup_logger

# Import all API router modules
from app.api.auth import router as auth_router
from app.api.jenkins import router as jenkins_router
from app.api.release import router as release_router
from app.api.history import router as history_router
from app.api.system import router as system_router

# Setup logger configuration
setup_logger()

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. Startup phase
    logger.info("Initializing Jenkins Release Scheduler service...")
    
    # Check if default JWT SECRET_KEY is being used
    if settings.SECRET_KEY == "your-super-secret-key-change-it-in-production":
        logger.critical(
            "\n"
            "=======================================================================\n"
            "   严重安全警告：JWT SECRET_KEY 正在使用默认值！                       \n"
            "   这允许攻击者轻易伪造身份令牌并控制整个调度系统。请务必在生产环境中修改！\n"
            "======================================================================="
        )
    
    # Initialize/verify database structure and administrator user seeding
    try:
        init_db()
    except Exception as e:
        logger.critical(f"Database initialization failed: {str(e)}")
        raise e
        
    # Start APScheduler with configured SQLAlchemy store
    try:
        scheduler_manager.start()
    except Exception as e:
        logger.critical(f"Failed to start APScheduler: {str(e)}")
        raise e
        
    # Start startup self-healing reconciliation asynchronously to avoid blocking lifespan
    try:
        from app.services.release_service import reconcile_running_tasks
        import threading
        t = threading.Thread(target=reconcile_running_tasks)
        t.start()
        logger.info("Triggered startup self-healing reconciliation thread.")
    except Exception as e:
        logger.error(f"Failed to start self-healing on startup: {str(e)}")
        
    yield
    
    # 2. Shutdown phase
    logger.info("Shutting down Jenkins Release Scheduler service...")
    try:
        scheduler_manager.shutdown()
    except Exception as e:
        logger.error(f"Error during scheduler shutdown: {str(e)}")
        
    logger.info("Service cleanup completed.")

app = FastAPI(
    title=settings.PROJECT_NAME,
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    lifespan=lifespan
)

# Bind custom and generic exception handlers to responses
app.add_exception_handler(AppException, app_exception_handler)
app.add_exception_handler(Exception, global_exception_handler)

# Register routes with tags for auto OpenAPI Swagger documentation
base_prefix = "" if settings.BASE_PATH == "/" else settings.BASE_PATH

@app.get("/health", tags=["Health Check"])
def health_check():
    return {"status": "ok", "base_path": settings.BASE_PATH}

app.include_router(auth_router, prefix=f"{base_prefix}{settings.API_V1_STR}/auth", tags=["Authentication"])
app.include_router(jenkins_router, prefix=f"{base_prefix}{settings.API_V1_STR}/jenkins", tags=["Jenkins Integration"])
app.include_router(release_router, prefix=f"{base_prefix}{settings.API_V1_STR}/release", tags=["Release Management"])
app.include_router(history_router, prefix=f"{base_prefix}{settings.API_V1_STR}/history", tags=["Execution Logs & History"])
app.include_router(system_router, prefix=f"{base_prefix}{settings.API_V1_STR}/system", tags=["System Settings & Dashboard"])

# ==========================================
# Vue Single Page Application Fallback Routing
# ==========================================
import urllib.parse

dist_path = "/app/dist"

@app.get("/{path_name:path}")
def spa_fallback(path_name: str):
    clean_path = path_name
    base_stripped = settings.BASE_PATH.strip('/')
    if base_stripped:
        prefix_to_strip = base_stripped + "/"
        if path_name.startswith(prefix_to_strip):
            clean_path = path_name[len(prefix_to_strip):]
        elif path_name == base_stripped:
            clean_path = ""
        else:
            raise HTTPException(status_code=404, detail="资源未找到")

    # 1. Prevent interception of API routes, Swagger Docs, or OpenAPI schemas
    # API 404 should return standard JSON responses, not index.html
    if clean_path.startswith("api/v1") or clean_path in ("docs", "redoc", "openapi.json"):
        raise HTTPException(status_code=404, detail="未找到该接口")
        
    # Normalize paths
    resolved_dist = os.path.abspath(dist_path)
    
    # Reject absolute paths, .., and encoded traversal sequences
    decoded_path_name = urllib.parse.unquote(clean_path)
    if ".." in decoded_path_name or decoded_path_name.startswith("/") or os.path.isabs(decoded_path_name):
        raise HTTPException(status_code=404, detail="资源未找到")
        
    target_path = os.path.abspath(os.path.join(resolved_dist, decoded_path_name))
    
    # Verify that target path remains inside static root
    try:
        common = os.path.commonpath([resolved_dist, target_path])
        if os.path.abspath(common) != resolved_dist:
            raise HTTPException(status_code=404, detail="资源未找到")
    except Exception:
        raise HTTPException(status_code=404, detail="资源未找到")
        
    # Reject symlinks pointing outside the static root
    if os.path.islink(target_path):
        real_target = os.path.realpath(target_path)
        try:
            common = os.path.commonpath([resolved_dist, real_target])
            if os.path.abspath(common) != resolved_dist:
                raise HTTPException(status_code=404, detail="资源未找到")
        except Exception:
            raise HTTPException(status_code=404, detail="资源未找到")

    # 2. Return physical asset if it exists in the built frontend directory (e.g. css/js/favicon)
    if os.path.isfile(target_path):
        return FileResponse(target_path)
        
    # 3. Fallback to index.html for Vue SPA client-side history routing
    index_path = os.path.join(resolved_dist, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
        
    raise HTTPException(status_code=404, detail="静态资源目录未初始化")

