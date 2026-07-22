from datetime import datetime, timedelta
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models.jenkins import JenkinsServer
from app.models.release import ReleaseHistory, ReleasePlan, ReleaseTask
from app.models.system import SystemConfig
from app.models.user import User
from app.services.data_cleaner import clean_expired_data


def retention_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()

    user = User(username="operator", password_hash="x", role="operator")
    server = JenkinsServer(
        name="jenkins",
        url="http://jenkins.example",
        username="admin",
        api_token="token",
    )
    db.add_all([user, server])
    db.flush()
    return db, user, server


def add_plan(db, user, status, updated_at):
    plan = ReleasePlan(
        name=f"{status.lower()} plan",
        type="SCHEDULED",
        status=status,
        creator_id=user.id,
        created_at=updated_at,
        updated_at=updated_at,
    )
    db.add(plan)
    db.flush()
    return plan


def test_cleanup_removes_only_expired_terminal_plans_and_preserves_history():
    db, user, server = retention_session()
    expired_at = datetime.now() - timedelta(days=31)
    recent_at = datetime.now() - timedelta(days=29)

    expired_success = add_plan(db, user, "SUCCESS", expired_at)
    expired_failed = add_plan(db, user, "FAILED", expired_at)
    expired_cancelled = add_plan(db, user, "CANCELLED", expired_at)
    expired_waiting = add_plan(db, user, "WAITING", expired_at)
    expired_running = add_plan(db, user, "RUNNING", expired_at)
    recent_success = add_plan(db, user, "SUCCESS", recent_at)

    task = ReleaseTask(
        plan_id=expired_success.id,
        server_id=server.id,
        job_name="deploy",
        branch="main",
        sequence=0,
        status="SUCCESS",
    )
    db.add(task)
    db.flush()
    history = ReleaseHistory(
        task_id=task.id,
        plan_id=expired_success.id,
        server_name=server.name,
        job_name=task.job_name,
        branch=task.branch,
        status="SUCCESS",
    )
    db.add(history)
    db.commit()

    ids = {
        "success": expired_success.id,
        "failed": expired_failed.id,
        "cancelled": expired_cancelled.id,
        "waiting": expired_waiting.id,
        "running": expired_running.id,
        "recent": recent_success.id,
        "task": task.id,
        "history": history.id,
    }

    with patch("app.services.data_cleaner.SyncSessionLocal", return_value=db):
        clean_expired_data()

    assert db.get(ReleasePlan, ids["success"]) is None
    assert db.get(ReleasePlan, ids["failed"]) is None
    assert db.get(ReleasePlan, ids["cancelled"]) is None
    assert db.get(ReleasePlan, ids["waiting"]) is not None
    assert db.get(ReleasePlan, ids["running"]) is not None
    assert db.get(ReleasePlan, ids["recent"]) is not None
    assert db.get(ReleaseTask, ids["task"]) is None

    preserved_history = db.get(ReleaseHistory, ids["history"])
    assert preserved_history is not None
    assert preserved_history.plan_id is None
    assert preserved_history.task_id is None

def test_zero_plan_retention_keeps_expired_completed_plans():
    db, user, _server = retention_session()
    expired_plan = add_plan(
        db,
        user,
        "SUCCESS",
        datetime.now() - timedelta(days=365),
    )
    db.add(SystemConfig(
        config_key="plan_retention_days",
        config_value="0",
        description="keep forever",
    ))
    db.commit()
    plan_id = expired_plan.id

    with patch("app.services.data_cleaner.SyncSessionLocal", return_value=db):
        clean_expired_data()

    assert db.get(ReleasePlan, plan_id) is not None
