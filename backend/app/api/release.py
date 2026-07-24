from fastapi import APIRouter, Depends, HTTPException, Request, BackgroundTasks
from sqlalchemy.orm import Session, selectinload
from sqlalchemy.future import select
from sqlalchemy import exists, update
from typing import List
from datetime import datetime, timedelta
from copy import deepcopy

from app.core.database import get_db
from app.api.deps import get_current_user, get_current_active_operator, log_action
from app.models.release import ReleasePlan, ReleaseTask
from app.models.jenkins import JenkinsJob
from app.models.user import User
from app.schemas.release import ReleasePlanCreate, ReleasePlanResponse, ReleasePlanUpdate
from app.services.scheduler import scheduler_manager
from app.services.release_preflight import preflight_block_reason, run_release_preflight
from app.services.release_service import execute_release_task

from app.services.deps_helper import get_client_ip, normalize_idempotency_key

router = APIRouter()


def _snapshot_release_plan(plan: ReleasePlan) -> tuple[dict, list[dict]]:
    plan_data = {
        column.name: deepcopy(getattr(plan, column.name))
        for column in ReleasePlan.__table__.columns
    }
    task_data = [
        {
            column.name: deepcopy(getattr(task, column.name))
            for column in ReleaseTask.__table__.columns
        }
        for task in plan.tasks
    ]
    return plan_data, task_data


def _restore_release_plan(db: Session, plan_data: dict, task_data: list[dict]) -> ReleasePlan:
    plan = db.execute(
        select(ReleasePlan).filter(ReleasePlan.id == plan_data["id"]).options(
            selectinload(ReleasePlan.tasks)
        )
    ).scalars().first()
    for task in list(plan.tasks):
        db.delete(task)
    db.flush()
    for name, value in plan_data.items():
        setattr(plan, name, value)
    for values in sorted(task_data, key=lambda item: item["id"]):
        db.add(ReleaseTask(**values))
    db.commit()
    return db.execute(
        select(ReleasePlan).filter(ReleasePlan.id == plan.id).options(
            selectinload(ReleasePlan.tasks).selectinload(ReleaseTask.job)
        )
    ).scalars().first()


def _register_release_jobs(plan: ReleasePlan) -> None:
    if plan.type == "IMMEDIATE":
        return
    for task in plan.tasks:
        if plan.type == "PIPELINE" and task.sequence != 0:
            continue
        scheduler_manager.add_release_job(
            plan.id, task.id, task.scheduled_time, execute_release_task, plan.id, task.id
        )


def _claim_schedule_recovery(db: Session, plan_id: int, revision: int) -> int | None:
    claimed_revision = revision + 1
    claimed = db.execute(
        update(ReleasePlan)
        .where(
            ReleasePlan.id == plan_id,
            ReleasePlan.status == "WAITING",
            ReleasePlan.preflight_revision == revision,
            ~exists().where(
                ReleaseTask.plan_id == plan_id,
                ReleaseTask.status != "WAITING",
            ),
        )
        .values(
            status="FAILED",
            preflight_status="UNCHECKED",
            preflight_revision=claimed_revision,
        )
        .execution_options(synchronize_session=False)
    )
    return claimed_revision if claimed.rowcount == 1 else None


def _run_preflight_safely(
    db: Session, plan: ReleasePlan, revision: int | None = None
) -> ReleasePlan:
    expected_revision = plan.preflight_revision if revision is None else revision
    try:
        return run_release_preflight(db, plan, expected_revision)
    except Exception:
        db.rollback()
        db.expire_all()
        current = db.execute(
            select(ReleasePlan).filter(ReleasePlan.id == plan.id).options(
                selectinload(ReleasePlan.tasks).selectinload(ReleaseTask.job)
            )
        ).scalars().first()
        if not current:
            return current
        task_results = [
            {
                "task_id": task.id,
                "job_name": task.job_name,
                "status": "FAILED",
                "checks": [{
                    "code": "preflight",
                    "status": "FAILED",
                    "message": "发布前检查异常",
                }],
            }
            for task in current.tasks
        ]
        failed = db.execute(
            update(ReleasePlan)
            .where(
                ReleasePlan.id == plan.id,
                ReleasePlan.status == "WAITING",
                ReleasePlan.preflight_revision == expected_revision,
                ~exists().where(
                    ReleaseTask.plan_id == plan.id,
                    ReleaseTask.status != "WAITING",
                ),
            )
            .values(
                preflight_status="FAILED",
                preflight_checked_at=datetime.now(),
                preflight_result={
                    "summary": "发布前检查异常",
                    "tasks": task_results,
                },
            )
            .execution_options(synchronize_session=False)
        )
        if failed.rowcount == 1:
            db.commit()
        else:
            db.rollback()
        db.expire_all()
        return db.execute(
            select(ReleasePlan).filter(ReleasePlan.id == plan.id).options(
                selectinload(ReleasePlan.tasks).selectinload(ReleaseTask.job)
            )
        ).scalars().first()


def _activate_release_jobs(db: Session, plan: ReleasePlan, revision: int) -> ReleasePlan:
    def current_plan():
        db.expire_all()
        return db.execute(
            select(ReleasePlan).filter(ReleasePlan.id == plan.id).options(
                selectinload(ReleasePlan.tasks).selectinload(ReleaseTask.job)
            )
        ).scalars().first()

    current = current_plan()
    if (
        not current
        or current.status != "WAITING"
        or current.preflight_revision != revision
        or current.preflight_status not in {"PASSED", "WARNING", "FAILED"}
        or any(task.status != "WAITING" for task in current.tasks)
    ):
        raise HTTPException(status_code=409, detail="发布计划状态已变化")
    _register_release_jobs(current)
    verified = current_plan()
    if (
        not verified
        or verified.status != "WAITING"
        or verified.preflight_revision != revision
        or verified.preflight_status not in {"PASSED", "WARNING", "FAILED"}
        or any(task.status != "WAITING" for task in verified.tasks)
    ):
        for task in current.tasks:
            scheduler_manager.remove_release_job(current.id, task.id)
        raise HTTPException(status_code=409, detail="发布计划状态已变化")
    return verified


def normalize_and_validate_execute_time(plan_type: str, execute_time):
    if execute_time and execute_time.tzinfo is not None:
        import zoneinfo
        execute_time = execute_time.astimezone(zoneinfo.ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)
    if plan_type in ("SCHEDULED", "BATCH", "PIPELINE"):
        if not execute_time:
            raise HTTPException(status_code=400, detail="排程发布计划必须指定调度时间")
        if execute_time <= datetime.now():
            raise HTTPException(status_code=400, detail="调度时间必须在未来")
    return execute_time


def validate_release_plan_input(db: Session, plan_in: ReleasePlanCreate):
    # 1. 验证计划类型和失败策略
    if plan_in.type not in ("IMMEDIATE", "SCHEDULED", "BATCH", "PIPELINE"):
        raise HTTPException(status_code=400, detail="未知的发布计划类型")
    if plan_in.pipeline_failure_strategy not in ("STOP", "CONTINUE"):
        raise HTTPException(status_code=400, detail="未知的管道失败策略")
        
    # 2. 验证任务数量
    if not plan_in.tasks or len(plan_in.tasks) > 100:
        raise HTTPException(status_code=400, detail="任务数量必须在 1 到 100 之间")
        
    # 3. 验证计划名称长度
    if not plan_in.name or len(plan_in.name) > 150:
        raise HTTPException(status_code=400, detail="计划名称长度不能超过 150 字符")
        
    # 4. 验证 sequence 序列是否是从 0 开始唯一且连续递增
    sorted_tasks = sorted(plan_in.tasks, key=lambda x: x.sequence)
    for idx, t in enumerate(sorted_tasks):
        if t.sequence != idx:
            raise HTTPException(status_code=400, detail="任务 sequence 必须从 0 开始且连续递增")
            
    from app.models.jenkins import JenkinsServer, JenkinsJob
    
    # 5. 校验各任务及参数
    for task_in in plan_in.tasks:
        if not task_in.branch or len(task_in.branch) > 150:
            raise HTTPException(status_code=400, detail="分支名称长度不能超过 150 字符")
            
        # 校验 job_id + server_id 归属
        job = db.query(JenkinsJob).filter(
            JenkinsJob.id == task_in.job_id,
            JenkinsJob.server_id == task_in.server_id
        ).first()
        if not job:
            raise HTTPException(status_code=400, detail="Jenkins 任务不存在或归属关系错误")
            
        # 校验 job_name 是否一致（若客户端提交了且非空）
        if task_in.job_name and task_in.job_name != job.name:
            raise HTTPException(status_code=400, detail=f"提交的任务名称 [{task_in.job_name}] 与系统真实名称 [{job.name}] 不一致")
            
        # 校验 job 实例是否激活
        server = db.get(JenkinsServer, task_in.server_id)
        if not server or not server.is_active:
            raise HTTPException(status_code=400, detail=f"绑定的 Jenkins 实例已被禁用或不存在")
            
        # 路径片段校验
        for part in job.name.split("/"):
            if part in (".", "..") or not part:
                raise HTTPException(status_code=400, detail="非法的 Jenkins 任务名称，禁止包含 '.'、'..' 或空片段")
                
        # PIPELINE is strictly serial: every task after the first depends on its predecessor.
        if plan_in.type == "PIPELINE":
            expected_dependency = None if task_in.sequence == 0 else task_in.sequence - 1
            if task_in.depends_on_sequence != expected_dependency:
                raise HTTPException(status_code=400, detail="流水线任务必须依赖前一个任务")
                    
        # 校验参数数量
        params = task_in.parameters or {}
        if len(params) > 100:
            raise HTTPException(status_code=400, detail="单个任务的参数数量不能超过 100")
            

@router.post("/plans", response_model=ReleasePlanResponse)
def create_plan(
    request: Request,
    plan_in: ReleasePlanCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_operator)
):
    execute_time = normalize_and_validate_execute_time(plan_in.type, plan_in.execute_time)


    # 校验和拦截参数完整性
    validate_release_plan_input(db, plan_in)

    try:
        idem_key = normalize_idempotency_key(request.headers.get("Idempotency-Key"), current_user.id)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))
    if idem_key:
        existing_plan = db.query(ReleasePlan).filter(ReleasePlan.idempotency_key == idem_key).options(
            selectinload(ReleasePlan.tasks).selectinload(ReleaseTask.job)
        ).first()
        if existing_plan:
            log_action(db, current_user, "IDEMPOTENCY_CONFLICT", get_client_ip(request), f"Idempotency conflict for plan creation key {idem_key}")
            return existing_plan

    # 1. Create ReleasePlan
    plan = ReleasePlan(
        name=plan_in.name,
        type=plan_in.type,
        execute_time=execute_time,
        interval_minutes=plan_in.interval_minutes,
        pipeline_failure_strategy=plan_in.pipeline_failure_strategy,
        status="WAITING",
        creator_id=current_user.id,
        idempotency_key=idem_key
    )
    db.add(plan)
    db.flush() # Secure plan.id
    
    # 2. Setup release tasks
    tasks: List[ReleaseTask] = []
    # Temporary sequence index to DB Task ID mapping for linking PIPELINE dependencies
    seq_to_task_map = {}
    
    from app.models.jenkins import JenkinsJob
    for task_in in plan_in.tasks:
        job = db.query(JenkinsJob).filter(JenkinsJob.id == task_in.job_id).first()
        task = ReleaseTask(
            plan_id=plan.id,
            server_id=task_in.server_id,
            job_id=task_in.job_id,
            job_name=job.name, # Use real job_name from database
            branch=task_in.branch,
            parameters=task_in.parameters or {},
            sequence=task_in.sequence,
            status="WAITING"
        )
        db.add(task)
        tasks.append(task)
        
    db.flush() # Secure task IDs
    
    # Map sequences to task IDs
    for t in tasks:
        seq_to_task_map[t.sequence] = t.id
        
    # Link PIPELINE depends_on_task_id
    if plan.type == "PIPELINE":
        for i, task_in in enumerate(plan_in.tasks):
            if task_in.depends_on_sequence is not None:
                dep_seq = task_in.depends_on_sequence
                if dep_seq in seq_to_task_map:
                    tasks[i].depends_on_task_id = seq_to_task_map[dep_seq]
                    
    from sqlalchemy.exc import IntegrityError
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        if idem_key:
            existing_plan = db.query(ReleasePlan).filter(ReleasePlan.idempotency_key == idem_key).options(
                selectinload(ReleasePlan.tasks).selectinload(ReleaseTask.job)
            ).first()
            if existing_plan:
                log_action(db, current_user, "IDEMPOTENCY_CONFLICT", get_client_ip(request), f"Idempotency conflict for plan creation key {idem_key} on commit")
                return existing_plan
        raise HTTPException(status_code=409, detail="并发请求冲突")
    
    # Refresh to load relationships
    stmt = select(ReleasePlan).filter(ReleasePlan.id == plan.id).options(
        selectinload(ReleasePlan.tasks).selectinload(ReleaseTask.job)
    )
    res = db.execute(stmt)
    plan = res.scalars().first()
    
    # 3. Schedule or execution trigger
    if plan.type == "IMMEDIATE":
        revision = plan.preflight_revision
        plan = _run_preflight_safely(db, plan, revision)

        if plan.preflight_status == "PASSED":
            claimed = db.execute(
                update(ReleasePlan)
                .where(
                    ReleasePlan.id == plan.id,
                    ReleasePlan.type == "IMMEDIATE",
                    ReleasePlan.status == "WAITING",
                    ReleasePlan.preflight_status == "PASSED",
                    ReleasePlan.preflight_revision == revision,
                )
                .values(status="RUNNING")
                .execution_options(synchronize_session=False)
            )
            db.commit()
            if claimed.rowcount != 1:
                db.expire_all()
                return db.get(ReleasePlan, plan.id)
            db.refresh(plan)

            if plan_in.type == "PIPELINE":
                # For pipeline, start only the sequence 0 task
                first_task = next((t for t in plan.tasks if t.sequence == 0), None)
                if first_task:
                    background_tasks.add_task(execute_release_task, plan.id, first_task.id)
            else:
                # For batch or normal, start everything immediately
                for t in plan.tasks:
                    background_tasks.add_task(execute_release_task, plan.id, t.id)
    else: # SCHEDULED, BATCH, PIPELINE (Scheduled)
        # Persist every calculated time together before touching the external scheduler.
        for t in plan.tasks:
            task_time = plan.execute_time
            if plan.type == "BATCH" and plan.interval_minutes > 0:
                task_time = plan.execute_time + timedelta(minutes=plan.interval_minutes * t.sequence)
            t.scheduled_time = task_time
        db.commit()

        revision = plan.preflight_revision
        plan = _run_preflight_safely(db, plan, revision)
        try:
            plan = _activate_release_jobs(db, plan, revision)
        except HTTPException:
            raise
        except Exception as error:
            for t in plan.tasks:
                scheduler_manager.remove_release_job(plan.id, t.id)
            db.rollback()
            if _claim_schedule_recovery(db, plan.id, revision) is None:
                db.rollback()
                raise HTTPException(status_code=409, detail="发布计划状态已变化")
            db.expire_all()
            current = db.get(ReleasePlan, plan.id)
            if current:
                db.delete(current)
            db.commit()
            raise HTTPException(status_code=503, detail=f"注册发布排程失败: {error}")
    log_action(db, current_user, "CREATE_RELEASE_PLAN", get_client_ip(request), f"Created release plan: {plan.name}")
    return plan

@router.get("/plans", response_model=List[ReleasePlanResponse])
def list_plans(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    stmt = select(ReleasePlan).options(
        selectinload(ReleasePlan.tasks).selectinload(ReleaseTask.job)
    ).order_by(ReleasePlan.created_at.desc())
    res = db.execute(stmt)
    return res.scalars().all()

@router.get("/plans/{plan_id}", response_model=ReleasePlanResponse)
def get_plan(
    plan_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    stmt = select(ReleasePlan).filter(ReleasePlan.id == plan_id).options(
        selectinload(ReleasePlan.tasks).selectinload(ReleaseTask.job)
    )
    res = db.execute(stmt)
    plan = res.scalars().first()
    if not plan:
        raise HTTPException(status_code=404, detail="未找到该发布计划")
    return plan

@router.post("/plans/{plan_id}/preflight", response_model=ReleasePlanResponse)
def preflight_plan(
    plan_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_operator),
):
    plan = db.execute(
        select(ReleasePlan).filter(ReleasePlan.id == plan_id).options(
            selectinload(ReleasePlan.tasks).selectinload(ReleaseTask.job)
        )
    ).scalars().first()
    if not plan:
        raise HTTPException(status_code=404, detail="发布计划不存在")
    if plan.status != "WAITING" or any(task.status != "WAITING" for task in plan.tasks):
        raise HTTPException(status_code=409, detail="发布计划状态已变化")
    return _run_preflight_safely(db, plan, plan.preflight_revision)


@router.post("/plans/{plan_id}/cancel")
def cancel_plan(
    request: Request,
    plan_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_operator)
):
    stmt = select(ReleasePlan).filter(ReleasePlan.id == plan_id).options(
        selectinload(ReleasePlan.tasks).selectinload(ReleaseTask.job)
    )
    res = db.execute(stmt)
    plan = res.scalars().first()
    if not plan:
        raise HTTPException(status_code=404, detail="未找到该发布计划")
        
    if plan.status in ["SUCCESS", "FAILED", "CANCELLED"]:
        raise HTTPException(status_code=400, detail="无法停止已经结束的发布计划")
        
    from app.models.jenkins import JenkinsServer
    from app.services.jenkins_client import JenkinsClient

    # Stop remote builds before reporting local cancellation success.
    for t in plan.tasks:
        if t.status == "RUNNING" and t.build_number:
            server = db.get(JenkinsServer, t.server_id)
            if not server:
                raise HTTPException(status_code=409, detail="关联的 Jenkins 实例不存在，无法停止远端构建")
            try:
                JenkinsClient(server.url, server.username, server.api_token).stop_build(
                    t.job_name,
                    t.build_number,
                )
            except Exception as error:
                raise HTTPException(status_code=502, detail=f"停止 Jenkins 构建失败: {error}")

    # Remove from APScheduler
    for t in plan.tasks:
        scheduler_manager.remove_release_job(plan.id, t.id)
        if t.status in ["WAITING", "RUNNING"]:
            t.status = "CANCELLED"
            t.finished_at = datetime.now()
            
    plan.status = "CANCELLED"
    db.commit()
    log_action(db, current_user, "CANCEL_RELEASE_PLAN", get_client_ip(request), f"Cancelled release plan: {plan.name}")
    return {"success": True, "message": "发布计划已成功停止"}

@router.post("/plans/{plan_id}/trigger")
def trigger_plan_immediately(
    request: Request,
    plan_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_operator)
):
    stmt = select(ReleasePlan).filter(ReleasePlan.id == plan_id).options(
        selectinload(ReleasePlan.tasks).selectinload(ReleaseTask.job)
    )
    res = db.execute(stmt)
    plan = res.scalars().first()
    if not plan:
        raise HTTPException(status_code=404, detail="未找到该发布计划")
        
    if plan.status != "WAITING":
        raise HTTPException(status_code=400, detail="只有等待执行状态的计划才可以被提早运行")
        
    # 校验任务绑定的 Jenkins 实例是否被禁用
    reason = preflight_block_reason(plan)
    if reason:
        raise HTTPException(status_code=400, detail=reason)

    from app.models.jenkins import JenkinsServer
    for t in plan.tasks:
        server = db.get(JenkinsServer, t.server_id)
        if server and not server.is_active:
            raise HTTPException(status_code=400, detail=f"无法运行发布计划，任务绑定的 Jenkins 实例 [{server.name}] 已被禁用")

    stmt_up = (
        update(ReleasePlan)
        .where(
            ReleasePlan.id == plan_id,
            ReleasePlan.status == "WAITING",
            ReleasePlan.preflight_status.in_(("PASSED", "WARNING")),
            ReleasePlan.preflight_revision == plan.preflight_revision,
        )
        .values(status="RUNNING")
    )
    res_up = db.execute(stmt_up)
    if res_up.rowcount != 1:
        db.rollback()
        db.refresh(plan)
        raise HTTPException(status_code=409, detail="发布计划已经被其他线程领取运行")
    db.commit()
    db.refresh(plan)

    # Remove all scheduling entries
    for t in plan.tasks:
        scheduler_manager.remove_release_job(plan.id, t.id)

    # Launch execution asynchronously
    if plan.type == "PIPELINE":
        first_task = next((t for t in plan.tasks if t.sequence == 0), None)
        if first_task:
            background_tasks.add_task(execute_release_task, plan.id, first_task.id)
    else:
        for t in plan.tasks:
            background_tasks.add_task(execute_release_task, plan.id, t.id)
            
    log_action(db, current_user, "TRIGGER_RELEASE_PLAN", get_client_ip(request), f"Triggered release plan early: {plan.name}")
    return {"success": True, "message": "发布计划已成功提早运行"}

@router.delete("/plans/{plan_id}")
def delete_plan(
    request: Request,
    plan_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_operator)
):
    plan = db.get(ReleasePlan, plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="未找到该发布计划")
        
    if plan.status == "RUNNING":
        raise HTTPException(status_code=400, detail="不能删除正在运行中的发布计划")
        
    # Cascade check Tasks
    stmt = select(ReleaseTask).filter(ReleaseTask.plan_id == plan_id)
    res = db.execute(stmt)
    tasks = res.scalars().all()
    for t in tasks:
        scheduler_manager.remove_release_job(plan_id, t.id)
        
    db.delete(plan)
    db.commit()
    log_action(db, current_user, "DELETE_RELEASE_PLAN", get_client_ip(request), f"Deleted release plan ID: {plan_id}")
    return {"success": True, "message": "发布计划已成功删除"}

@router.put("/plans/{plan_id}", response_model=ReleasePlanResponse)
def update_plan(
    request: Request,
    plan_id: int,
    plan_in: ReleasePlanCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_operator)
):
    execute_time = normalize_and_validate_execute_time(plan_in.type, plan_in.execute_time)

    # 1. Fetch existing plan with tasks
    stmt = select(ReleasePlan).filter(ReleasePlan.id == plan_id).options(
        selectinload(ReleasePlan.tasks).selectinload(ReleaseTask.job)
    )
    res = db.execute(stmt)
    plan = res.scalars().first()
    if not plan:
        raise HTTPException(status_code=404, detail="未找到该发布计划")
        
    # 校验和拦截参数完整性
    validate_release_plan_input(db, plan_in)
            
    # 2. Status constraint: only edit WAITING plans
    if plan.status != "WAITING":
        raise HTTPException(status_code=400, detail="只有等待执行状态的计划才可以被修改")

    old_plan_data, old_task_data = _snapshot_release_plan(plan)
    old_revision = plan.preflight_revision
    task_ids = [task.id for task in plan.tasks]
    claimed_tasks = db.execute(
        update(ReleaseTask)
        .where(
            ReleaseTask.plan_id == plan_id,
            ReleaseTask.id.in_(task_ids),
            ReleaseTask.status == "WAITING",
        )
        .values(status="EDITING")
        .execution_options(synchronize_session=False)
    )
    if claimed_tasks.rowcount != len(task_ids):
        db.rollback()
        raise HTTPException(status_code=409, detail="发布计划状态已变化")
    claimed_plan = db.execute(
        update(ReleasePlan)
        .where(
            ReleasePlan.id == plan_id,
            ReleasePlan.status == "WAITING",
            ReleasePlan.preflight_revision == old_revision,
        )
        .values(
            preflight_status="UNCHECKED",
            preflight_revision=old_revision + 1,
        )
        .execution_options(synchronize_session=False)
    )
    if claimed_plan.rowcount != 1:
        db.rollback()
        raise HTTPException(status_code=409, detail="发布计划状态已变化")

    # 6. Update ReleasePlan fields
    plan.name = plan_in.name
    plan.type = plan_in.type
    plan.execute_time = execute_time
    plan.interval_minutes = plan_in.interval_minutes
    plan.pipeline_failure_strategy = plan_in.pipeline_failure_strategy
    plan.updated_at = datetime.now()
    
    # 7. Create new tasks
    from app.models.jenkins import JenkinsJob
    new_tasks = []
    for task_in in plan_in.tasks:
        job = db.query(JenkinsJob).filter(JenkinsJob.id == task_in.job_id).first()
        task = ReleaseTask(
            plan_id=plan.id,
            server_id=task_in.server_id,
            job_id=task_in.job_id,
            job_name=job.name, # Use real job_name from database
            branch=task_in.branch,
            parameters=task_in.parameters or {},
            sequence=task_in.sequence,
            status="WAITING"
        )
        db.add(task)
        new_tasks.append(task)
        
    db.flush() # Get IDs
    
    # Map dependencies for PIPELINE
    seq_to_task_map = {t.sequence: t.id for t in new_tasks}
    if plan.type == "PIPELINE":
        for i, task_in in enumerate(plan_in.tasks):
            if task_in.depends_on_sequence is not None:
                dep_seq = task_in.depends_on_sequence
                if dep_seq in seq_to_task_map:
                    actual_task = next((t for t in new_tasks if t.sequence == task_in.sequence), None)
                    if actual_task:
                        actual_task.depends_on_task_id = seq_to_task_map[dep_seq]
        db.flush()

    # Keep the claimed rows until replacements have IDs so SQLite cannot
    # reuse an old task ID while its scheduler entry is still being removed.
    for task in list(plan.tasks):
        db.delete(task)
    db.flush()
        
    for t in new_tasks:
        task_time = plan.execute_time
        if plan.type == "BATCH" and plan.interval_minutes > 0:
            task_time = plan.execute_time + timedelta(minutes=plan.interval_minutes * t.sequence)
        t.scheduled_time = task_time

    db.commit()

    # Old task IDs are harmless after commit and can now be removed without
    # contending with APScheduler's job store on the same SQLite database.
    for task_data in old_task_data:
        scheduler_manager.remove_release_job(plan_id, task_data["id"])

    plan = db.execute(
        select(ReleasePlan).filter(ReleasePlan.id == plan_id).options(
            selectinload(ReleasePlan.tasks).selectinload(ReleaseTask.job)
        )
    ).scalars().first()
    revision = plan.preflight_revision

    # 8. Check first, then expose jobs to APScheduler.
    plan = _run_preflight_safely(db, plan, revision)
    try:
        plan = _activate_release_jobs(db, plan, revision)
    except HTTPException:
        raise
    except Exception as error:
        for t in plan.tasks:
            scheduler_manager.remove_release_job(plan_id, t.id)
        db.rollback()
        claimed_revision = _claim_schedule_recovery(db, plan_id, revision)
        if claimed_revision is None:
            db.rollback()
            raise HTTPException(status_code=409, detail="发布计划状态已变化")
        old_plan_data["preflight_revision"] = claimed_revision
        db.expire_all()
        restored_plan = _restore_release_plan(db, old_plan_data, old_task_data)
        restoration_error = None
        try:
            _register_release_jobs(restored_plan)
        except Exception as restore_error:
            restoration_error = restore_error

        detail = f"更新发布排程失败，已回滚数据库: {error}"
        if restoration_error:
            detail += f"；恢复原排程失败: {restoration_error}"
        raise HTTPException(status_code=503, detail=detail)
    
    # Refetch to return loaded response
    stmt = select(ReleasePlan).filter(ReleasePlan.id == plan_id).options(
        selectinload(ReleasePlan.tasks).selectinload(ReleaseTask.job)
    )
    res = db.execute(stmt)
    plan = res.scalars().first()

    log_action(db, current_user, "UPDATE_RELEASE_PLAN", get_client_ip(request), f"Updated release plan: {plan.name}")
    return plan

@router.post("/tasks/{task_id}/sync")
def sync_task_status_manually(
    task_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_operator)
):
    from app.services.release_service import reconcile_single_task
    from app.models.jenkins import JenkinsServer
    from app.services.jenkins_client import JenkinsClient

    task = db.query(ReleaseTask).filter(ReleaseTask.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="未找到该发布任务")
    
    if not task.build_number:
        raise HTTPException(status_code=400, detail="该任务在 Jenkins 上尚未被触发，无法同步")

    # Execute reconciliation
    updated = reconcile_single_task(db, task.id)
    if updated:
        return {"success": True, "message": "任务状态已成功同步并更新"}
    
    # If not updated (e.g., still building or already matching status), return status detail
    try:
        server = db.query(JenkinsServer).filter(JenkinsServer.id == task.server_id).first()
        if not server:
            raise HTTPException(status_code=404, detail="未找到关联的 Jenkins 实例")
            
        client = JenkinsClient(server.url, server.username, server.api_token)
        status_info = client.get_build_status(task.job_name, task.build_number)
        
        # Translate Jenkins status to Chinese for frontend display
        jenkins_status = '构建中' if status_info.get('building') else status_info.get('result')
        if jenkins_status == 'SUCCESS':
            jenkins_status = '成功'
        elif jenkins_status == 'FAILURE' or jenkins_status == 'FAILED':
            jenkins_status = '失败'
        elif jenkins_status == 'ABORTED':
            jenkins_status = '已中止'
            
        return {
            "success": True,
            "message": f"任务状态已同步，本地状态：{task.status}，Jenkins 状态：{jenkins_status}",
            "building": status_info.get("building", False)
        }
    except Exception as e:
        return {
            "success": True,
            "message": f"本地状态为 {task.status}，同步 Jenkins 失败：{str(e)}",
            "building": task.status == "RUNNING"
        }
