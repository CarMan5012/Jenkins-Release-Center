from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import desc
from typing import List, Optional

from app.core.database import get_db
from app.api.deps import get_current_user
from app.models.release import ReleaseHistory, ReleaseTask
from app.models.jenkins import JenkinsServer
from app.schemas.release import ReleaseHistoryResponse, BuildLogResponse
from app.services.jenkins_client import JenkinsClient
from app.services.jenkins_sync_task import sync_external_builds

router = APIRouter()

@router.get("", response_model=List[ReleaseHistoryResponse])
def list_histories(
    db: Session = Depends(get_db),
    job_name: Optional[str] = None,
    status: Optional[str] = None,
    is_external: Optional[bool] = None,
    page: int = 1,
    limit: int = 20,
    current_user: str = Depends(get_current_user)
):
    offset = (page - 1) * limit
    stmt = db.query(ReleaseHistory).order_by(desc(ReleaseHistory.created_at))
    if job_name:
        stmt = stmt.filter(ReleaseHistory.job_name.like(f"%{job_name}%"))
    if status:
        stmt = stmt.filter(ReleaseHistory.status == status)
    if is_external is not None:
        stmt = stmt.filter(ReleaseHistory.is_external == is_external)
        
    return stmt.offset(offset).limit(limit).all()

@router.get("/{history_id}", response_model=ReleaseHistoryResponse)
def get_history_detail(
    history_id: int,
    db: Session = Depends(get_db),
    current_user: str = Depends(get_current_user)
):
    history = db.get(ReleaseHistory, history_id)
    if not history:
        raise HTTPException(status_code=404, detail="未找到该发布历史记录")
    return history

@router.get("/tasks/{task_id}/logs", response_model=BuildLogResponse)
def get_task_logs(
    task_id: int,
    start: int = 0,
    db: Session = Depends(get_db),
    current_user: str = Depends(get_current_user)
):
    task = db.get(ReleaseTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="未找到该发布任务")
        
    # Pattern 1: Task completed. Check if log is archived in history
    if task.status in ["SUCCESS", "FAILED"]:
        history = db.query(ReleaseHistory).filter(ReleaseHistory.task_id == task_id).first()
        if history and history.logs:
            # We have archived logs. Return the chunk from start offset.
            logs_content = history.logs
            sliced_logs = logs_content[start:]
            return {
                "log_text": sliced_logs,
                "next_start": start + len(sliced_logs),
                "has_more": False
            }
            
    # Pattern 2: Task is running or log was not cached. Retrieve from Jenkins server.
    if not task.build_number:
        return {
            "log_text": "Jenkins build is initializing and has not allocated a build number yet...",
            "next_start": 0,
            "has_more": True
        }
        
    server = db.get(JenkinsServer, task.server_id)
    if not server:
        raise HTTPException(status_code=400, detail="关联的 Jenkins 服务器配置已丢失")
        
    # Executed directly under thread pool by FastAPI when defined as a sync route
    client = JenkinsClient(server.url, server.username, server.api_token)
    log_text, next_start, has_more = client.get_progressive_log(task.job_name, task.build_number, start)
    
    return {
        "log_text": log_text,
        "next_start": next_start,
        "has_more": has_more
    }

@router.get("/{history_id}/logs", response_model=BuildLogResponse)
def get_history_logs(
    history_id: int,
    start: int = 0,
    db: Session = Depends(get_db),
    current_user: str = Depends(get_current_user)
):
    history = db.get(ReleaseHistory, history_id)
    if not history:
        raise HTTPException(status_code=404, detail="未找到该发布历史记录")
        
    # Lazy-load or stream progressive logs from Jenkins if logs cache is missing or currently BUILDING
    if not history.logs or history.status == "BUILDING":
        if history.job_name is not None and history.build_number is not None:
            if history.server_id is not None:
                server = db.get(JenkinsServer, history.server_id)
            elif history.server_name is not None:
                server = db.query(JenkinsServer).filter(JenkinsServer.name == history.server_name).first()
            else:
                server = None
            if server:
                try:
                    client = JenkinsClient(server.url, server.username, server.api_token)
                    log_text, next_start, has_more = client.get_progressive_log(history.job_name, history.build_number, start)
                    if log_text:
                        if not history.logs:
                            history.logs = log_text[:200000]
                        elif history.status == "BUILDING":
                            history.logs = (history.logs + log_text)[:200000]
                        db.commit()
                    return {
                        "log_text": log_text,
                        "next_start": next_start,
                        "has_more": has_more
                    }
                except Exception:
                    pass

    logs_content = history.logs or ""
    sliced_logs = logs_content[start:]
    return {
        "log_text": sliced_logs,
        "next_start": start + len(sliced_logs),
        "has_more": False
    }

@router.post("/sync")
def sync_external_history(
    job_name: Optional[str] = None,
    server_id: Optional[int] = None,
    current_user: str = Depends(get_current_user)
):
    sync_external_builds(server_id=server_id, job_name=job_name)
    return {"message": "外部构建记录已成功同步"}

from sqlalchemy import text

from app.models.system import AuditLog

@router.post("/reset-sequence")
def reset_history_sequence(
    db: Session = Depends(get_db),
    current_user: str = Depends(get_current_user)
):
    """
    Clear all history records and reset the auto-increment ID counter back to 1.
    """
    try:
        db.query(ReleaseHistory).delete()
        db.flush()
        
        # 多数据库方言支持，彻底归零重置自增主键 ID
        bind = db.get_bind()
        dialect_name = bind.dialect.name.lower() if bind and hasattr(bind, "dialect") else ""
        
        if "sqlite" in dialect_name:
            try:
                db.execute(text("DELETE FROM sqlite_sequence WHERE name='release_history'"))
            except Exception:
                # sqlite_sequence 系统表仅在 SQLite 产生 AUTOINCREMENT 时建立，若不存在则后续新增默认从 #1 开始，忽略即可
                pass
        elif "mysql" in dialect_name:
            db.execute(text("ALTER TABLE release_history AUTO_INCREMENT = 1"))
        elif "postgresql" in dialect_name or "postgres" in dialect_name:
            db.execute(text("ALTER SEQUENCE release_history_id_seq RESTART WITH 1"))
        else:
            try:
                db.execute(text("DELETE FROM sqlite_sequence WHERE name='release_history'"))
            except Exception:
                pass
            try:
                db.execute(text("ALTER TABLE release_history AUTO_INCREMENT = 1"))
            except Exception:
                pass

        # 提取用户名字符串与用户 ID
        username_str = getattr(current_user, "username", str(current_user))
        user_id_val = getattr(current_user, "id", None)

        # 写入安全审计日志
        db.add(AuditLog(
            user_id=user_id_val,
            username=username_str,
            action="RESET_HISTORY_SEQUENCE",
            details="清空所有构建发布历史记录并重置自增主键 ID 序号归零从 #1 开始"
        ))
        
        db.commit()
        return {"success": True, "message": "历史记录 ID 序号已成功重置，后续新增记录将从 #1 开始计算"}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"重置历史记录 ID 失败: {str(e)}")
