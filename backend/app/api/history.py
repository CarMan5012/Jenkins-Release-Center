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
        
    logs_content = history.logs or ""
    sliced_logs = logs_content[start:]
    return {
        "log_text": sliced_logs,
        "next_start": start + len(sliced_logs),
        "has_more": False
    }

@router.post("/sync")
def sync_external_history(
    current_user: str = Depends(get_current_user)
):
    sync_external_builds()
    return {"message": "外部构建记录已成功同步"}
