from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from sqlalchemy.future import select
from sqlalchemy import func, desc
from datetime import datetime, timedelta, time
from typing import List, Dict, Any, Optional
from concurrent.futures import ThreadPoolExecutor

from app.core.database import get_db
from app.api.deps import get_current_user, get_current_active_admin, log_action
from app.services.deps_helper import get_client_ip
from app.models.release import ReleasePlan, ReleaseHistory
from app.models.jenkins import JenkinsServer
from app.models.system import SystemConfig, NotifyConfig, AuditLog
from app.models.user import User
from app.schemas.system import (
    SystemConfigResponse, SystemConfigCreate,
    NotifyConfigResponse, NotifyConfigCreate, AuditLogResponse
)
from app.services.jenkins_client import JenkinsClient

router = APIRouter()

@router.get("/dashboard/stats")
def get_dashboard_stats(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    # Today's start and end times
    today_start = datetime.combine(datetime.now().date(), time.min)
    
    # 1. Total Plans Created Today
    today_count = db.query(func.count(ReleasePlan.id)).filter(ReleasePlan.created_at >= today_start).scalar() or 0
    
    # 2. Waiting and Running Counts
    waiting_count = db.query(func.count(ReleasePlan.id)).filter(ReleasePlan.status == "WAITING").scalar() or 0
    running_count = db.query(func.count(ReleasePlan.id)).filter(ReleasePlan.status == "RUNNING").scalar() or 0
    
    # 3. Successful vs Failed Counts
    success_count = db.query(func.count(ReleaseHistory.id)).filter(ReleaseHistory.status == "SUCCESS").scalar() or 0
    failed_count = db.query(func.count(ReleaseHistory.id)).filter(ReleaseHistory.status == "FAILED").scalar() or 0
    
    # 4. Recent 7 days trend
    trend_labels = []
    trend_success = []
    trend_failed = []
    
    for i in range(6, -1, -1):
        target_date = datetime.now().date() - timedelta(days=i)
        start_dt = datetime.combine(target_date, time.min)
        end_dt = datetime.combine(target_date, time.max)
        
        s_count = db.query(func.count(ReleaseHistory.id)).filter(
            ReleaseHistory.status == "SUCCESS",
            ReleaseHistory.created_at.between(start_dt, end_dt)
        ).scalar() or 0
        
        f_count = db.query(func.count(ReleaseHistory.id)).filter(
            ReleaseHistory.status == "FAILED",
            ReleaseHistory.created_at.between(start_dt, end_dt)
        ).scalar() or 0
        
        trend_labels.append(target_date.strftime("%Y-%m-%d"))
        trend_success.append(s_count)
        trend_failed.append(f_count)
        
    # 5. Jenkins Servers Connection Health Info
    servers = db.query(JenkinsServer).all()
    server_health = []
    
    def check_health(s):
        client = JenkinsClient(s.url, s.username, s.api_token)
        success, _ = client.test_connection()
        return {"name": s.name, "status": "UP" if success else "DOWN"}
        
    if servers:
        with ThreadPoolExecutor(max_workers=len(servers)) as executor:
            health_results = list(executor.map(check_health, servers))
        server_health.extend(health_results)
        
    # 6. Recent 10 history lines
    raw_histories = db.query(ReleaseHistory).order_by(desc(ReleaseHistory.created_at)).limit(10).all()
    recent_histories = []
    for h in raw_histories:
        recent_histories.append({
            "id": h.id,
            "job_name": h.job_name,
            "branch": h.branch,
            "build_number": h.build_number,
            "status": h.status,
            "trigger_by": h.trigger_by,
            "created_at": h.created_at
        })
        
    return {
        "stats": {
            "today_releases": today_count,
            "waiting_releases": waiting_count,
            "running_releases": running_count,
            "success_rate": round(success_count / (success_count + failed_count) * 100, 2) if (success_count + failed_count) > 0 else 100.0,
            "failed_releases": failed_count
        },
        "trend": {
            "labels": trend_labels,
            "success": trend_success,
            "failed": trend_failed
        },
        "servers": server_health,
        "recent_history": recent_histories
    }

# Notification Config Routing
@router.post("/notify-configs", response_model=NotifyConfigResponse)
def create_notify_config(
    request: Request,
    config: NotifyConfigCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_admin)
):
    db_config = NotifyConfig(
        name=config.name,
        channel_type=config.channel_type,
        webhook_url=config.webhook_url,
        secret=config.secret,
        keyword=config.keyword,
        is_active=config.is_active,
        trigger_events=config.trigger_events
    )
    db.add(db_config)
    db.commit()
    db.refresh(db_config)
    log_action(db, current_user, "CREATE_NOTIFY_CONFIG", get_client_ip(request), f"Created Notification Channel {config.name}")
    return db_config

@router.get("/notify-configs", response_model=List[NotifyConfigResponse])
def list_notify_configs(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    return db.query(NotifyConfig).all()

@router.put("/notify-configs/{config_id}", response_model=NotifyConfigResponse)
def update_notify_config(
    request: Request,
    config_id: int,
    config_in: NotifyConfigCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_admin)
):
    config = db.get(NotifyConfig, config_id)
    if not config:
        raise HTTPException(status_code=404, detail="未找到该通知渠道配置")
        
    for field, value in config_in.model_dump().items():
        if field == "secret" and value == "••••••••":
            continue
        if field == "webhook_url" and "••••••••" in str(value):
            continue
        setattr(config, field, value)
        
    db.commit()
    db.refresh(config)
    log_action(db, current_user, "UPDATE_NOTIFY_CONFIG", get_client_ip(request), f"Updated Notification Channel {config.name}")
    return config

@router.delete("/notify-configs/{config_id}")
def delete_notify_config(
    request: Request,
    config_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_admin)
):
    config = db.get(NotifyConfig, config_id)
    if not config:
        raise HTTPException(status_code=404, detail="未找到该通知渠道配置")
    db.delete(config)
    db.commit()
    log_action(db, current_user, "DELETE_NOTIFY_CONFIG", get_client_ip(request), f"Deleted Notification Channel {config.name}")
    return {"success": True, "message": "通知渠道已成功删除"}

@router.post("/notify-configs/{config_id}/test")
def test_notify_config(
    request: Request,
    config_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_admin)
):
    config = db.get(NotifyConfig, config_id)
    if not config:
        raise HTTPException(status_code=404, detail="未找到该通知渠道配置")
        
    from app.services.notification import send_notification_to_channel
    
    title = "测试消息"
    content = "恭喜，当前通知渠道测试发送成功！本系统消息调度及安全校验参数已配置正确，能够正常推送通知~"
    markdown_content = "恭喜，当前通知渠道测试发送成功！本系统消息调度及安全校验参数已配置正确，能够正常推送通知~"
    
    try:
        send_notification_to_channel(config, title, content, markdown_content)
        log_action(db, current_user, "TEST_NOTIFY_CONFIG", get_client_ip(request), f"Sent Test Notification to Channel {config.name}")
        return {"success": True, "message": "测试消息已发出，请在对应的通知群内检查是否收到"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"发送测试消息失败: {str(e)}")


# System Config Routing
@router.get("/configs", response_model=List[SystemConfigResponse])
def list_system_configs(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_admin)
):
    configs = db.query(SystemConfig).all()
    # Mask sensitive credentials in memory
    for c in configs:
        k_lower = c.config_key.lower()
        if any(w in k_lower for w in ("key", "password", "secret", "token")):
            c.config_value = "••••••••"
    return configs

@router.post("/configs", response_model=SystemConfigResponse)
def set_system_config(
    request: Request,
    config: SystemConfigCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_admin)
):
    if not config.config_key:
        raise HTTPException(status_code=400, detail="配置键不能为空")
        
    db_config = db.query(SystemConfig).filter(SystemConfig.config_key == config.config_key).first()
    
    # Check if a mask was submitted
    is_masked = config.config_value == "••••••••" or (config.config_value and all(char in "•*" for char in config.config_value))
    
    if db_config:
        if is_masked:
            # Mask submitted, keep original value
            pass
        else:
            db_config.config_value = config.config_value
        db_config.description = config.description
    else:
        if is_masked:
            raise HTTPException(status_code=400, detail="新建配置不能使用掩码值")
        db_config = SystemConfig(
            config_key=config.config_key,
            config_value=config.config_value,
            description=config.description
        )
        db.add(db_config)
        
    db.commit()
    db.refresh(db_config)
    log_action(db, current_user, "SET_SYSTEM_CONFIG", get_client_ip(request), f"Configured System key: {config.config_key}")
    
    # Return masked response to prevent leaking sensitive fields
    k_lower = db_config.config_key.lower()
    if any(w in k_lower for w in ("key", "password", "secret", "token")):
        from app.schemas.system import SystemConfigResponse
        return SystemConfigResponse(
            id=db_config.id,
            config_key=db_config.config_key,
            config_value="••••••••",
            description=db_config.description
        )
        
    return db_config

# Audit log Routing
@router.get("/audit-logs", response_model=List[AuditLogResponse])
def list_audit_logs(
    db: Session = Depends(get_db),
    username: Optional[str] = None,
    page: int = 1,
    limit: int = 50,
    current_user: User = Depends(get_current_active_admin)
):
    offset = (page - 1) * limit
    stmt = db.query(AuditLog).order_by(desc(AuditLog.created_at))
    if username:
        stmt = stmt.filter(AuditLog.username.like(f"%{username}%"))
    return stmt.offset(offset).limit(limit).all()

from app.core.security import decrypt_secret

@router.post("/decrypt-field")
def decrypt_field_api(
    data: Dict[str, str],
    current_user: User = Depends(get_current_user)
):
    cipher_text = data.get("text", "")
    if not cipher_text:
        return {"decrypted": ""}
    if cipher_text.startswith("enc:"):
        cipher_text = cipher_text[4:]
    try:
        decrypted = decrypt_secret(cipher_text)
        return {"decrypted": decrypted}
    except Exception:
        return {"decrypted": cipher_text}
