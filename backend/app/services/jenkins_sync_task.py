from datetime import datetime
from loguru import logger
from sqlalchemy.orm import Session

from app.core.database import SyncSessionLocal
from app.models.jenkins import JenkinsServer, JenkinsJob
from app.models.release import ReleaseHistory, ReleaseTask
from app.services.jenkins_client import JenkinsClient

def sync_external_builds():
    """
    Background worker task scheduled to scan Jenkins jobs for external manually-triggered builds,
    and archive their statuses and logs dynamically into release_history.
    """
    logger.info("Starting scheduled task: Syncing external manually-triggered builds...")
    
    db: Session = SyncSessionLocal()
    try:
        # 1. Fetch active servers
        servers = db.query(JenkinsServer).filter(JenkinsServer.is_active == 1).all()
        for server in servers:
            client = JenkinsClient(server.url, server.username, server.api_token)
            
            # 2. Fetch jobs associated with this server
            jobs = db.query(JenkinsJob).filter(JenkinsJob.server_id == server.id).all()
            for job in jobs:
                remote_build_numbers = client.get_build_numbers(job.name)

                # Retrieve last 10 builds for each job
                builds = client.get_recent_builds(job.name, limit=10)
                
                for build in builds:
                    # We only archive completed runs
                    if build.get("building") is True or not build.get("result"):
                        continue
                        
                    build_number = build.get("number")
                    result = build.get("result")
                    timestamp = build.get("timestamp") or 0
                    duration_ms = build.get("duration") or 0
                    
                    # 3. Check if build exists in release_task or release_history
                    # Avoid duplicated records for tasks spawned inside our scheduler
                    task_exists = db.query(ReleaseTask).filter(
                        ReleaseTask.server_id == server.id,
                        ReleaseTask.job_name == job.name,
                        ReleaseTask.build_number == build_number
                    ).first()
                    
                    if task_exists:
                        continue
                        
                    history_exists = db.query(ReleaseHistory).filter(
                        ReleaseHistory.server_name == server.name,
                        ReleaseHistory.job_name == job.name,
                        ReleaseHistory.build_number == build_number
                    ).first()
                    
                    if history_exists:
                        continue
                        
                    try:
                        # This is an external/manually-triggered build run! Let's archive it.
                        logger.info(f"Found external build: {job.name} #{build_number} on Server '{server.name}'")
                        
                        # Resolve trigger author
                        trigger_by = "Unknown"
                        branch = "external"
                        
                        # Traverse actions to parse triggers and parameters
                        actions = build.get("actions", []) or []
                        for action in actions:
                            if not action:
                                continue
                                
                            # Parse user or cause trigger
                            causes = action.get("causes", []) or []
                            for cause in causes:
                                if not cause:
                                    continue
                                if cause.get("userName"):
                                    trigger_by = cause.get("userName")
                                elif cause.get("shortDescription"):
                                    trigger_by = cause.get("shortDescription")
                                    
                            # Parse branch parameters if present
                            parameters = action.get("parameters", []) or []
                            for param in parameters:
                                p_name = param.get("name", "").lower()
                                if p_name in ["branch", "branch_name", "tag", "git_branch", "gitparameter"]:
                                    branch = str(param.get("value", ""))
                                    
                        # Fetch console logs
                        logs = ""
                        try:
                            logs, _, _ = client.get_progressive_log(job.name, build_number, start=0)
                        except Exception as e:
                            logs = f"Failed to sync build logs: {str(e)}"
                            
                        started_at = datetime.fromtimestamp(timestamp / 1000.0) if timestamp else datetime.now()
                        finished_at = datetime.fromtimestamp((timestamp + duration_ms) / 1000.0) if (timestamp and duration_ms) else datetime.now()
                        
                        # Map Jenkins build result status
                        status = "SUCCESS" if result == "SUCCESS" else "FAILED"
                        
                        # Insert history entry
                        history = ReleaseHistory(
                            task_id=None,
                            plan_id=None,
                            server_name=server.name,
                            job_name=job.name,
                            branch=branch,
                            build_number=build_number,
                            status=status,
                            trigger_by=trigger_by,
                            started_at=started_at,
                            finished_at=finished_at,
                            duration=duration_ms // 1000, # convert to seconds
                            logs=logs[:200000], # limit log text index
                            is_external=True,
                            raw_response={"final_jenkins_result": result, "external_sync": True}
                        )
                        db.add(history)
                        db.commit()
                        logger.info(f"Successfully archived external build history for {job.name} #{build_number}")
                    except Exception as build_err:
                        db.rollback()
                        logger.error(f"Failed to sync and save build {job.name} #{build_number}: {str(build_err)}")

                if remote_build_numbers is None:
                    logger.warning(f"Skipping stale build deletion for {job.name}: Jenkins inventory unavailable")
                    continue

                try:
                    stale = db.query(ReleaseHistory).filter(
                        ReleaseHistory.server_name == server.name,
                        ReleaseHistory.job_name == job.name,
                        ReleaseHistory.is_external.is_(True)
                    )
                    if remote_build_numbers:
                        stale = stale.filter(ReleaseHistory.build_number.notin_(remote_build_numbers))
                    deleted = stale.delete(synchronize_session=False)
                    db.commit()
                    if deleted:
                        logger.info(f"Removed {deleted} stale external builds for {job.name} on Server '{server.name}'")
                except Exception as clean_err:
                    db.rollback()
                    logger.error(f"Failed to clean stale external builds for {job.name}: {str(clean_err)}")

    except Exception as e:
        logger.error(f"Error during scheduled external build sync: {str(e)}")
    finally:
        db.close()
