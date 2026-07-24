import time
import threading
from datetime import datetime
from typing import Optional, List, Dict, Any
from sqlalchemy import exists, update
from sqlalchemy.orm import Session
from loguru import logger

from app.core.database import SyncSessionLocal
from app.models.release import ReleasePlan, ReleaseTask, ReleaseHistory
from app.models.jenkins import JenkinsServer, JenkinsJob
from app.services.jenkins_client import JenkinsClient
from app.services.notification import send_release_notification
from app.services.release_preflight import preflight_block_reason

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

    # Spawn a separate thread to handle long running polls so APScheduler thread pool is not blocked
    t = threading.Thread(target=execute_task_workflow, args=(plan_id, task_id))
    t.start()

def reconcile_single_task(db: Session, task_id: int) -> bool:
    """
    Queries Jenkins to check the current build status of a task and updates db accordingly.
    Returns True if task has reached a final state and was updated, False if still building or failed to poll.
    """
    task = db.query(ReleaseTask).filter(ReleaseTask.id == task_id).first()
    if not task or not task.build_number:
        return False
        
    if task.status in ["SUCCESS", "CANCELLED", "SKIPPED"]:
        # If the task is in final state, verify if its history matches. If so, skip reconciliation.
        histories = db.query(ReleaseHistory).filter(
            ReleaseHistory.task_id == task.id,
            ReleaseHistory.is_external == False
        ).all()
        # Only skip if there is exactly 1 history record and its status matches the task status.
        # Otherwise (multiple duplicates or inconsistent status), we must run reconciliation to clean/fix.
        if len(histories) == 1 and histories[0].status == task.status:
            return False
        
    server = db.query(JenkinsServer).filter(JenkinsServer.id == task.server_id).first()
    if not server:
        logger.error(f"Reconciliation error: Associated Jenkins server for task {task_id} not found.")
        return False
        
    try:
        client = JenkinsClient(server.url, server.username, server.api_token)
        status_info = client.get_build_status(task.job_name, task.build_number)
        
        if status_info.get("building") is True:
            return False
            
        build_result = status_info.get("result")
        duration = status_info.get("duration", 0)
        
        if build_result:
            task.finished_at = datetime.now()
            task.duration = duration
            
            if build_result == "SUCCESS":
                task.status = "SUCCESS"
                task.error_message = None
                db.commit()
                send_release_notification(task.id, "success")
                write_history(db, task, "SUCCESS", duration, "SUCCESS", client)
                handle_pipeline_success(db, task.plan_id, task.id)
            else:
                task.status = "FAILED"
                task.error_message = f"Jenkins Build completed with status: {build_result} (Reconciled)"
                db.commit()
                send_release_notification(task.id, "failed")
                write_history(db, task, "FAILED", duration, build_result, client)
                handle_pipeline_failure(db, task.plan_id, task.id)
            
            logger.info(f"Task {task_id} successfully reconciled to status: {task.status}")
            return True
    except Exception as e:
        logger.error(f"Failed to reconcile task {task_id} status from Jenkins: {str(e)}")
        
    return False

def reconcile_running_tasks():
    """
    Scheduled background task to reconcile any tasks currently in RUNNING state.
    """
    db: Session = SyncSessionLocal()
    try:
        running_tasks = db.query(ReleaseTask).filter(ReleaseTask.status == "RUNNING").all()
        if not running_tasks:
            return
            
        logger.info(f"Scheduled reconciliation: Found {len(running_tasks)} RUNNING tasks to check.")
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
            logger.warning(f"Task {task_id} is already running or completed. Skipping execution.")
            return

        db.refresh(task)

        # Update Plan status to RUNNING if WAITING
        plan = db.query(ReleasePlan).filter(ReleasePlan.id == plan_id).first()
        if plan and plan.status == "WAITING":
            plan.status = "RUNNING"
            db.commit()

        # Broadcast release start notice
        send_release_notification(task.id, "start")

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

        # 4. Resolve Queue item to Build Number
        logger.info(f"Polling queue item to resolve build number: {queue_url}")
        build_number = client.get_build_number_from_queue(queue_url)
        task.build_number = build_number
        
        # Compute exact URL links
        job_path = "/".join([f"job/{part}" for part in task.job_name.split("/")])
        task.build_url = f"{server.url.rstrip('/')}/{job_path}/{build_number}/"
        task.console_url = f"{server.url.rstrip('/')}/{job_path}/{build_number}/console"
        db.commit()

        # Cancellation may arrive while Jenkins is still assigning a build number.
        db.refresh(task)
        if task.status == "CANCELLED":
            logger.info(f"Task {task_id} was cancelled while queued. Stopping Jenkins build #{build_number}.")
            client.stop_build(task.job_name, build_number)
            return

        # 5. Poll Build Result Status
        logger.info(f"Polling build state for job: {task.job_name} #{build_number}")
        poll_interval = 8
        timeout_seconds = 3600  # Default 1h timeout
        poll_start = time.time()
        
        build_result = None
        duration = 0
        
        while time.time() - poll_start < timeout_seconds:
            # Retrieve fresh instance from DB in case status was updated asynchronously
            db.refresh(task)
            if task.status == "CANCELLED":
                logger.info(f"Task {task_id} was aborted by user. Exiting poll loop.")
                # Task status is already CANCELLED, return early
                return
                
            try:
                status_info = client.get_build_status(task.job_name, build_number)
                if not status_info["building"]:
                    build_result = status_info["result"]
                    duration = status_info["duration"]
                    break
            except Exception as e:
                logger.warning(f"Failed polling Jenkins build status: {str(e)}")
            time.sleep(poll_interval)

        if not build_result:
            raise TimeoutError(f"Jenkins build did not finish within the maximum timeout of {timeout_seconds} seconds.")

        task.finished_at = datetime.now()
        task.duration = duration

        if build_result == "SUCCESS":
            task.status = "SUCCESS"
            db.commit()
            send_release_notification(task.id, "success")
            write_history(db, task, "SUCCESS", duration, "SUCCESS", client)
            # Advance to next dependency in pipeline
            handle_pipeline_success(db, plan_id, task_id)
        else:
            task.status = "FAILED"
            task.error_message = f"Jenkins Build completed with status: {build_result}"
            db.commit()
            send_release_notification(task.id, "failed")
            write_history(db, task, "FAILED", duration, build_result, client)
            handle_pipeline_failure(db, plan_id, task_id)

    except Exception as e:
        logger.error(f"Task workflow execution failure: {str(e)}")
        # Check current state in DB
        db.rollback()
        task = db.query(ReleaseTask).filter(ReleaseTask.id == task_id).first()
        if task and task.status != "CANCELLED":
            # Attempt final instant status check/correction from Jenkins
            corrected = False
            if task.build_number:
                try:
                    corrected = reconcile_single_task(db, task_id)
                except Exception as ex:
                    logger.error(f"Failed to execute final inline correction for task {task_id}: {str(ex)}")
            
            if not corrected:
                task.status = "FAILED"
                task.error_message = str(e)
                task.finished_at = datetime.now()
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
    """
    Log completed build execution trace details and progressive log texts to historical archives.
    Updates existing history log if it already exists for the task, to prevent duplicated records and stat discrepancy.
    Clean up any redundant historical rows for the same task.
    """
    logs = ""
    if client and task.build_number:
        try:
            # We capture the final logs snapshot
            logs, _, _ = client.get_progressive_log(task.job_name, task.build_number, start=0)
        except Exception as e:
            logs = f"Failed to sync build logs: {str(e)}"
            
    # Look for all existing history records for this task (excluding external manual syncs)
    histories = db.query(ReleaseHistory).filter(
        ReleaseHistory.task_id == task.id,
        ReleaseHistory.is_external == False
    ).order_by(ReleaseHistory.id.desc()).all()
    
    if histories:
        # Update the latest one (first item in desc order)
        main_history = histories[0]
        main_history.status = status
        main_history.finished_at = task.finished_at
        main_history.duration = duration
        if logs:
            main_history.logs = logs[:200000]
        main_history.raw_response = {"final_jenkins_result": raw_status, "reconciled": True}
        
        # Delete any other redundant history records for this task
        for extra in histories[1:]:
            db.delete(extra)
            
        db.commit()
        logger.info(f"Updated main ReleaseHistory for task {task.id} to status {status} and removed {len(histories) - 1} duplicates.")
    else:
        history = ReleaseHistory(
            task_id=task.id,
            plan_id=task.plan_id,
            server_name=task.server.name if task.server else "Unknown",
            job_name=task.job_name,
            branch=task.branch,
            build_number=task.build_number,
            status=status,
            trigger_by="scheduler_service",
            started_at=task.started_at,
            finished_at=task.finished_at,
            duration=duration,
            logs=logs[:200000],
            raw_response={"final_jenkins_result": raw_status}
        )
        db.add(history)
        db.commit()
        logger.info(f"Created new ReleaseHistory for task {task.id} with status: {status}")

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
    
    # If anything is still running or pending, plan is active
    if any(s in ["WAITING", "RUNNING"] for s in statuses):
        return
        
    # Check results to decide final plan state
    if all(s == "SUCCESS" for s in statuses):
        plan.status = "SUCCESS"
    elif any(s == "FAILED" for s in statuses):
        plan.status = "FAILED"
    else:
        # e.g., mix of success, skipped, cancelled
        plan.status = "FAILED"
        
    db.commit()
    logger.info(f"Release Plan {plan_id} completed execution. Final consolidated status: {plan.status}")
