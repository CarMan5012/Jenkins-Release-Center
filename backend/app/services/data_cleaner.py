from datetime import datetime, timedelta
from app.core.database import SyncSessionLocal
from app.models.system import SystemConfig, AuditLog
from app.models.release import ReleaseHistory, ReleasePlan, ReleaseTask
from loguru import logger

def clean_expired_data():
    """
    数据保留策略清理逻辑，每天定时自动执行一次。
    清理超期的审计日志、执行历史记录和已结束发布计划。
    """
    logger.info("Executing expired data cleanup task...")
    db = SyncSessionLocal()
    try:
        # 1. 清理安全审计日志
        audit_config = db.query(SystemConfig).filter(SystemConfig.config_key == "audit_log_retention_days").first()
        if audit_config and audit_config.config_value:
            try:
                retention_days = int(audit_config.config_value)
                if retention_days > 0:
                    cutoff_date = datetime.now() - timedelta(days=retention_days)
                    deleted_count = db.query(AuditLog).filter(AuditLog.created_at < cutoff_date).delete()
                    db.commit()
                    if deleted_count > 0:
                        logger.info(f"Cleaned up {deleted_count} expired audit logs (older than {retention_days} days).")
                else:
                    logger.info("Audit log retention policy is set to infinite. Skipping audit log cleanup.")
            except ValueError:
                logger.error(f"Invalid value for audit_log_retention_days: {audit_config.config_value}")
        else:
            logger.info("No audit log retention policy found. Skipping audit log cleanup.")

        # 2. 清理发布执行历史与日志
        history_config = db.query(SystemConfig).filter(SystemConfig.config_key == "history_retention_days").first()
        if history_config and history_config.config_value:
            try:
                retention_days = int(history_config.config_value)
                if retention_days > 0:
                    cutoff_date = datetime.now() - timedelta(days=retention_days)
                    deleted_count = db.query(ReleaseHistory).filter(ReleaseHistory.created_at < cutoff_date).delete()
                    db.commit()
                    if deleted_count > 0:
                        logger.info(f"Cleaned up {deleted_count} expired release histories (older than {retention_days} days).")
                else:
                    logger.info("Release history retention policy is set to infinite. Skipping history cleanup.")
            except ValueError:
                logger.error(f"Invalid value for history_retention_days: {history_config.config_value}")
        else:
            logger.info("No release history retention policy found. Skipping history cleanup.")

        # 3. 清理已结束的发布计划，默认保留 30 天
        plan_config = db.query(SystemConfig).filter(
            SystemConfig.config_key == "plan_retention_days"
        ).first()
        retention_days = int(plan_config.config_value) if plan_config and plan_config.config_value else 30
        if retention_days > 0:
            cutoff_date = datetime.now() - timedelta(days=retention_days)
            expired_plans = db.query(ReleasePlan).filter(
                ReleasePlan.status.in_(["SUCCESS", "FAILED", "CANCELLED"]),
                ReleasePlan.updated_at < cutoff_date,
            ).all()
            if expired_plans:
                plan_ids = [plan.id for plan in expired_plans]
                task_ids = [
                    task_id
                    for task_id, in db.query(ReleaseTask.id).filter(
                        ReleaseTask.plan_id.in_(plan_ids)
                    ).all()
                ]
                db.query(ReleaseHistory).filter(
                    (ReleaseHistory.plan_id.in_(plan_ids))
                    | (ReleaseHistory.task_id.in_(task_ids))
                ).update(
                    {ReleaseHistory.plan_id: None, ReleaseHistory.task_id: None},
                    synchronize_session=False,
                )
                for plan in expired_plans:
                    db.delete(plan)
                db.commit()
                logger.info(f"Cleaned up {len(expired_plans)} expired release plans (older than {retention_days} days).")
        else:
            logger.info("Release plan retention policy is set to infinite. Skipping plan cleanup.")
            
    except Exception as e:
        db.rollback()
        logger.error(f"Error occurred during expired data cleanup: {str(e)}")
    finally:
        db.close()
