from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.release import validate_release_plan_input
from app.core.database import Base
from app.models.jenkins import JenkinsServer
from app.models.release import ReleasePlan, ReleaseTask
from app.models.user import User
from app.schemas.release import ReleasePlanCreate, ReleaseTaskCreate
from app.services.jenkins_client import JenkinsClient
from app.services.release_service import handle_pipeline_failure, handle_pipeline_success


def pipeline_input(dependencies):
    return ReleasePlanCreate(
        name="pipeline",
        type="PIPELINE",
        execute_time=datetime.now() + timedelta(minutes=10),
        tasks=[
            ReleaseTaskCreate(
                server_id=1,
                job_id=index + 1,
                job_name="deploy",
                branch="main",
                sequence=index,
                depends_on_sequence=dependency,
            )
            for index, dependency in enumerate(dependencies)
        ],
    )


def validation_db():
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = SimpleNamespace(name="deploy")
    db.get.return_value = SimpleNamespace(
        url="http://jenkins.example",
        username="admin",
        api_token="token",
        is_active=True,
    )
    return db


def test_pipeline_requires_each_step_to_depend_on_previous_sequence():
    with patch.object(JenkinsClient, "get_job_parameters", return_value=[]):
        with pytest.raises(HTTPException) as error:
            validate_release_plan_input(validation_db(), pipeline_input([None, None]))

    assert error.value.status_code == 400
    assert "前一个任务" in error.value.detail


def test_pipeline_accepts_strict_serial_dependency_chain():
    with patch.object(JenkinsClient, "get_job_parameters", side_effect=AssertionError("network call")) as remote:
        validate_release_plan_input(validation_db(), pipeline_input([None, 0, 1]))

    remote.assert_not_called()


def pipeline_session(failure_strategy="STOP"):
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
        name="legacy branching pipeline",
        type="PIPELINE",
        status="RUNNING",
        pipeline_failure_strategy=failure_strategy,
        creator_id=user.id,
    )
    db.add(plan)
    db.flush()

    parent = ReleaseTask(
        plan_id=plan.id,
        server_id=server.id,
        job_name="parent",
        branch="main",
        sequence=0,
        status="SUCCESS" if failure_strategy == "STOP" else "FAILED",
    )
    db.add(parent)
    db.flush()
    db.add_all([
        ReleaseTask(
            plan_id=plan.id,
            server_id=server.id,
            job_name=f"child-{index}",
            branch="main",
            sequence=index,
            depends_on_task_id=parent.id,
            status="WAITING",
        )
        for index in (1, 2)
    ])
    db.commit()
    return db, plan, parent


def test_pipeline_success_starts_all_waiting_legacy_dependants():
    db, plan, parent = pipeline_session()

    with patch("app.services.release_service.threading.Thread") as thread_class:
        handle_pipeline_success(db, plan.id, parent.id)

    assert thread_class.call_count == 2
    assert thread_class.return_value.start.call_count == 2


def test_pipeline_continue_starts_all_waiting_legacy_dependants():
    db, plan, parent = pipeline_session("CONTINUE")

    with patch("app.services.release_service.threading.Thread") as thread_class:
        handle_pipeline_failure(db, plan.id, parent.id)

    assert thread_class.call_count == 2
    assert thread_class.return_value.start.call_count == 2


def test_retry_single_task_resets_downstream_skipped_tasks():
    from fastapi import Request
    from app.api.release import retry_single_task
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()

    user = User(username="op", password_hash="x", role="operator")
    server = JenkinsServer(name="j", url="http://j.example", username="u", api_token="t")
    db.add_all([user, server])
    db.flush()

    plan = ReleasePlan(name="pipeline", type="PIPELINE", status="FAILED", preflight_status="PASSED", creator_id=user.id)
    db.add(plan)
    db.flush()

    t1 = ReleaseTask(plan_id=plan.id, server_id=server.id, job_name="step1", branch="main", sequence=0, status="FAILED", error_message="failed")
    t2 = ReleaseTask(plan_id=plan.id, server_id=server.id, job_name="step2", branch="main", sequence=1, status="SKIPPED")
    t3 = ReleaseTask(plan_id=plan.id, server_id=server.id, job_name="step3", branch="main", sequence=2, status="SKIPPED")
    db.add_all([t1, t2, t3])
    db.commit()

    req = Request({
        "type": "http", "method": "POST", "path": f"/plans/{plan.id}/tasks/{t1.id}/retry",
        "headers": [], "query_string": b"", "scheme": "http", "server": ("testserver", 80), "client": ("127.0.0.1", 12345)
    })
    bg_tasks = MagicMock()
    with patch("app.api.release.log_action"):
        retry_single_task(req, plan.id, t1.id, bg_tasks, db, user)

    db.expire_all()
    statuses = [t.status for t in db.query(ReleaseTask).filter_by(plan_id=plan.id).order_by(ReleaseTask.sequence).all()]
    assert statuses == ["WAITING", "WAITING", "WAITING"]
    assert db.get(ReleasePlan, plan.id).status == "RUNNING"
    db.close()
