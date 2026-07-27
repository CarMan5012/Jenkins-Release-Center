from datetime import datetime
from typing import Optional
from loguru import logger
from sqlalchemy.orm import Session

from app.core.database import SyncSessionLocal
from app.models.jenkins import JenkinsServer, JenkinsJob
from app.models.release import ReleaseHistory, ReleaseTask
from app.services.jenkins_client import JenkinsClient

import threading

_sync_lock = threading.Lock()

from sqlalchemy import func

def cleanup_all_duplicate_histories(db: Session):
    """
    Find and purge all duplicate records in release_history with identical (job_name, build_number).
    Retains the record with the most complete status or highest ID, and deletes the rest.
    """
    try:
        duplicates = db.query(
            ReleaseHistory.job_name,
            ReleaseHistory.build_number,
            func.count(ReleaseHistory.id).label("count")
        ).filter(
            ReleaseHistory.job_name.isnot(None),
            ReleaseHistory.build_number.isnot(None)
        ).group_by(
            ReleaseHistory.job_name,
            ReleaseHistory.build_number
        ).having(
            func.count(ReleaseHistory.id) > 1
        ).all()

        for item in duplicates:
            job_name, build_num = item.job_name, item.build_number
            entries = db.query(ReleaseHistory).filter(
                ReleaseHistory.job_name == job_name,
                ReleaseHistory.build_number == build_num
            ).order_by(
                (ReleaseHistory.status != 'BUILDING').desc(),
                ReleaseHistory.id.desc()
            ).all()

            if len(entries) > 1:
                main_entry = entries[0]
                for extra in entries[1:]:
                    db.delete(extra)
                db.commit()
                logger.info(f"Purged {len(entries) - 1} duplicate entries for job '{job_name}' #{build_num}, kept main ID #{main_entry.id}")
    except Exception as e:
        db.rollback()
        logger.warning(f"Error during duplicate history cleanup: {str(e)}")

def sync_external_builds(server_id: Optional[int] = None, job_name: Optional[str] = None):
    """
    Background worker task scheduled to scan Jenkins jobs for external manually-triggered builds,
    and strictly mirror their latest statuses and metadata into release_history.
    """
    if not _sync_lock.acquire(blocking=False):
        logger.info("Another sync_external_builds task is already running. Skipping redundant execution.")
        return

    logger.info(f"Syncing external builds strictly mirroring Jenkins (server={server_id}, job={job_name})...")
    
    db: Session = SyncSessionLocal()
    try:
        # 1. Run global database deduplication purge on start
        cleanup_all_duplicate_histories(db)

        # 2. Fetch active servers
        server_query = db.query(JenkinsServer).filter(JenkinsServer.is_active == 1)
        if server_id:
            server_query = server_query.filter(JenkinsServer.id == server_id)
        servers = server_query.all()

        for server in servers:
            client = JenkinsClient(server.url, server.username, server.api_token)
            
            # 3. Fetch jobs associated with this server
            job_query = db.query(JenkinsJob).filter(JenkinsJob.server_id == server.id)
            if job_name:
                job_query = job_query.filter(JenkinsJob.name == job_name)
            jobs = job_query.all()

            for job in jobs:
                # Retrieve last 20 builds for target job from Jenkins
                recent_builds = client.get_recent_builds(job.name, limit=20)
                if not recent_builds:
                    continue

                jenkins_build_map = {b.get("number"): b for b in recent_builds if b.get("number")}
                jenkins_numbers = set(jenkins_build_map.keys())

                # Step A: Update or Insert records strictly based on latest Jenkins data
                for build_number, build in jenkins_build_map.items():
                    is_building = build.get("building") is True
                    result = build.get("result")
                    timestamp = build.get("timestamp") or 0
                    duration_ms = build.get("duration") or 0

                    if is_building:
                        status = "BUILDING"
                    else:
                        status = "SUCCESS" if result == "SUCCESS" else ("UNSTABLE" if result == "UNSTABLE" else "FAILED")

                    trigger_by = "Unknown"
                    branch = "external"
                    actions = build.get("actions", []) or []
                    for action in actions:
                        if not action:
                            continue
                        causes = action.get("causes", []) or []
                        for cause in causes:
                            if not cause:
                                continue
                            if cause.get("userName"):
                                trigger_by = cause.get("userName")
                            elif cause.get("shortDescription"):
                                trigger_by = cause.get("shortDescription")
                        parameters = action.get("parameters", []) or []
                        for param in parameters:
                            p_name = param.get("name", "").lower()
                            if p_name in ["branch", "branch_name", "tag", "git_branch", "gitparameter"]:
                                branch = str(param.get("value", ""))

                    started_at = datetime.fromtimestamp(timestamp / 1000.0) if timestamp else datetime.now()
                    finished_at = datetime.fromtimestamp((timestamp + duration_ms) / 1000.0) if (timestamp and duration_ms and not is_building) else None

                    # Query all matching entries in local database for this (job_name, build_number)
                    existing_entries = db.query(ReleaseHistory).filter(
                        ReleaseHistory.job_name == job.name,
                        ReleaseHistory.build_number == build_number
                    ).order_by(ReleaseHistory.id.desc()).all()

                    if existing_entries:
                        main_entry = existing_entries[0]
                        # Purge duplicate entries if any
                        if len(existing_entries) > 1:
                            for extra in existing_entries[1:]:
                                db.delete(extra)
                            db.commit()

                        # Overwrite with latest Jenkins status and timestamps
                        main_entry.status = status
                        main_entry.started_at = started_at
                        if not is_building:
                            main_entry.finished_at = finished_at
                            main_entry.duration = duration_ms // 1000
                        if trigger_by != "Unknown":
                            main_entry.trigger_by = trigger_by
                        if branch != "external":
                            main_entry.branch = branch
                        db.commit()
                    else:
                        # Insert brand new external history record
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
                            duration=duration_ms // 1000,
                            logs="",
                            is_external=True,
                            raw_response={"final_jenkins_result": result, "external_sync": True}
                        )
                        db.add(history)
                        db.commit()

                # Step B: Purge stale local external histories that no longer exist in latest Jenkins builds list
                stale_records = db.query(ReleaseHistory).filter(
                    ReleaseHistory.server_name == server.name,
                    ReleaseHistory.job_name == job.name,
                    ReleaseHistory.is_external.is_(True),
                    ReleaseHistory.build_number.notin_(jenkins_numbers)
                ).all()
                if stale_records:
                    for s_rec in stale_records:
                        db.delete(s_rec)
                    db.commit()

    except Exception as e:
        logger.error(f"Error during scheduled external build sync: {str(e)}")
    finally:
        db.close()
        if _sync_lock.locked():
            try:
                _sync_lock.release()
            except RuntimeError:
                pass
