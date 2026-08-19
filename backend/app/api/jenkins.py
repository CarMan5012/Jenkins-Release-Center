from datetime import datetime
import os
import json
import zipfile
import time
from fastapi import APIRouter, Depends, HTTPException, Request, BackgroundTasks
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from sqlalchemy.future import select
from typing import List, Dict, Any, Optional, Tuple

from app.core.database import get_db, SyncSessionLocal
from app.core.security import encrypt_secret
from app.api.deps import get_current_user, get_current_active_admin, get_current_active_operator, log_action
from app.services.deps_helper import is_request_trusted_https, get_client_ip, validate_jenkins_url, normalize_idempotency_key
from app.core.ws_manager import manager
from app.models.jenkins import JenkinsServer, JenkinsView, JenkinsJob, JenkinsBackup
from app.models.release import ReleaseHistory
from app.models.user import User
from app.schemas.jenkins import (
    JenkinsServerCreate, JenkinsServerResponse, JenkinsServerUpdate,
    JenkinsViewResponse, JenkinsJobResponse, JenkinsBackupResponse
)
from app.services.jenkins_client import JenkinsClient
from app.services.jenkins_sync_task import sync_external_builds
from app.services.scheduler import scheduler_manager

router = APIRouter()

def decrypt_backup_to_memory(enc_filepath: str) -> bytes:
    import hashlib
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from app.core.config import settings

    if not os.path.exists(enc_filepath):
        raise HTTPException(status_code=404, detail="备份文件不存在")

    key_material = settings.BACKUP_ENCRYPTION_KEY
    if not key_material:
        raise HTTPException(status_code=500, detail="备份密钥未配置")

    aes_key = hashlib.sha256(key_material.strip().encode()).digest()

    with open(enc_filepath, "rb") as f:
        file_content = f.read()

    if len(file_content) < 5 + 12 + 16 or file_content[:5] != b"JRCB1":
        raise HTTPException(status_code=400, detail="备份密钥错误或文件已损坏")

    nonce = file_content[5:17]
    ciphertext = file_content[17:]

    try:
        aesgcm = AESGCM(aes_key)
        decrypted_data = aesgcm.decrypt(nonce, ciphertext, None)
        return decrypted_data
    except Exception:
        raise HTTPException(status_code=400, detail="备份密钥错误或文件已损坏")

def read_backup_details(zip_path):
    with zipfile.ZipFile(zip_path) as archive:
        try:
            details = json.loads(archive.read("details.json"))
        except KeyError:
            return {"available": False, "version": 0, "views": [], "jobs": []}

    if not isinstance(details, dict) or not isinstance(details.get("jobs"), list):
        raise ValueError("Invalid Jenkins backup details format.")

    return {
        "available": True,
        "version": details.get("version", 1),
        "views": details.get("views", []),
        "jobs": details["jobs"],
    }

@router.post("/servers", response_model=JenkinsServerResponse)
def create_server(
    request: Request,
    server: JenkinsServerCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_operator)
):
    # Check if duplicate name
    existing = db.query(JenkinsServer).filter(JenkinsServer.name == server.name).first()
    if existing:
        raise HTTPException(status_code=400, detail="Jenkins 实例名称已存在")

    # Validate URL
    from app.core.config import settings
    try:
        validate_jenkins_url(server.url, settings.APP_ENV != "development", settings.JENKINS_ALLOWED_ORIGINS)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    encrypted_token = encrypt_secret(server.api_token)
    encrypted_username = encrypt_secret(server.username)
    db_server = JenkinsServer(
        name=server.name,
        url=server.url,
        username=encrypted_username,
        api_token=encrypted_token,
        description=server.description,
        is_active=server.is_active
    )
    db.add(db_server)
    db.commit()
    db.refresh(db_server)
    log_action(db, current_user, "CREATE_JENKINS_SERVER", get_client_ip(request), f"添加 Jenkins 实例: {server.name}")

    # 自动触发后台同步任务（仅在启用状态下）
    if db_server.is_active:
        syncing_servers.add(db_server.id)
        def run_sync_and_cleanup(sid: int):
            try:
                sync_jenkins_data(sid)
            except Exception as e:
                import loguru
                loguru.logger.error(f"Background sync failed for newly created server {sid}: {str(e)}")
            finally:
                syncing_servers.discard(sid)

        background_tasks.add_task(run_sync_and_cleanup, db_server.id)

    return db_server

@router.get("/servers", response_model=List[JenkinsServerResponse])
def list_servers(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    return db.query(JenkinsServer).all()

@router.get("/servers/{server_id}", response_model=JenkinsServerResponse)
def get_server(
    server_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    server = db.get(JenkinsServer, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="未找到该 Jenkins 实例")
    return server

@router.put("/servers/{server_id}", response_model=JenkinsServerResponse)
def update_server(
    request: Request,
    server_id: int,
    server_in: JenkinsServerUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_operator)
):
    server = db.get(JenkinsServer, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="未找到该 Jenkins 实例")

    update_data = server_in.model_dump(exclude_unset=True)

    # Validate URL and Token re-submission if Origin changes
    from app.core.config import settings
    from urllib.parse import urlparse

    if "url" in update_data:
        try:
            validate_jenkins_url(update_data["url"], settings.APP_ENV != "development", settings.JENKINS_ALLOWED_ORIGINS)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

        old_parsed = urlparse(server.url)
        new_parsed = urlparse(update_data["url"])
        old_origin = f"{old_parsed.scheme}://{old_parsed.netloc}"
        new_origin = f"{new_parsed.scheme}://{new_parsed.netloc}"
        if new_origin != old_origin:
            if "api_token" not in update_data or not update_data["api_token"] or update_data["api_token"] in ["********", "••••••••"]:
                raise HTTPException(status_code=400, detail="修改 Jenkins origin 时必须重新提交 Token")

    if "username" in update_data:
        if update_data["username"] and not update_data["username"].startswith("enc:"):
            update_data["username"] = encrypt_secret(update_data["username"])
        elif update_data["username"] and update_data["username"].startswith("enc:"):
            del update_data["username"]

    if "api_token" in update_data:
        if not update_data["api_token"] or update_data["api_token"] in ["********", "••••••••"]:
            del update_data["api_token"]
        else:
            update_data["api_token"] = encrypt_secret(update_data["api_token"])

    for field, value in update_data.items():
        setattr(server, field, value)

    db.commit()
    db.refresh(server)
    log_action(db, current_user, "UPDATE_JENKINS_SERVER", get_client_ip(request), f"更新 Jenkins 实例: {server.name}")
    return server

@router.delete("/servers/{server_id}")
def delete_server(
    request: Request,
    server_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_operator)
):
    server = db.get(JenkinsServer, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="未找到该 Jenkins 实例")

    from app.models.release import ReleasePlan, ReleaseTask, ReleaseHistory

    # 检查是否有正在运行中的发布计划
    running_plan = (
        db.query(ReleasePlan)
        .join(ReleaseTask, ReleaseTask.plan_id == ReleasePlan.id)
        .filter(ReleaseTask.server_id == server_id, ReleasePlan.status == "RUNNING")
        .first()
    )
    if running_plan:
        raise HTTPException(status_code=400, detail=f"该实例关联了正在运行中的发布计划 [{running_plan.name}]，禁止删除")

    # 查找所有与该实例相关的任务及计划 ID
    tasks = db.query(ReleaseTask).filter(ReleaseTask.server_id == server_id).all()
    plan_ids = list({t.plan_id for t in tasks})

    # 清理所有待删除计划在调度器中的任务
    for t in tasks:
        scheduler_manager.remove_release_job(t.plan_id, t.id)

    try:
        if plan_ids:
            # 删除相关的历史记录
            db.query(ReleaseHistory).filter(ReleaseHistory.plan_id.in_(plan_ids)).delete(synchronize_session=False)
            # 删除相关的任务
            db.query(ReleaseTask).filter(ReleaseTask.plan_id.in_(plan_ids)).delete(synchronize_session=False)
            # 删除相关的计划本身
            db.query(ReleasePlan).filter(ReleasePlan.id.in_(plan_ids)).delete(synchronize_session=False)

        # 删除任何其他仅仅关联该 server_id 的孤立历史记录（虽然通常有 plan_id）
        db.query(ReleaseHistory).filter(ReleaseHistory.server_id == server_id).delete(synchronize_session=False)

        db.delete(server)
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"强制删除失败：{str(e)}")

    log_action(db, current_user, "DELETE_JENKINS_SERVER", get_client_ip(request), f"删除 Jenkins 实例: {server.name} 及其关联依赖数据")
    return {"success": True, "message": "Jenkins 实例及相关的所有发布数据已强制删除"}

@router.post("/servers/{server_id}/test")
def test_server_connection(
    server_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_operator)
):
    server = db.get(JenkinsServer, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="未找到该 Jenkins 实例")
    if not server.is_active:
        raise HTTPException(status_code=400, detail="该 Jenkins 实例已被禁用，无法测试连接")

    client = JenkinsClient(server.url, server.username, server.api_token)
    success, message = client.test_connection()
    return {"success": success, "message": message}

# View and Job Synchronization
def sync_jenkins_data(server_id: int):
    """
    Synchronous worker running in a background thread to safely request data from Jenkins API
    and persist views/jobs to the database.
    """
    db = SyncSessionLocal()
    try:
        server = db.query(JenkinsServer).filter(JenkinsServer.id == server_id).first()
        if not server:
            return

        client = JenkinsClient(server.url, server.username, server.api_token)

        # 1. Fetch Views
        views = client.get_views()
        view_names = []
        for view_data in views:
            view_name = view_data["name"]
            view_names.append(view_name)

            # Upsert View
            db_view = db.query(JenkinsView).filter(
                JenkinsView.server_id == server_id,
                JenkinsView.name == view_name
            ).first()

            if not db_view:
                db_view = JenkinsView(
                    server_id=server_id,
                    name=view_name,
                    url=view_data["url"]
                )
                db.add(db_view)
            else:
                db_view.url = view_data["url"]
            db.commit()

        # Optional: Remove Views in DB that no longer exist in Jenkins
        db.query(JenkinsView).filter(
            JenkinsView.server_id == server_id,
            ~JenkinsView.name.in_(view_names)
        ).delete(synchronize_session=False)
        db.commit()

        # 2. Fetch and Sync Jobs under each View
        db_views = db.query(JenkinsView).filter(JenkinsView.server_id == server_id).all()
        synced_job_names = []

        for view in db_views:
            jobs = client.get_jobs_in_view(view.name)
            for job_data in jobs:
                job_name = job_data["name"]
                synced_job_names.append(job_name)

                # Retrieve last build metadata if present
                last_build = job_data.get("lastBuild")
                last_num = last_build.get("number") if last_build else None
                last_result = last_build.get("result") if last_build else None
                last_time = None
                if last_build and last_build.get("timestamp"):
                    last_time = datetime.fromtimestamp(last_build.get("timestamp") / 1000.0)

                # Upsert Job
                db_job = db.query(JenkinsJob).filter(
                    JenkinsJob.server_id == server_id,
                    JenkinsJob.name == job_name
                ).first()

                if not db_job:
                    db_job = JenkinsJob(
                        server_id=server_id,
                        view_id=view.id,
                        name=job_name,
                        description=job_data.get("description"),
                        last_build_number=last_num,
                        last_build_result=last_result,
                        last_build_time=last_time
                    )
                    db.add(db_job)
                else:
                    if view.name.lower() != "all" or not db_job.view_id:
                        db_job.view_id = view.id
                    db_job.description = job_data.get("description")
                    db_job.last_build_number = last_num
                    db_job.last_build_result = last_result
                    db_job.last_build_time = last_time
                db.commit()

        # Remove deleted jobs
        db.query(JenkinsJob).filter(
            JenkinsJob.server_id == server_id,
            ~JenkinsJob.name.in_(synced_job_names)
        ).delete(synchronize_session=False)
        db.commit()

        # Update server last_synced_at upon complete success
        server.last_synced_at = datetime.now()
        db.commit()

    except Exception as e:
        import loguru
        loguru.logger.error(f"Sync error for server {server_id}: {str(e)}")
        raise e
    finally:
        db.close()

# In-memory tracking of currently running Jenkins sync tasks to prevent concurrent duplicate requests
syncing_servers = set()

@router.post("/servers/{server_id}/sync")
def trigger_sync(
    request: Request,
    server_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_operator)
):
    server = db.get(JenkinsServer, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="未找到该 Jenkins 实例")
    if not server.is_active:
        raise HTTPException(status_code=400, detail="该 Jenkins 实例已被禁用，无法执行数据同步")

    if server_id in syncing_servers:
        raise HTTPException(status_code=409, detail="该 Jenkins 实例已在数据同步中，请勿重复提交")

    syncing_servers.add(server_id)

    def run_sync_and_cleanup(sid: int):
        try:
            sync_jenkins_data(sid)
        except Exception as e:
            import loguru
            loguru.logger.error(f"Background sync failed for server {sid}: {str(e)}")
        finally:
            syncing_servers.discard(sid)
            manager.broadcast_event("SYNC_UPDATE", {"server_id": sid, "syncing": False})

    background_tasks.add_task(run_sync_and_cleanup, server_id)
    log_action(db, current_user, "SYNC_JENKINS", get_client_ip(request), f"Triggered background Views/Jobs sync for server {server.name}")
    return {"success": True, "message": "Jenkins 数据同步任务已在后台启动"}

@router.get("/servers/{server_id}/sync/status")
def get_sync_status(
    server_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    server = db.get(JenkinsServer, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="未找到该 Jenkins 实例")
    return {"syncing": server_id in syncing_servers}

@router.get("/servers/{server_id}/views", response_model=List[JenkinsViewResponse])
def get_server_views(
    server_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    return db.query(JenkinsView).filter(JenkinsView.server_id == server_id).all()

@router.get("/servers/{server_id}/views/{view_id}/jobs", response_model=List[JenkinsJobResponse])
def get_view_jobs(
    server_id: int,
    view_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    view = db.get(JenkinsView, view_id)
    if view and view.name.lower() == "all":
        return db.query(JenkinsJob).filter(JenkinsJob.server_id == server_id).all()

    return db.query(JenkinsJob).filter(
        JenkinsJob.server_id == server_id,
        JenkinsJob.view_id == view_id
    ).all()

@router.get("/servers/{server_id}/jobs", response_model=List[JenkinsJobResponse])
def get_all_jobs(
    server_id: int,
    query: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    stmt = db.query(JenkinsJob).filter(JenkinsJob.server_id == server_id)
    if query:
        stmt = stmt.filter(JenkinsJob.name.like(f"%{query}%"))
    return stmt.all()

_BRANCH_CACHE: Dict[Tuple[int, int], Tuple[float, List[str]]] = {}
_BRANCH_CACHE_TTL = 120  # 120 seconds cache

@router.get("/servers/{server_id}/jobs/{job_id}/branches", response_model=List[str])
def get_git_branches(
    server_id: int,
    job_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    server = db.get(JenkinsServer, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="未找到该 Jenkins 实例或对应的 Job")
    if not server.is_active:
        raise HTTPException(status_code=400, detail="该 Jenkins 实例已被禁用，无法获取分支信息")

    job = (
        db.query(JenkinsJob)
        .filter(JenkinsJob.id == job_id, JenkinsJob.server_id == server_id)
        .first()
    )
    if not job:
        raise HTTPException(
            status_code=404, detail="Jenkins server or job was not found"
        )

    cache_key = (server_id, job_id)
    now = time.time()
    if cache_key in _BRANCH_CACHE:
        ts, cached_branches = _BRANCH_CACHE[cache_key]
        if now - ts < _BRANCH_CACHE_TTL:
            return cached_branches

    branches: List[str] = []
    try:
        client = JenkinsClient(server.url, server.username, server.api_token)
        branches = client.get_branches_and_tags(job.name)
    except Exception:
        branches = []

    # Merge historical branches from ReleaseHistory if available
    try:
        history_rows = (
            db.query(ReleaseHistory.branch)
            .filter(ReleaseHistory.server_id == server_id)
            .filter(ReleaseHistory.job_name == job.name)
            .filter(ReleaseHistory.branch.isnot(None))
            .distinct()
            .all()
        )
        for row in history_rows:
            b_val = row[0]
            if b_val and isinstance(b_val, str) and b_val.strip():
                clean_b = b_val.strip()
                if clean_b not in branches:
                    branches.append(clean_b)
    except Exception:
        pass

    if not branches:
        branches = ["master", "main", "develop", "release"]

    _BRANCH_CACHE[cache_key] = (now, branches)
    return branches

@router.post("/servers/{server_id}/jobs/{job_id}/run")
def run_job_directly(
    request: Request,
    server_id: int,
    job_id: int,
    payload: Dict[str, Any],
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_operator)
):
    """
    Directly trigger a Jenkins build without creating a Release Plan in the database.
    """
    server = db.get(JenkinsServer, server_id)
    job = db.get(JenkinsJob, job_id)
    if not server or not job or job.server_id != server_id:
        raise HTTPException(status_code=404, detail="未找到该 Jenkins 实例或对应的 Job")
    if not server.is_active:
        raise HTTPException(status_code=400, detail="该 Jenkins 实例已被禁用，无法运行任务")

    branch = payload.get("branch")
    parameters = payload.get("parameters", {})

    client = JenkinsClient(server.url, server.username, server.api_token)
    try:
        queue_url = client.trigger_build(job.name, parameters, branch=branch)
        log_action(db, current_user, "RUN_JENKINS_JOB_DIRECTLY", get_client_ip(request), f"直接触发任务 '{job.name}' 构建 (分支: {branch})，实例: '{server.name}'")

        def delayed_sync():
            import time
            time.sleep(1.2)
            sync_external_builds(server_id=server_id, job_name=job.name)
            try:
                from app.core.ws_manager import manager
                manager.broadcast_event("HISTORY_UPDATE")
            except Exception:
                pass

        background_tasks.add_task(delayed_sync)

        return {
            "success": True,
            "message": f"成功向 Jenkins 触发任务 [{job.name}] 构建",
            "queue_url": queue_url
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"触发 Jenkins 构建失败: {str(e)}")


# ==========================================
# Jenkins Server Jobs Configuration Backups
# ==========================================
@router.post("/servers/{server_id}/backups", response_model=JenkinsBackupResponse)
def create_backup(
    request: Request,
    server_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_operator)
):
    server = db.get(JenkinsServer, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="未找到该 Jenkins 实例")
    if not server.is_active:
        raise HTTPException(status_code=400, detail="该 Jenkins 实例已被禁用，无法创建备份")

    try:
        idem_key = normalize_idempotency_key(request.headers.get("Idempotency-Key"), current_user.id)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))
    if idem_key:
        existing = db.query(JenkinsBackup).filter(JenkinsBackup.idempotency_key == idem_key).first()
        if existing:
            log_action(db, current_user, "IDEMPOTENCY_CONFLICT", get_client_ip(request), f"重复触发备份拦截，Key: {idem_key}")
            return existing

    db_backup = JenkinsBackup(
        server_id=server_id,
        status="BACKUPING",
        job_count=0,
        idempotency_key=idem_key
    )
    db.add(db_backup)

    from sqlalchemy.exc import IntegrityError
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        if idem_key:
            existing = db.query(JenkinsBackup).filter(JenkinsBackup.idempotency_key == idem_key).first()
            if existing:
                log_action(db, current_user, "IDEMPOTENCY_CONFLICT", get_client_ip(request), f"重复触发备份拦截，Key: {idem_key}")
                return existing
        raise HTTPException(status_code=409, detail="并发请求冲突")

    db.refresh(db_backup)

    from app.services.jenkins_backup_service import execute_jenkins_backup
    background_tasks.add_task(execute_jenkins_backup, server_id, db_backup.id)

    log_action(db, current_user, "CREATE_BACKUP", get_client_ip(request), f"创建配置备份任务 #{db_backup.id}（实例: {server_id}）")
    return db_backup

@router.get("/servers/{server_id}/backups", response_model=List[JenkinsBackupResponse])
def list_backups(
    server_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    return db.query(JenkinsBackup).filter(
        JenkinsBackup.server_id == server_id
    ).order_by(JenkinsBackup.backup_time.desc()).all()

@router.get("/servers/{server_id}/backups/{backup_id}/summary")
def get_backup_summary(
    server_id: int,
    backup_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    backup = db.get(JenkinsBackup, backup_id)
    if not backup or backup.server_id != server_id:
        raise HTTPException(status_code=404, detail="未找到对应的备份记录")
    return {"summary_md": backup.summary_md or "暂无汇总报告"}

@router.get("/servers/{server_id}/backups/{backup_id}/details")
def get_backup_details(
    request: Request,
    server_id: int,
    backup_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_admin)
):
    if not is_request_trusted_https(request):
        raise HTTPException(status_code=400, detail="直接 HTTP 访问或非安全连接下禁止在线查看明文凭据")

    backup = db.get(JenkinsBackup, backup_id)
    if not backup or backup.server_id != server_id:
        raise HTTPException(status_code=404, detail="未找到对应的备份记录。")
    if backup.status != "SUCCESS" or not backup.zip_path or not os.path.exists(backup.zip_path):
        raise HTTPException(status_code=400, detail="备份文件缺失或尚未就绪。")

    try:
        decrypted_data = decrypt_backup_to_memory(backup.zip_path)
        import io
        with zipfile.ZipFile(io.BytesIO(decrypted_data)) as archive:
            details = json.loads(archive.read("details.json"))

        log_action(db, current_user, "VIEW_BACKUP_DETAILS", get_client_ip(request), f"查看备份记录详情 #{backup_id}（实例: {server_id}）")
        return details
    except Exception:
        raise HTTPException(status_code=500, detail="备份详情配置无效")

@router.get("/servers/{server_id}/backups/{backup_id}/download")
def download_backup_zip(
    request: Request,
    server_id: int,
    backup_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_admin)
):
    backup = db.get(JenkinsBackup, backup_id)
    if not backup or backup.server_id != server_id:
        raise HTTPException(status_code=404, detail="未找到对应的备份记录。")
    if backup.status != "SUCCESS" or not backup.zip_path or not os.path.exists(backup.zip_path):
        raise HTTPException(status_code=400, detail="备份文件缺失或尚未就绪。")

    # Audit log
    log_action(db, current_user, "DOWNLOAD_BACKUP", get_client_ip(request), f"下载加密备份包 #{backup_id}（实例: {server_id}）")

    filename = f"jenkins_backup_server_{server_id}_{backup.backup_time.strftime('%Y%m%d%H%M%S')}.zip.enc"
    return FileResponse(
        path=backup.zip_path,
        filename=filename,
        media_type="application/octet-stream"
    )

@router.delete("/servers/{server_id}/backups/{backup_id}")
def delete_backup(
    request: Request,
    server_id: int,
    backup_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_operator)
):
    backup = db.get(JenkinsBackup, backup_id)
    if not backup or backup.server_id != server_id:
        raise HTTPException(status_code=404, detail="未找到对应的备份记录")

    if backup.zip_path and os.path.exists(backup.zip_path):
        try:
            os.remove(backup.zip_path)
        except OSError:
            pass

    db.delete(backup)
    db.commit()
    log_action(db, current_user, "DELETE_BACKUP", get_client_ip(request), f"删除配置备份记录 #{backup_id}（实例: {server_id}）")
    return {"message": "备份删除成功"}
