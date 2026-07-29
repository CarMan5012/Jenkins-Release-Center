import time
import threading
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from sqlalchemy import and_, exists, or_, update
from sqlalchemy.orm import Session
from loguru import logger

from app.core.database import SyncSessionLocal
from app.models.release import ReleasePlan, ReleaseTask, ReleaseHistory
from app.models.jenkins import JenkinsServer, JenkinsJob
from app.services.jenkins_client import JenkinsClient, jenkins_datetime, normalize_jenkins_status
from app.services.notification import (
    send_release_notification,
    send_plan_summary_notification,
    send_plan_start_notification
)
from app.services.release_preflight import preflight_block_reason

ACTIVE_TASK_STATUSES = ("QUEUED", "BUILDING", "RUNNING")


def claim_task_final_state(
    db: Session,
    task_id: int,
    status: str,
    duration: int,
    started_at: datetime,
    finished_at: datetime,
) -> bool:
    result = db.execute(
        update(ReleaseTask)
        .where(
            ReleaseTask.id == task_id,
            ReleaseTask.status.in_(ACTIVE_TASK_STATUSES),
        )
        .values(
            status=status,
            duration=duration,
            started_at=started_at,
            finished_at=finished_at,
            error_message=None if status == "SUCCESS" else f"Jenkins result: {status}",
        )
        .execution_options(synchronize_session=False)
    )
    db.commit()
    return result.rowcount == 1


def finalize_task_from_jenkins(
    db: Session,
    task: ReleaseTask,
    status_info: Dict[str, Any],
    client: JenkinsClient,
) -> bool:
    status = normalize_jenkins_status(
        status_info.get("building", False), status_info.get("result")
    )
    if status in ("BUILDING", "UNKNOWN"):
        return False

    raw_duration = status_info.get("duration", 0)
    try:
        duration = int(raw_duration) if raw_duration is not None else 0
    except (TypeError, ValueError):
        duration = 0
    started_at = (
        jenkins_datetime(status_info.get("timestamp"))
        or task.started_at
        or datetime.now()
    )
    finished_at = started_at + timedelta(seconds=duration)
    if not claim_task_final_state(
        db, task.id, status, duration, started_at, finished_at
    ):
        db.refresh(task)
        return False

    db.refresh(task)
    raw_status = status_info.get("result")
    send_release_notification(task.id, "success" if status == "SUCCESS" else "failed")
    write_history(db, task, status, duration, raw_status, client)
    if status == "SUCCESS":
        handle_pipeline_success(db, task.plan_id, task.id)
    else:
        handle_pipeline_failure(db, task.plan_id, task.id)
    return True


def execute_release_task(plan_id: int, task_id: int):
    """
    APScheduler entry point callback. Must be a module-level global function.
    Runs inside background scheduler threads.
    """
    logger.info(f"APScheduler trigger: Plan {plan_id}, Task {task_id}")
    db: Session = SyncSessionLocal()
    try:
        plan = db.query(ReleasePlan).filter(ReleasePlan.id == plan_id).first()
        task = db.query(ReleaseTask).filter(ReleaseTask.id == task_id).first()
        if not plan or not task or task.plan_id != plan_id:
            logger.warning(f"Release task ownership check failed: Plan {plan_id}, Task {task_id}")
            return
        reason = preflight_block_reason(plan)
        if reason:
            db.query(ReleaseTask).filter(
                ReleaseTask.plan_id == plan_id,
                ReleaseTask.status == "WAITING",
            ).update(
                {
                    ReleaseTask.status: "FAILED",
                    ReleaseTask.error_message: reason,
                    ReleaseTask.finished_at: datetime.now(),
                },
                synchronize_session=False,
            )
            if plan.status in ["WAITING", "RUNNING"]:
                plan.status = "FAILED"
            db.commit()
            logger.warning(f"Release task blocked: {reason}")
            return
        if task.status != "WAITING":
            logger.warning(f"Task {task_id} is missing or no longer waiting. Skipping execution.")
            return
    finally:
        db.close()

    # 计划触发启动时进行【开始通知】（内置 plan_start 防重与 start 事件匹配校验）
    send_plan_start_notification(plan_id)

    # Spawn a separate thread to handle long running polls so APScheduler thread pool is not blocked
    t = threading.Thread(target=execute_task_workflow, args=(plan_id, task_id))
    t.start()


def resolve_task_build_number(
    db: Session, task: ReleaseTask, client: JenkinsClient
) -> Optional[int]:
    if task.build_number is not None:
        return task.build_number
    if task.jenkins_queue_id is None:
        return None

    queue_item = client.get_queue_item(task.jenkins_queue_id)
    number = ((queue_item or {}).get("executable") or {}).get("number")
    if number is None:
        number = next(
            (
                build.get("number")
                for build in client.get_recent_builds(task.job_name, 20)
                if build.get("queueId") == task.jenkins_queue_id
            ),
            None,
        )

    if number is None:
        task.error_message = (
            f"Jenkins queue #{task.jenkins_queue_id} unresolved; "
            "no matching build number found."
        )
        db.commit()
        return None

    db.execute(
        update(ReleaseTask)
        .where(
            ReleaseTask.id == task.id,
            ReleaseTask.status.in_(ACTIVE_TASK_STATUSES),
            ReleaseTask.build_number.is_(None),
        )
        .values(build_number=int(number), error_message=None)
        .execution_options(synchronize_session=False)
    )
    db.commit()
    db.refresh(task)
    return task.build_number


def reconcile_single_task(db: Session, task_id: int) -> bool:
    """
    Queries Jenkins to check the current build status of a task and updates db accordingly.
    Supports auto-reconciling build_number mismatch and updating final/running states.
    """
    task = db.query(ReleaseTask).filter(ReleaseTask.id == task_id).first()
    if not task:
        return False

    server = db.query(JenkinsServer).filter(JenkinsServer.id == task.server_id).first()
    if not server:
        logger.error(f"Reconciliation error: Associated Jenkins server for task {task_id} not found.")
        return False
        
    client = JenkinsClient(server.url, server.username, server.api_token)

    if resolve_task_build_number(db, task, client) is None:
        return False
        
    try:
        status_info = client.get_build_status(task.job_name, task.build_number)
        
        if status_info.get("building") is True:
            result = db.execute(
                update(ReleaseTask)
                .where(
                    ReleaseTask.id == task.id,
                    ReleaseTask.status.in_(ACTIVE_TASK_STATUSES),
                )
                .values(status="BUILDING")
                .execution_options(synchronize_session=False)
            )
            db.commit()
            db.refresh(task)
            if result.rowcount != 1:
                return False
            task.duration = 0
            db.commit()
            return True
            
        finalized = finalize_task_from_jenkins(db, task, status_info, client)
        if finalized:
            logger.info(f"Task {task_id} successfully reconciled to status: {task.status}")
        return finalized
    except Exception as e:
        logger.error(f"Failed to reconcile task {task_id} status from Jenkins: {str(e)}")
        
    return False

def reconcile_running_tasks():
    """
    Scheduled background task to reconcile any tasks currently in QUEUED, BUILDING, or RUNNING state.
    """
    db: Session = SyncSessionLocal()
    try:
        running_tasks = db.query(ReleaseTask).filter(
            ReleaseTask.status.in_(["QUEUED", "BUILDING", "RUNNING"])
        ).all()
        if not running_tasks:
            return
            
        logger.info(f"Scheduled reconciliation: Found {len(running_tasks)} active tasks to check.")
        for task in running_tasks:
            try:
                reconcile_single_task(db, task.id)
            except Exception as ex:
                logger.error(f"Failed reconciling running task {task.id}: {str(ex)}")
    except Exception as e:
        logger.error(f"Error during scheduled batch reconciliation: {str(e)}")
    finally:
        db.close()

def execute_task_workflow(plan_id: int, task_id: int):
    """
    Core release task workflow execution: updates DB states, calls Jenkins, polls status, logs history,
    and advances pipeline sequence.
    """
    db: Session = SyncSessionLocal()
    try:
        task = db.query(ReleaseTask).filter(ReleaseTask.id == task_id).first()
        if not task:
            logger.error(f"Task execution failed: Task ID {task_id} not found in DB.")
            return

        # Atomic state transition from WAITING to RUNNING
        now_time = datetime.now()
        stmt = (
            update(ReleaseTask)
            .where(
                ReleaseTask.id == task_id,
                ReleaseTask.plan_id == plan_id,
                ReleaseTask.status == "WAITING",
                exists().where(
                    ReleasePlan.id == plan_id,
                    ReleasePlan.preflight_status.in_(["PASSED", "WARNING"]),
                ),
            )
            .values(status="RUNNING", started_at=now_time)
            .execution_options(synchronize_session=False)
        )
        res = db.execute(stmt)
        db.commit()
        if res.rowcount == 0:
            db.expire_all()
            plan = db.query(ReleasePlan).filter(ReleasePlan.id == plan_id).first()
            task = db.query(ReleaseTask).filter(ReleaseTask.id == task_id).first()
            if plan and task and task.plan_id == plan_id and task.status == "WAITING":
                reason = preflight_block_reason(plan)
                if reason:
                    db.query(ReleaseTask).filter(
                        ReleaseTask.plan_id == plan_id,
                        ReleaseTask.status == "WAITING",
                    ).update(
                        {
                            ReleaseTask.status: "FAILED",
                            ReleaseTask.error_message: reason,
                            ReleaseTask.finished_at: datetime.now(),
                        },
                        synchronize_session=False,
                    )
                    if plan.status in ["WAITING", "RUNNING"]:
                        plan.status = "FAILED"
                    db.commit()
                    logger.warning(f"Release task blocked after claim rejection: {reason}")
                    return
            logger.warning(f"Task {task_id} is already running or completed. Skipping execution.")
            return

        db.refresh(task)

        # Update Plan status to RUNNING if WAITING
        plan = db.query(ReleasePlan).filter(ReleasePlan.id == plan_id).first()
        if plan and plan.status == "WAITING":
            plan.status = "RUNNING"
            db.commit()

        # 2. Setup Jenkins API Client
        server = db.query(JenkinsServer).filter(JenkinsServer.id == task.server_id).first()
        if not server:
            raise Exception("Associated Jenkins server configuration was not found.")
        if not server.is_active:
            raise Exception(f"关联的 Jenkins 实例 [{server.name}] 已被禁用，无法执行发布任务")

        client = JenkinsClient(server.url, server.username, server.api_token)

        # 3. Trigger build with backoff retries
        logger.info(f"Triggering build: Job='{task.job_name}' on Server='{server.name}'")
        queue_url = None
        max_retries = 3
        retry_delay = 5
        
        for attempt in range(1, max_retries + 1):
            try:
                queue_url = client.trigger_build(task.job_name, task.parameters, branch=task.branch)
                break
            except Exception as e:
                logger.warning(f"Attempt {attempt} to trigger Jenkins build failed: {str(e)}")
                if "HTTP 404" in str(e) or "404" in str(e):
                    raise Exception(f"Job does not exist on Jenkins server: {task.job_name}")
                if attempt == max_retries:
                    raise Exception(f"Failed to trigger Jenkins build after {max_retries} attempts. Error: {str(e)}")
                time.sleep(retry_delay)

        # 4. Resolve Queue item to Build Number (State: QUEUED)
        queue_id = client.extract_queue_id(queue_url)
        task.jenkins_queue_id = queue_id
        task.status = "QUEUED"
        task.error_message = None
        db.commit()

        def queue_on_poll(why: str):
            try:
                db.refresh(task)
                if task.status == "CANCELLED":
                    raise RuntimeError(f"Task {task_id} was cancelled by user while queued in Jenkins.")
                if why:
                    task.error_message = f"Jenkins 队列等待: {why}"
                else:
                    task.error_message = None
                db.commit()
            except Exception as poll_ex:
                if "cancelled by user" in str(poll_ex).lower():
                    raise poll_ex

        logger.info(f"Polling queue item to resolve build number: {queue_url}")
        try:
            try:
                build_number = client.get_build_number_from_queue(queue_url, on_poll=queue_on_poll)
            except TypeError:
                build_number = client.get_build_number_from_queue(queue_url)
        except Exception as q_err:
            db.refresh(task)
            if task.status == "CANCELLED" or "cancelled by user" in str(q_err).lower():
                logger.info(f"Task {task_id} was cancelled while in queue. Stopping flow.")
                return
            raise q_err
        
        build_number = int(build_number)
        build_claim = db.execute(
            update(ReleaseTask)
            .where(
                ReleaseTask.id == task.id,
                ReleaseTask.jenkins_queue_id == queue_id,
                ReleaseTask.build_number.is_(None),
            )
            .values(build_number=build_number)
            .execution_options(synchronize_session=False)
        )
        db.commit()
        db.refresh(task)
        if build_claim.rowcount != 1 and (
            task.jenkins_queue_id != queue_id
            or task.build_number != build_number
        ):
            return

        # 5. Transition to BUILDING state
        job_path = "/".join([f"job/{part}" for part in task.job_name.split("/")])
        build_url = f"{server.url.rstrip('/')}/{job_path}/{build_number}/"
        console_url = f"{server.url.rstrip('/')}/{job_path}/{build_number}/console"
        building_claim = db.execute(
            update(ReleaseTask)
            .where(
                ReleaseTask.id == task.id,
                ReleaseTask.status.in_(ACTIVE_TASK_STATUSES),
                ReleaseTask.jenkins_queue_id == queue_id,
                ReleaseTask.build_number == build_number,
            )
            .values(
                status="BUILDING",
                started_at=datetime.now(),
                error_message=None,
                build_url=build_url,
                console_url=console_url,
            )
            .execution_options(synchronize_session=False)
        )
        db.commit()
        db.refresh(task)
        if building_claim.rowcount != 1:
            if (
                task.status == "CANCELLED"
                and task.jenkins_queue_id == queue_id
                and task.build_number == build_number
            ):
                client.stop_build(task.job_name, build_number)
            return

        send_release_notification(task.id, "start")

        # 6. Poll Build Result Status
        logger.info(f"Polling build state for job: {task.job_name} #{build_number}")
        poll_interval = 8
        timeout_seconds = 3600  # Default 1h timeout
        poll_start = time.time()
        
        status_info = None
        jenkins_status = "UNKNOWN"
        consecutive_not_found = 0
        
        while time.time() - poll_start < timeout_seconds:
            # Retrieve fresh instance from DB in case status was updated asynchronously
            db.refresh(task)
            if task.status in ["CANCELLED", "SUCCESS", "FAILED", "UNSTABLE", "SKIPPED"]:
                logger.info(f"Task {task_id} status is already final ({task.status}). Exiting poll loop.")
                return
                
            try:
                status_info = client.get_build_status(task.job_name, build_number)
                consecutive_not_found = 0
                jenkins_status = normalize_jenkins_status(
                    status_info.get("building", False), status_info.get("result")
                )
                if jenkins_status not in ("BUILDING", "UNKNOWN"):
                    break
            except Exception as e:
                err_str = str(e).lower()
                logger.warning(f"Failed polling Jenkins build status for #{build_number}: {str(e)}")
                if "404" in err_str or "not found" in err_str or "does not exist" in err_str:
                    consecutive_not_found += 1
                    if consecutive_not_found >= 3:
                        logger.error(f"Jenkins build #{build_number} for {task.job_name} not found after 3 retries. Marking task as FAILED.")
                        finished_at = datetime.now()
                        if not claim_task_final_state(
                            db,
                            task.id,
                            "FAILED",
                            0,
                            task.started_at or finished_at,
                            finished_at,
                        ):
                            db.refresh(task)
                            return
                        db.refresh(task)
                        task.error_message = f"Jenkins 远端无构建记录 #{build_number}（可能已被手动删除或队列取消）"
                        db.commit()
                        send_release_notification(task.id, "failed")
                        return
            time.sleep(poll_interval)

        db.refresh(task)
        if task.status in ["CANCELLED", "SUCCESS", "FAILED", "UNSTABLE", "SKIPPED"]:
            logger.info(f"Task {task_id} was finalized externally during poll. Exiting workflow.")
            return

        if jenkins_status in ("BUILDING", "UNKNOWN"):
            raise TimeoutError(f"Jenkins build did not finish within the maximum timeout of {timeout_seconds} seconds.")

        if not finalize_task_from_jenkins(db, task, status_info, client):
            return

    except Exception as e:
        logger.error(f"Task workflow execution failure: {str(e)}")
        # Check current state in DB
        db.rollback()
        task = db.query(ReleaseTask).filter(ReleaseTask.id == task_id).first()
        if task and task.status not in ["CANCELLED", "SUCCESS", "FAILED", "UNSTABLE", "SKIPPED"]:
            is_still_building = False
            corrected = False
            
            if task.build_number:
                try:
                    server = db.query(JenkinsServer).filter(JenkinsServer.id == task.server_id).first()
                    if server:
                        client = JenkinsClient(server.url, server.username, server.api_token)
                        status_info = client.get_build_status(task.job_name, task.build_number)
                        if status_info.get("building") is True:
                            is_still_building = True
                        else:
                            corrected = reconcile_single_task(db, task_id)
                except Exception as ex:
                    logger.error(f"Failed to execute inline status check for task {task_id}: {str(ex)}")

            if is_still_building:
                logger.warning(f"Task {task_id} transient error: '{str(e)}', Jenkins build #{task.build_number} still BUILDING. Keeping BUILDING state.")
                task.error_message = f"网络连接闪断: {str(e)} (后端保持构建检测)"
                db.commit()
            elif not corrected:
                finished_at = datetime.now()
                if not claim_task_final_state(
                    db,
                    task.id,
                    "FAILED",
                    0,
                    task.started_at or finished_at,
                    finished_at,
                ):
                    db.refresh(task)
                    return
                db.refresh(task)
                task.error_message = str(e)
                db.commit()
                try:
                    send_release_notification(task.id, "failed")
                    write_history(db, task, "FAILED", 0, "SYSTEM_ERROR", None)
                except Exception as ex:
                    logger.error(f"Failed to record system error to history log: {str(ex)}")
                handle_pipeline_failure(db, plan_id, task_id)
    finally:
        db.close()

def write_history(db: Session, task: ReleaseTask, status: str, duration: int, raw_status: str, client: Optional[JenkinsClient]):
    logs = ""
    if client and task.build_number:
        try:
            logs, _, _ = client.get_progressive_log(
                task.job_name, task.build_number, start=0
            )
        except Exception as e:
            logs = f"Failed to sync build logs: {str(e)}"

    history_filters = [ReleaseHistory.task_id == task.id]
    if (
        task.server_id is not None
        and task.job_name
        and task.build_number is not None
    ):
        history_filters.append(
            and_(
                ReleaseHistory.server_id == task.server_id,
                ReleaseHistory.job_name == task.job_name,
                ReleaseHistory.build_number == task.build_number,
            )
        )

    histories = (
        db.query(ReleaseHistory)
        .filter(or_(*history_filters))
        .order_by(
            ReleaseHistory.task_id.isnot(None).desc(),
            ReleaseHistory.id.desc(),
        )
        .all()
    )

    if histories:
        main_history = histories[0]
        for extra in histories[1:]:
            db.delete(extra)
        if len(histories) > 1:
            db.flush()
    else:
        main_history = ReleaseHistory()
        db.add(main_history)

    main_history.task_id = task.id
    main_history.plan_id = task.plan_id
    main_history.server_id = task.server_id
    main_history.server_name = task.server.name if task.server else "Unknown"
    main_history.job_name = task.job_name
    main_history.branch = task.branch
    main_history.build_number = task.build_number
    main_history.status = status
    main_history.is_external = False
    main_history.trigger_by = "scheduler_service"
    main_history.started_at = task.started_at
    main_history.finished_at = task.finished_at
    main_history.duration = duration
    main_history.logs = logs[:200000]
    main_history.raw_response = {"final_jenkins_result": raw_status}
    db.commit()
    logger.info(
        f"Recorded ReleaseHistory for task {task.id} with status: {status}"
    )

def handle_pipeline_success(db: Session, plan_id: int, task_id: int):
    """
    Called when a task succeeds. For PIPELINE plans, triggers the downstream task.
    """
    plan = db.query(ReleasePlan).filter(ReleasePlan.id == plan_id).first()
    if not plan:
        return
        
    if plan.type == "PIPELINE":
        # Start every dependant to safely handle branching plans created by older versions.
        next_tasks = db.query(ReleaseTask).filter(
            ReleaseTask.plan_id == plan_id,
            ReleaseTask.depends_on_task_id == task_id,
            ReleaseTask.status == "WAITING"
        ).all()
        
        if next_tasks:
            for next_task in next_tasks:
                logger.info(f"Triggering pipeline step: Task ID={next_task.id} depending on Task ID={task_id}")
                t = threading.Thread(target=execute_task_workflow, args=(plan_id, next_task.id))
                t.start()
            return
            
    # Check if entire plan has ended
    check_and_finalize_plan(db, plan_id)

def handle_pipeline_failure(db: Session, plan_id: int, task_id: int):
    """
    Called when a task fails. Handles PIPELINE flow based on STOP or CONTINUE strategies.
    """
    plan = db.query(ReleasePlan).filter(ReleasePlan.id == plan_id).first()
    if not plan:
        return

    if plan.type == "PIPELINE":
        if plan.pipeline_failure_strategy == "STOP":
            logger.info(f"Pipeline error strategy is STOP. Terminating plan {plan_id} execution flow.")
            # Set all downstream waiting tasks as SKIPPED
            downstream_tasks = db.query(ReleaseTask).filter(
                ReleaseTask.plan_id == plan_id,
                ReleaseTask.status == "WAITING"
            ).all()
            for t in downstream_tasks:
                t.status = "SKIPPED"
                t.error_message = f"Skipped because pre-requisite Task {task_id} failed."
            db.commit()
            
            plan.status = "FAILED"
            db.commit()
            return
        else: # CONTINUE
            # Launch every waiting dependant for compatibility with legacy branching plans.
            next_tasks = db.query(ReleaseTask).filter(
                ReleaseTask.plan_id == plan_id,
                ReleaseTask.depends_on_task_id == task_id,
                ReleaseTask.status == "WAITING"
            ).all()
            if next_tasks:
                for next_task in next_tasks:
                    logger.info(f"Pipeline error strategy is CONTINUE. Launching next step: Task ID={next_task.id}")
                    t = threading.Thread(target=execute_task_workflow, args=(plan_id, next_task.id))
                    t.start()
                return

    check_and_finalize_plan(db, plan_id)

def check_and_finalize_plan(db: Session, plan_id: int):
    """
    Inspects all task statuses within a plan. If everything is finished, aggregates their status to the plan.
    """
    plan = db.query(ReleasePlan).filter(ReleasePlan.id == plan_id).first()
    if not plan:
        return
        
    tasks = db.query(ReleaseTask).filter(ReleaseTask.plan_id == plan_id).all()
    statuses = [t.status for t in tasks]
    
    # 1. 如果所有任务都处于 WAITING 状态，计划状态保持 WAITING（等待中）
    if all(s == "WAITING" for s in statuses):
        if plan.status not in ["WAITING", "CANCELLED", "FAILED"]:
            plan.status = "WAITING"
            db.commit()
        return

    # 2. 如果存在正在队列中或构建中的任务 (QUEUED, BUILDING, RUNNING) 或部分已完成，计划状态为 RUNNING
    if any(s in ["QUEUED", "BUILDING", "RUNNING"] for s in statuses) or (any(s in ["SUCCESS", "FAILED", "CANCELLED", "UNSTABLE"] for s in statuses) and any(s in ["WAITING", "QUEUED", "BUILDING", "RUNNING"] for s in statuses)):
        if plan.status != "RUNNING":
            plan.status = "RUNNING"
            db.commit()
        return
        
    # Check results to decide final plan state
    if all(s == "SUCCESS" for s in statuses):
        plan.status = "SUCCESS"
    elif any(s in ["FAILED", "UNSTABLE"] for s in statuses):
        plan.status = "FAILED"
    elif any(s == "CANCELLED" for s in statuses):
        plan.status = "CANCELLED"
    else:
        # e.g., all skipped or mix of skipped
        plan.status = "FAILED"
        
    db.commit()
    logger.info(f"Release Plan {plan_id} completed execution. Final consolidated status: {plan.status}")
    send_plan_summary_notification(plan_id)


