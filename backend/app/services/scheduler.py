import re
from apscheduler.events import EVENT_JOB_MISSED
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from app.core.config import settings
from app.core.database import SyncSessionLocal
from app.models.release import ReleasePlan, ReleaseTask
from loguru import logger
from datetime import datetime

class SchedulerManager:
    def __init__(self):
        # We configure the scheduler with SQLAlchemyJobStore for persistence
        from app.core.database import sync_engine
        self.jobstores = {
            'default': SQLAlchemyJobStore(engine=sync_engine)
        }

        self.scheduler = BackgroundScheduler(jobstores=self.jobstores)
        self.scheduler.add_listener(
            self.handle_missed_job,
            EVENT_JOB_MISSED,
        )

    def handle_missed_job(self, event):
        match = re.fullmatch(
            r"plan_(\d+)_task_(\d+)",
            event.job_id,
        )
        if not match:
            return

        plan_id, task_id = map(int, match.groups())
        db = SyncSessionLocal()
        try:
            task = db.get(ReleaseTask, task_id)
            if (
                not task
                or task.plan_id != plan_id
                or task.status != "WAITING"
            ):
                return

            task.status = "FAILED"
            task.error_message = (
                "计划执行时间已错过，超过 300 秒容错窗口"
            )
            task.finished_at = datetime.now()
            db.commit()

            from app.services.release_service import (
                handle_pipeline_failure,
            )
            handle_pipeline_failure(db, plan_id, task_id)
        finally:
            db.close()

    def start(self):
        if not self.scheduler.running:
            self.scheduler.start()
            logger.info("APScheduler started successfully with SQLAlchemyJobStore.")
            
            # Register interval job to sync external builds
            from app.services.jenkins_sync_task import sync_external_builds
            self.scheduler.add_job(
                sync_external_builds,
                'cron',
                hour=18,
                minute=0,
                id="sync_external_builds",
                replace_existing=True
            )
            logger.info("Registered sync_external_builds daily 18:00 cron task.")
            
            # Register interval job to reconcile running release tasks (every 10 seconds)
            from app.services.release_service import reconcile_running_tasks
            self.scheduler.add_job(
                reconcile_running_tasks,
                'interval',
                seconds=10,
                id="reconcile_running_tasks",
                replace_existing=True
            )
            logger.info("Registered reconcile_running_tasks background task (10s interval).")
            
            # Register cron job to clean up expired data daily at 3:00 AM
            from app.services.data_cleaner import clean_expired_data
            self.scheduler.add_job(
                clean_expired_data,
                'cron',
                hour=3,
                minute=0,
                id="clean_expired_data",
                replace_existing=True
            )
            logger.info("Registered clean_expired_data background task.")

            # Register cron job to auto backup active Jenkins servers daily at 2:00 AM
            from app.services.jenkins_backup_service import auto_backup_active_jenkins_servers
            self.scheduler.add_job(
                auto_backup_active_jenkins_servers,
                'cron',
                hour=2,
                minute=0,
                id="auto_backup_jenkins_servers",
                replace_existing=True
            )
            logger.info("Registered auto_backup_jenkins_servers daily 02:00 cron task.")

            # Register/reload daily auto sync Views/Jobs task
            self.reload_jenkins_auto_sync_job()

    def reload_jenkins_auto_sync_job(self):
        """Reload daily auto-sync for Jenkins servers View/Job metadata based on SystemConfig."""
        db = SyncSessionLocal()
        try:
            from app.models.system import SystemConfig
            from app.services.jenkins_sync_task import sync_all_active_jenkins_servers

            enabled_config = db.query(SystemConfig).filter(SystemConfig.config_key == "jenkins_auto_sync_enabled").first()
            time_config = db.query(SystemConfig).filter(SystemConfig.config_key == "jenkins_auto_sync_time").first()

            is_enabled = enabled_config.config_value == "1" if enabled_config else True
            sync_time_str = time_config.config_value if time_config and time_config.config_value else "08:00"

            job_id = "auto_sync_jenkins_data"

            if is_enabled:
                try:
                    hour, minute = map(int, sync_time_str.strip().split(":"))
                    self.scheduler.add_job(
                        sync_all_active_jenkins_servers,
                        'cron',
                        hour=hour,
                        minute=minute,
                        id=job_id,
                        replace_existing=True
                    )
                    logger.info(f"Registered auto_sync_jenkins_data daily {sync_time_str} cron task.")
                except Exception as e:
                    logger.error(f"Failed to parse or add auto_sync_jenkins_data cron job ({sync_time_str}): {e}")
            else:
                if self.scheduler.get_job(job_id):
                    self.scheduler.remove_job(job_id)
                    logger.info("Removed auto_sync_jenkins_data cron task (disabled).")
        except Exception as e:
            logger.error(f"Error reloading jenkins auto sync job: {e}")
        finally:
            db.close()

    def shutdown(self):
        if self.scheduler.running:
            self.scheduler.shutdown()
            logger.info("APScheduler shutdown.")

    def add_release_job(self, plan_id: int, task_id: int, execute_time: datetime, func, *args, **kwargs):
        """
        Dynamically registers a one-time (date trigger) task with APScheduler.
        The job ID is formatted as plan_{plan_id}_task_{task_id} to bind directly with database.
        """
        job_id = f"plan_{plan_id}_task_{task_id}"
        
        # Remove any pre-existing job with the same ID to prevent duplicates
        self.remove_release_job(plan_id, task_id)
        
        # We specify misfire_grace_time of 300 seconds (5 minutes) so that if the scheduler is down
        # and starts up within 5 mins of the scheduled time, it still triggers the release.
        self.scheduler.add_job(
            func,
            'date',
            run_date=execute_time,
            args=args,
            kwargs=kwargs,
            id=job_id,
            misfire_grace_time=300
        )
        logger.info(f"Successfully scheduled job {job_id} for target execution time: {execute_time}")

    def remove_release_job(self, plan_id: int, task_id: int):
        """
        Deregister a scheduled job from the queue and job store database.
        """
        job_id = f"plan_{plan_id}_task_{task_id}"
        try:
            if self.scheduler.get_job(job_id):
                self.scheduler.remove_job(job_id)
                logger.info(f"Cancelled and removed scheduled job: {job_id}")
        except Exception as e:
            logger.error(f"Error removing scheduled job {job_id}: {str(e)}")

# Instantiate the global scheduler manager
scheduler_manager = SchedulerManager()
