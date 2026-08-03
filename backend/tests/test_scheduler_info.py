from types import SimpleNamespace
from unittest.mock import patch
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models.release import ReleasePlan, ReleaseTask
from app.models.user import User
from app.services.scheduler import scheduler_manager
from app.api.system import get_scheduler_info


def test_handle_missed_job_fails_waiting_task_and_plan():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()

    user = User(username="op", password_hash="x")
    db.add(user)
    db.flush()

    plan = ReleasePlan(id=1, name="missed", type="IMMEDIATE", status="WAITING", preflight_status="PASSED", creator_id=user.id)
    db.add(plan)
    db.flush()

    task = ReleaseTask(id=2, plan_id=1, server_id=1, job_name="deploy", branch="main", sequence=0, status="WAITING")
    db.add(task)
    db.commit()

    event = SimpleNamespace(job_id="plan_1_task_2")
    with patch("app.services.scheduler.SyncSessionLocal", return_value=db):
        scheduler_manager.handle_missed_job(event)

    db.expire_all()
    assert db.get(ReleaseTask, 2).status == "FAILED"
    assert db.get(ReleasePlan, 1).status == "FAILED"
    assert "300" in db.get(ReleaseTask, 2).error_message
    db.close()


def test_scheduler_info_reports_stopped_scheduler():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    user = User(username="admin", password_hash="x", role="admin", is_active=True)
    db.add(user)
    db.commit()

    with patch.object(scheduler_manager, "scheduler", SimpleNamespace(running=False)):
        result = get_scheduler_info(True, db, user)

    assert result["scheduler_summary"]["status"] == "STOPPED"
    assert result["scheduler_summary"]["total_jobs_count"] == 0
    assert all(task["status"] == "已停止" for task in result["system_cron_tasks"])
    db.close()
