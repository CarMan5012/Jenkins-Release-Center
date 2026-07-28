from datetime import datetime, timedelta
from typing import Optional
import threading

from loguru import logger
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.database import SyncSessionLocal
from app.models.jenkins import JenkinsJob, JenkinsServer
from app.models.release import ReleaseHistory
from app.services.jenkins_client import (
    JenkinsClient,
    jenkins_datetime,
    normalize_jenkins_status,
)


_sync_lock = threading.Lock()


def cleanup_all_duplicate_histories(db: Session):
    """Keep one preferred row for each complete Jenkins build identity."""
    try:
        duplicates = (
            db.query(
                ReleaseHistory.server_id,
                ReleaseHistory.job_name,
                ReleaseHistory.build_number,
                func.count(ReleaseHistory.id).label("count"),
            )
            .filter(
                ReleaseHistory.server_id.isnot(None),
                ReleaseHistory.job_name.isnot(None),
                ReleaseHistory.build_number.isnot(None),
            )
            .group_by(
                ReleaseHistory.server_id,
                ReleaseHistory.job_name,
                ReleaseHistory.build_number,
            )
            .having(func.count(ReleaseHistory.id) > 1)
            .all()
        )

        for item in duplicates:
            entries = (
                db.query(ReleaseHistory)
                .filter(
                    ReleaseHistory.server_id == item.server_id,
                    ReleaseHistory.job_name == item.job_name,
                    ReleaseHistory.build_number == item.build_number,
                )
                .order_by(
                    ReleaseHistory.task_id.isnot(None).desc(),
                    (ReleaseHistory.status != "BUILDING").desc(),
                    ReleaseHistory.id.desc(),
                )
                .all()
            )
            for extra in entries[1:]:
                db.delete(extra)
            if len(entries) > 1:
                logger.info(
                    "Purged {} duplicate entries for server {} job '{}' #{}, kept ID #{}",
                    len(entries) - 1,
                    item.server_id,
                    item.job_name,
                    item.build_number,
                    entries[0].id,
                )
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.warning("Error during duplicate history cleanup: {}", str(exc))


def _update_history(
    history,
    server,
    status,
    started_at,
    finished_at,
    duration,
    trigger_by,
    branch,
    logs,
    raw_response,
):
    history.server_name = server.name
    history.status = status
    history.started_at = started_at
    history.finished_at = finished_at
    history.duration = duration
    if trigger_by != "Unknown":
        history.trigger_by = trigger_by
    if branch != "external":
        history.branch = branch
    if logs is not None:
        history.logs = logs[:200000]
    history.raw_response = raw_response


def sync_external_builds(server_id: Optional[int] = None, job_name: Optional[str] = None):
    """Synchronize Jenkins build metadata without treating the recent page as inventory."""
    if not _sync_lock.acquire(blocking=False):
        logger.info("Another sync_external_builds task is already running. Skipping redundant execution.")
        return

    logger.info(
        "Syncing external builds from Jenkins (server={}, job={})...",
        server_id,
        job_name,
    )
    db: Session = SyncSessionLocal()
    try:
        cleanup_all_duplicate_histories(db)

        server_query = db.query(JenkinsServer).filter(JenkinsServer.is_active == 1)
        if server_id is not None:
            server_query = server_query.filter(JenkinsServer.id == server_id)

        for server in server_query.all():
            client = JenkinsClient(server.url, server.username, server.api_token)
            job_query = db.query(JenkinsJob).filter(JenkinsJob.server_id == server.id)
            if job_name is not None:
                job_query = job_query.filter(JenkinsJob.name == job_name)

            for job in job_query.all():
                recent_builds = client.get_recent_builds(job.name, limit=20) or []
                builds = {
                    build.get("number"): build
                    for build in recent_builds
                    if build.get("number") is not None
                }

                for build_number, build in builds.items():
                    is_building = build.get("building") is True
                    result = build.get("result")
                    status = normalize_jenkins_status(is_building, result)
                    duration_ms = build.get("duration")
                    duration = (
                        int(duration_ms / 1000)
                        if isinstance(duration_ms, (int, float)) and duration_ms > 0
                        else 0
                    )
                    started_at = jenkins_datetime(build.get("timestamp")) or datetime.now()
                    finished_at = None if is_building else started_at + timedelta(seconds=duration)

                    trigger_by = "Unknown"
                    branch = "external"
                    for action in build.get("actions", []) or []:
                        if not action:
                            continue
                        for cause in action.get("causes", []) or []:
                            if not cause:
                                continue
                            if cause.get("userName"):
                                trigger_by = cause["userName"]
                            elif cause.get("shortDescription"):
                                trigger_by = cause["shortDescription"]
                        for parameter in action.get("parameters", []) or []:
                            parameter_name = parameter.get("name", "").lower()
                            if parameter_name in {
                                "branch", "branch_name", "tag", "git_branch", "gitparameter"
                            }:
                                branch = str(parameter.get("value", ""))

                    logs = None
                    if not is_building:
                        try:
                            log_text, _, has_more = client.get_progressive_log(
                                job.name, build_number, 0
                            )
                            if has_more or (log_text or "").startswith(
                                (
                                    "HTTP Error ",
                                    "Error retrieving build logs:",
                                    "Waiting for build console output",
                                )
                            ):
                                logger.warning(
                                    "Jenkins log is incomplete for server {} job '{}' #{}",
                                    server.id, job.name, build_number,
                                )
                            else:
                                logs = (log_text or "")[:200000]
                        except Exception as exc:
                            logger.warning(
                                "Could not archive Jenkins log for server {} job '{}' #{}: {}",
                                server.id, job.name, build_number, str(exc),
                            )

                    raw_response = {
                        "final_jenkins_result": result,
                        "external_sync": True,
                        "queueId": build.get("queueId"),
                    }
                    existing = (
                        db.query(ReleaseHistory)
                        .filter(
                            ReleaseHistory.server_id == server.id,
                            ReleaseHistory.job_name == job.name,
                            ReleaseHistory.build_number == build_number,
                        )
                        .order_by(
                            ReleaseHistory.task_id.isnot(None).desc(),
                            (ReleaseHistory.status != "BUILDING").desc(),
                            ReleaseHistory.id.desc(),
                        )
                        .first()
                    )
                    if existing is not None:
                        _update_history(
                            existing, server, status, started_at, finished_at, duration,
                            trigger_by, branch, logs, raw_response,
                        )
                        db.commit()
                        continue

                    history = ReleaseHistory(
                        task_id=None,
                        plan_id=None,
                        server_id=server.id,
                        server_name=server.name,
                        job_name=job.name,
                        branch=branch,
                        build_number=build_number,
                        status=status,
                        trigger_by=trigger_by,
                        started_at=started_at,
                        finished_at=finished_at,
                        duration=duration,
                        logs=logs or "",
                        is_external=True,
                        raw_response=raw_response,
                    )
                    db.add(history)
                    try:
                        db.commit()
                    except IntegrityError:
                        db.rollback()
                        existing = (
                            db.query(ReleaseHistory)
                            .filter(
                                ReleaseHistory.server_id == server.id,
                                ReleaseHistory.job_name == job.name,
                                ReleaseHistory.build_number == build_number,
                            )
                            .order_by(
                                ReleaseHistory.task_id.isnot(None).desc(),
                                (ReleaseHistory.status != "BUILDING").desc(),
                                ReleaseHistory.id.desc(),
                            )
                            .first()
                        )
                        if existing is None:
                            logger.warning(
                                "History identity conflicted but could not be reloaded for server {} job '{}' #{}",
                                server.id, job.name, build_number,
                            )
                            continue
                        _update_history(
                            existing, server, status, started_at, finished_at, duration,
                            trigger_by, branch, logs, raw_response,
                        )
                        db.commit()

                inventory = client.get_build_numbers(job.name)
                if inventory is None:
                    logger.warning(
                        "Skipping history deletion because Jenkins inventory is unavailable for server {} job '{}'",
                        server.id, job.name,
                    )
                    continue
                stale_records = (
                    db.query(ReleaseHistory)
                    .filter(
                        ReleaseHistory.server_id == server.id,
                        ReleaseHistory.job_name == job.name,
                        ReleaseHistory.is_external.is_(True),
                        ReleaseHistory.build_number.notin_(inventory),
                    )
                    .all()
                )
                for stale_record in stale_records:
                    db.delete(stale_record)
                if stale_records:
                    db.commit()
    except Exception as exc:
        db.rollback()
        logger.error("Error during scheduled external build sync: {}", str(exc))
    finally:
        db.close()
        if _sync_lock.locked():
            try:
                _sync_lock.release()
            except RuntimeError:
                pass