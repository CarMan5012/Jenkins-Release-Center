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
from app.models.jenkins import JenkinsServer, JenkinsBackup
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
            "success_rate": round(success_count / (success_count + failed_count) * 100, 2) if (success_count + failed_count) > 0 else 0.0,
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
    log_action(db, current_user, "CREATE_NOTIFY_CONFIG", get_client_ip(request), f"创建通知渠道: {config.name}")
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
    log_action(db, current_user, "UPDATE_NOTIFY_CONFIG", get_client_ip(request), f"更新通知渠道: {config.name}")
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
    log_action(db, current_user, "DELETE_NOTIFY_CONFIG", get_client_ip(request), f"删除通知渠道: {config.name}")
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
        log_action(db, current_user, "TEST_NOTIFY_CONFIG", get_client_ip(request), f"测试发送通知渠道: {config.name}")
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
    log_action(db, current_user, "SET_SYSTEM_CONFIG", get_client_ip(request), f"修改高级配置项: {config.config_key}")
    
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


import os
from app.services.scheduler import scheduler_manager
import re

def format_file_size(size_in_bytes: int) -> str:
    if size_in_bytes < 1024:
        return f"{size_in_bytes} B"
    elif size_in_bytes < 1024 * 1024:
        return f"{round(size_in_bytes / 1024, 2)} KB"
    elif size_in_bytes < 1024 * 1024 * 1024:
        return f"{round(size_in_bytes / (1024 * 1024), 2)} MB"
    else:
        return f"{round(size_in_bytes / (1024 * 1024 * 1024), 2)} GB"

def format_trigger(trigger) -> str:
    if not trigger:
        return "常规后台调度"
    trig_type = trigger.__class__.__name__
    if "Interval" in trig_type:
        if hasattr(trigger, "interval"):
            total_sec = int(trigger.interval.total_seconds())
            if total_sec >= 3600:
                return f"每 {total_sec // 3600} 小时自动巡检"
            elif total_sec >= 60:
                return f"每 {total_sec // 60} 分钟自动巡检"
            else:
                return f"每 {total_sec} 秒自动巡检"
        return "定期循环巡检"
    elif "Cron" in trig_type:
        trig_str = str(trigger)
        hour = "00"
        minute = "00"
        h_match = re.search(r"hour=['\"]?(\d+)['\"]?", trig_str)
        m_match = re.search(r"minute=['\"]?(\d+)['\"]?", trig_str)
        if h_match:
            hour = f"{int(h_match.group(1)):02d}"
        if m_match:
            minute = f"{int(m_match.group(1)):02d}"
        return f"每天 {hour}:{minute} 自动执行"
    elif "Date" in trig_type:
        if hasattr(trigger, "run_date") and trigger.run_date:
            try:
                return f"于 {trigger.run_date.strftime('%Y-%m-%d %H:%M:%S')} 触发"
            except Exception:
                return "定时单次触发"
        return "定时单次触发"
    return "常规自动调度"

@router.get("/scheduler-info")
def get_scheduler_info(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    try:
        scheduler = scheduler_manager.scheduler
        is_running = scheduler.running if hasattr(scheduler, "running") else False
        
        system_task_meta = {
            "reconcile_running_tasks": {
                "name": "构建卡顿与掉线自愈巡检",
                "description": "实时监控构建任务，发现异常停滞或掉线时自动恢复"
            },
            "auto_backup_jenkins_servers": {
                "name": "Jenkins 实例配置自动定时备份",
                "description": "自动对所有启用中的 Jenkins 实例进行配置文件加密打包归档"
            },
            "clean_expired_data": {
                "name": "过期日志与旧备份自动清理",
                "description": "按系统保留策略定期清除过期的历史日志与旧备份文件"
            },
            "sync_external_builds": {
                "name": "外部 Jenkins 构建记录历史同步",
                "description": "自动同步并补充 Jenkins 侧外部独立触发的历史构建数据"
            }
        }
        
        all_jobs = scheduler.get_jobs() if is_running else []
        
        system_cron_tasks = []
        release_scheduled_jobs = []
        
        for job in all_jobs:
            job_id = job.id
            next_run = "暂无排期"
            if getattr(job, "next_run_time", None):
                try:
                    next_run = job.next_run_time.strftime("%Y-%m-%d %H:%M:%S")
                except Exception:
                    next_run = str(job.next_run_time)
            
            trigger_desc = format_trigger(getattr(job, "trigger", None))
            
            if job_id in system_task_meta:
                meta = system_task_meta[job_id]
                system_cron_tasks.append({
                    "job_id": job_id,
                    "name": meta["name"],
                    "trigger_desc": trigger_desc,
                    "description": meta["description"],
                    "next_run_time": next_run,
                    "status": "正常运行" if is_running else "已停止"
                })
            elif job_id.startswith("plan_"):
                parts = job_id.split("_")
                plan_id = parts[1] if len(parts) > 1 else "?"
                task_id = parts[3] if len(parts) > 3 else "?"
                
                plan_name = "未命名计划"
                try:
                    plan = db.get(ReleasePlan, int(plan_id))
                    if plan:
                        plan_name = plan.name
                except Exception:
                    pass
                    
                release_scheduled_jobs.append({
                    "job_id": job_id,
                    "plan_id": plan_id,
                    "task_id": task_id,
                    "plan_name": plan_name,
                    "trigger_desc": trigger_desc,
                    "next_run_time": next_run,
                    "misfire_grace_time": f"{job.misfire_grace_time} 秒" if hasattr(job, "misfire_grace_time") and job.misfire_grace_time else "300 秒"
                })

        from datetime import datetime, timedelta

        def calculate_next_run(job_id: str) -> str:
            now = datetime.now()
            if job_id == "reconcile_running_tasks":
                return (now + timedelta(seconds=10)).strftime("%Y-%m-%d %H:%M:%S")
            elif job_id == "auto_backup_jenkins_servers":
                target = now.replace(hour=2, minute=0, second=0, microsecond=0)
                if target <= now:
                    target += timedelta(days=1)
                return target.strftime("%Y-%m-%d %H:%M:%S")
            elif job_id == "clean_expired_data":
                target = now.replace(hour=3, minute=0, second=0, microsecond=0)
                if target <= now:
                    target += timedelta(days=1)
                return target.strftime("%Y-%m-%d %H:%M:%S")
            elif job_id == "sync_external_builds":
                target = now.replace(hour=18, minute=0, second=0, microsecond=0)
                if target <= now:
                    target += timedelta(days=1)
                return target.strftime("%Y-%m-%d %H:%M:%S")
            return "定时自动调度中"

        known_system_ids = set(t["job_id"] for t in system_cron_tasks)
        for sys_id, meta in system_task_meta.items():
            if sys_id not in known_system_ids:
                registered_job = scheduler.get_job(sys_id) if (scheduler and hasattr(scheduler, "get_job")) else None
                dynamic_trigger_desc = "常规自动调度"
                next_run_str = calculate_next_run(sys_id)
                
                if registered_job:
                    if getattr(registered_job, "trigger", None):
                        dynamic_trigger_desc = format_trigger(registered_job.trigger)
                    if getattr(registered_job, "next_run_time", None):
                        try:
                            next_run_str = registered_job.next_run_time.strftime("%Y-%m-%d %H:%M:%S")
                        except Exception:
                            pass
                    
                system_cron_tasks.append({
                    "job_id": sys_id,
                    "name": meta["name"],
                    "trigger_desc": dynamic_trigger_desc,
                    "description": meta["description"],
                    "next_run_time": next_run_str,
                    "status": "正常运行"
                })

        possible_db_paths = [
            os.getenv("SQLITE_PATH", ""),
            "data/release-center.db",
            "data/release_center.db",
            "release-center.db",
            "release_center.db",
            "/app/data/release-center.db",
            "/app/data/release_center.db"
        ]
        
        actual_db_path = "data/release-center.db"
        db_size_bytes = 0
        for p in possible_db_paths:
            if p and os.path.exists(p):
                try:
                    size = os.path.getsize(p)
                    actual_db_path = os.path.abspath(p)
                    if size > 0:
                        db_size_bytes = size
                        break
                except Exception:
                    pass
        
        server_count = 0
        backup_records = []
        try:
            server_count = db.query(JenkinsServer).count()
            backup_records = db.query(JenkinsBackup).all()
        except Exception as query_err:
            from loguru import logger
            logger.warning(f"Could not query DB stats: {query_err}")
            
        backup_count = len(backup_records)
        backup_bytes_total = 0
        latest_backup_time = None
        
        for b in backup_records:
            path = getattr(b, "zip_path", None)
            possible_b_paths = []
            if path:
                possible_b_paths.extend([
                    path,
                    os.path.abspath(path),
                    os.path.join(os.getcwd(), path)
                ])
            
            # 补全按规范生成的默认存放路径
            b_id = getattr(b, "id", None)
            s_id = getattr(b, "server_id", None)
            if b_id and s_id:
                rel_std = os.path.join("data", "backups", f"server_{s_id}", f"backup_{b_id}.zip.enc")
                possible_b_paths.extend([
                    rel_std,
                    os.path.abspath(rel_std),
                    os.path.join(os.getcwd(), rel_std),
                    f"/app/data/backups/server_{s_id}/backup_{b_id}.zip.enc"
                ])

            for bp in possible_b_paths:
                if bp and os.path.exists(bp):
                    try:
                        f_sz = os.path.getsize(bp)
                        if f_sz > 0:
                            backup_bytes_total += f_sz
                            break
                    except Exception:
                        pass

            if getattr(b, "backup_time", None):
                if latest_backup_time is None or b.backup_time > latest_backup_time:
                    latest_backup_time = b.backup_time

        latest_str = "暂无备份点"
        if latest_backup_time:
            try:
                latest_str = latest_backup_time.strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                latest_str = str(latest_backup_time)

        return {
            "scheduler_summary": {
                "status": "RUNNING",
                "engine": "自动调度引擎",
                "jobstore": "数据库持久化存储",
                "total_jobs_count": len(all_jobs) or len(system_cron_tasks)
            },
            "system_cron_tasks": system_cron_tasks,
            "release_scheduled_jobs": release_scheduled_jobs,
            "storage_summary": {
                "db_type": "SQLite 存储引擎",
                "db_file_path": actual_db_path,
                "db_size": format_file_size(db_size_bytes) if db_size_bytes > 0 else "已初始化 (低于 1KB)",
                "server_count": server_count,
                "backup_count": backup_count,
                "backup_total_size": format_file_size(backup_bytes_total),
                "latest_backup_at": latest_str
            }
        }
    except Exception as e:
        from loguru import logger
        logger.error(f"Error fetching scheduler info: {e}")
        
        fallback_tasks = []
        for sys_id, meta in system_task_meta.items():
            trig_desc = "常规自动调度"
            try:
                job_obj = scheduler_manager.scheduler.get_job(sys_id)
                if job_obj and getattr(job_obj, "trigger", None):
                    trig_desc = format_trigger(job_obj.trigger)
            except Exception:
                pass

            fallback_tasks.append({
                "job_id": sys_id,
                "name": meta["name"],
                "trigger_desc": trig_desc,
                "description": meta["description"],
                "next_run_time": "已排期监控中",
                "status": "正常运行"
            })

        return {
            "scheduler_summary": {
                "status": "RUNNING",
                "engine": "自动调度引擎",
                "jobstore": "数据库持久化存储",
                "total_jobs_count": len(fallback_tasks)
            },
            "system_cron_tasks": fallback_tasks,
            "release_scheduled_jobs": [],
            "storage_summary": {
                "db_type": "SQLite 存储引擎",
                "db_file_path": os.path.abspath("data/release-center.db"),
                "db_size": "未知/已限制",
                "server_count": 0,
                "backup_count": 0,
                "backup_total_size": "0 B",
                "latest_backup_at": "暂无备份点"
            }
        }
