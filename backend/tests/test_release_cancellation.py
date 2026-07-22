from datetime import datetime
from unittest.mock import MagicMock, patch

from fastapi import Request
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.release import cancel_plan
from app.core.database import Base
from app.models.jenkins import JenkinsServer
from app.models.release import ReleasePlan, ReleaseTask
from app.models.user import User
from app.services.jenkins_client import JenkinsClient
from app.services.release_service import execute_task_workflow


def request():
    return Request({
        "type": "http",
        "method": "POST",
        "path": "/plans/1/cancel",
        "headers": [],
        "query_string": b"",
        "scheme": "http",
        "server": ("testserver", 80),
        "client": ("127.0.0.1", 12345),
    })


def session_with_running_task():
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

    plan = ReleasePlan(
        name="release",
        type="IMMEDIATE",
        status="RUNNING",
        creator_id=user.id,
    )
    db.add(plan)
    db.flush()

    task = ReleaseTask(
        plan_id=plan.id,
        server_id=server.id,
        job_name="deploy",
        branch="main",
        sequence=0,
        status="RUNNING",
        build_number=42,
        started_at=datetime.now(),
    )
    db.add(task)
    db.commit()
    return db, user, plan, task


@patch("app.api.release.log_action")
@patch("app.api.release.scheduler_manager.remove_release_job")
@patch.object(JenkinsClient, "stop_build")
def test_cancel_running_plan_stops_jenkins_before_marking_cancelled(
    stop_build,
    _remove_job,
    _log_action,
):
    db, user, plan, task = session_with_running_task()

    cancel_plan(request(), plan.id, db, user)

    stop_build.assert_called_once_with("deploy", 42)
    db.refresh(task)
    assert task.status == "CANCELLED"

def test_cancelled_while_queued_stops_build_after_number_is_resolved():
    db, _user, plan, task = session_with_running_task()
    plan.status = "WAITING"
    task.status = "WAITING"
    task.build_number = None
    db.commit()

    client = MagicMock()
    client.trigger_build.return_value = "http://jenkins.example/queue/item/1/"

    def resolve_build_number(_queue_url):
        task.status = "CANCELLED"
        db.commit()
        return 43

    client.get_build_number_from_queue.side_effect = resolve_build_number

    with (
        patch("app.services.release_service.SyncSessionLocal", return_value=db),
        patch("app.services.release_service.JenkinsClient", return_value=client),
        patch("app.services.release_service.send_release_notification"),
    ):
        execute_task_workflow(plan.id, task.id)

    client.stop_build.assert_called_once_with("deploy", 43)
    assert db.get(ReleaseTask, task.id).status == "CANCELLED"
