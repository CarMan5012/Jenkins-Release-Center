import pytest
from datetime import datetime
from unittest.mock import MagicMock, patch

from fastapi import HTTPException, Request
from sqlalchemy import create_engine, update
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
        preflight_status="PASSED",
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
@patch.object(JenkinsClient, "get_recent_builds")
@patch.object(JenkinsClient, "stop_build")
def test_cancel_running_plan_stops_jenkins_before_marking_cancelled(
    stop_build,
    get_recent_builds,
    _remove_job,
    _log_action,
):
    db, user, plan, task = session_with_running_task()

    cancel_plan(request(), plan.id, db, user)

    stop_build.assert_called_once_with("deploy", 42)
    get_recent_builds.assert_not_called()
    db.refresh(task)
    assert task.status == "CANCELLED"

def test_cancelled_while_queued_stops_build_after_number_is_resolved():
    db, _user, plan, task = session_with_running_task()
    plan.status = "WAITING"
    task.status = "WAITING"
    task.build_number = None
    db.commit()

    client = MagicMock()
    client.extract_queue_id.return_value = 1
    client.trigger_build.return_value = "http://jenkins.example/queue/item/1/"
    client.get_build_status.return_value = {
        "building": False,
        "result": "SUCCESS",
        "duration": 1,
    }

    def resolve_build_number(_queue_url, on_poll=None):
        task.status = "CANCELLED"
        db.commit()
        return 43

    client.get_build_number_from_queue.side_effect = resolve_build_number

    with (
        patch("app.services.release_service.SyncSessionLocal", return_value=db),
        patch("app.services.release_service.JenkinsClient", return_value=client),
        patch("app.services.release_service.send_release_notification"),
        patch("app.services.release_service.write_history"),
        patch("app.services.release_service.handle_pipeline_success"),
    ):
        execute_task_workflow(plan.id, task.id)

    client.stop_build.assert_called_once_with("deploy", 43)
    resolved_task = db.get(ReleaseTask, task.id)
    assert resolved_task.status == "CANCELLED"
    assert resolved_task.build_number == 43
    assert resolved_task.jenkins_queue_id == 1


def test_cancelled_during_building_transition_is_not_reactivated():
    db, _user, plan, task = session_with_running_task()
    plan.status = "WAITING"
    task.status = "WAITING"
    task.build_number = None
    db.commit()

    client = MagicMock()
    client.trigger_build.return_value = "http://jenkins.example/queue/item/1/"
    client.extract_queue_id.return_value = 1
    client.get_build_number_from_queue.return_value = 43
    client.get_build_status.return_value = {
        "building": False,
        "result": "SUCCESS",
        "duration": 1,
    }

    real_execute = db.execute
    building_update_seen = False

    def cancel_before_building_update(statement, *args, **kwargs):
        nonlocal building_update_seen
        values = getattr(statement, "_values", {})
        target_status = next(
            (
                getattr(value, "value", None)
                for column, value in values.items()
                if getattr(column, "name", None) == "status"
            ),
            None,
        )
        if target_status == "BUILDING":
            building_update_seen = True
            real_execute(
                update(ReleaseTask)
                .where(ReleaseTask.id == task.id)
                .values(status="CANCELLED")
            )
            db.commit()
        return real_execute(statement, *args, **kwargs)

    db.execute = MagicMock(side_effect=cancel_before_building_update)
    with (
        patch("app.services.release_service.SyncSessionLocal", return_value=db),
        patch("app.services.release_service.JenkinsClient", return_value=client),
        patch("app.services.release_service.send_release_notification"),
        patch("app.services.release_service.write_history"),
        patch("app.services.release_service.handle_pipeline_success"),
    ):
        execute_task_workflow(plan.id, task.id)

    db.expire_all()
    resolved_task = db.get(ReleaseTask, task.id)
    assert building_update_seen is True
    assert resolved_task.status == "CANCELLED"
    assert resolved_task.build_number == 43
    client.stop_build.assert_called_once_with("deploy", 43)



def test_retry_plan_immediately_in_place():
    from app.api.release import retry_plan_immediately
    db, user, plan, task = session_with_running_task()
    plan.status = "FAILED"
    task.status = "FAILED"
    task.jenkins_queue_id = 1001
    task.error_message = "failed"
    task.build_url = "http://jenkins.example/job/deploy/42/"
    task.console_url = "http://jenkins.example/job/deploy/42/console"
    db.commit()
    bg_tasks = MagicMock()
    res = retry_plan_immediately(request(), plan.id, bg_tasks, db, user)
    db.expire_all()
    plan = db.get(ReleasePlan, plan.id)
    task = db.get(ReleaseTask, task.id)
    assert res["success"] is True
    assert res["id"] == plan.id
    assert plan.status == "RUNNING"
    assert task.status == "WAITING"
    assert task.error_message is None
    assert task.finished_at is None
    assert task.jenkins_queue_id is None
    assert task.build_number is None
    assert task.build_url is None
    assert task.console_url is None


def test_retry_single_task_in_place():
    from app.api.release import retry_single_task
    db, user, plan, task = session_with_running_task()
    task.status = "FAILED"
    plan.status = "FAILED"
    task.jenkins_queue_id = 1001
    task.error_message = "failed"
    task.build_url = "http://jenkins.example/job/deploy/42/"
    task.console_url = "http://jenkins.example/job/deploy/42/console"
    db.commit()

    bg_tasks = MagicMock()
    res = retry_single_task(request(), plan.id, task.id, bg_tasks, db, user)
    db.expire_all()
    plan = db.get(ReleasePlan, plan.id)
    task = db.get(ReleaseTask, task.id)
    assert res["success"] is True
    assert res["task_id"] == task.id
    assert task.status == "WAITING"
    assert plan.status == "RUNNING"
    assert task.error_message is None
    assert task.finished_at is None
    assert task.jenkins_queue_id is None
    assert task.build_number is None
    assert task.build_url is None

def test_trigger_plan_clears_previous_jenkins_identity():
    from app.api.release import trigger_plan_immediately

    db, user, plan, task = session_with_running_task()
    plan.status = "FAILED"
    task.status = "FAILED"
    task.jenkins_queue_id = 1001
    task.error_message = "failed"
    task.build_url = "http://jenkins.example/job/deploy/42/"
    task.console_url = "http://jenkins.example/job/deploy/42/console"
    db.commit()

    with (
        patch("app.api.release.scheduler_manager.remove_release_job"),
        patch("app.api.release.log_action"),
    ):
        trigger_plan_immediately(request(), plan.id, MagicMock(), db, user)
    db.expire_all()
    task = db.get(ReleaseTask, task.id)

    assert task.status == "WAITING"
    assert task.jenkins_queue_id is None
    assert task.build_number is None
    assert task.build_url is None
    assert task.console_url is None


def test_preflight_failed_plan_clears_previous_jenkins_identity():
    from app.api.release import preflight_plan

    db, user, plan, task = session_with_running_task()
    plan.status = "FAILED"
    task.status = "FAILED"
    task.jenkins_queue_id = 1001
    task.error_message = "failed"
    task.build_url = "http://jenkins.example/job/deploy/42/"
    task.console_url = "http://jenkins.example/job/deploy/42/console"
    db.commit()

    with patch("app.api.release._run_preflight_safely", return_value=plan):
        preflight_plan(plan.id, db, user)
    db.expire_all()
    task = db.get(ReleaseTask, task.id)

    assert task.status == "WAITING"
    assert task.jenkins_queue_id is None
    assert task.build_number is None
    assert task.build_url is None
    assert task.console_url is None


@patch("app.api.release.log_action")
@patch("app.api.release.scheduler_manager.remove_release_job")
@patch.object(JenkinsClient, "cancel_queue_item")
def test_cancel_queued_plan_cancels_remote_queue(
    cancel_queue,
    _remove_job,
    _log_action,
):
    db, user, plan, task = session_with_running_task()
    task.status = "QUEUED"
    task.jenkins_queue_id = 1001
    task.build_number = None
    db.commit()

    result = cancel_plan(request(), plan.id, db, user)

    cancel_queue.assert_called_once_with(1001)
    assert result["success"] is True
    assert db.get(ReleaseTask, task.id).status == "CANCELLED"


@patch("app.api.release.log_action")
@patch.object(
    JenkinsClient,
    "stop_build",
    side_effect=RuntimeError("Jenkins down"),
)
def test_cancel_failure_keeps_task_active_and_returns_502(
    _stop_build,
    _log_action,
):
    db, user, plan, task = session_with_running_task()

    with pytest.raises(HTTPException) as error:
        cancel_plan(request(), plan.id, db, user)

    assert error.value.status_code == 502
    assert db.get(ReleaseTask, task.id).status == "RUNNING"
    assert db.get(ReleasePlan, plan.id).status == "RUNNING"


@patch("app.api.release.log_action")
@patch("app.api.release.scheduler_manager.remove_release_job")
@patch.object(JenkinsClient, "stop_build")
def test_cancel_partial_failure_keeps_plan_active_and_returns_502(
    stop_build,
    _remove_job,
    _log_action,
):
    db, user, plan, task1 = session_with_running_task()
    task2 = ReleaseTask(
        plan_id=plan.id,
        server_id=task1.server_id,
        job_name="deploy-2",
        branch="main",
        sequence=1,
        status="RUNNING",
        build_number=43,
        started_at=datetime.now(),
    )
    db.add(task2)
    db.commit()

    stop_build.side_effect = [None, RuntimeError("Jenkins node unreachable")]

    with pytest.raises(HTTPException) as error:
        cancel_plan(request(), plan.id, db, user)

    assert error.value.status_code == 502
    assert db.get(ReleaseTask, task1.id).status == "CANCELLED"
    assert db.get(ReleaseTask, task2.id).status == "RUNNING"
    assert db.get(ReleasePlan, plan.id).status == "RUNNING"

