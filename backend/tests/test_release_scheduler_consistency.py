from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from fastapi import BackgroundTasks, HTTPException, Request
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.release import create_plan, update_plan
from app.core.database import Base
from app.models.jenkins import JenkinsJob, JenkinsServer
from app.models.release import ReleasePlan, ReleaseTask
from app.models.user import User
from app.schemas.release import ReleasePlanCreate, ReleaseTaskCreate
from app.services.scheduler import scheduler_manager
import app.api.release as release_api


def request(method="POST", path="/plans"):
    return Request({
        "type": "http",
        "method": method,
        "path": path,
        "headers": [],
        "query_string": b"",
        "scheme": "http",
        "server": ("testserver", 80),
        "client": ("127.0.0.1", 12345),
    })


def release_session():
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
    old_job = JenkinsJob(server_id=server.id, name="old-deploy")
    new_job = JenkinsJob(server_id=server.id, name="new-deploy")
    db.add_all([old_job, new_job])
    db.commit()
    return db, user, server, old_job, new_job


def scheduled_input(job, name="scheduled release"):
    return ReleasePlanCreate(
        name=name,
        type="SCHEDULED",
        execute_time=datetime.now() + timedelta(minutes=10),
        tasks=[ReleaseTaskCreate(
            server_id=job.server_id,
            job_id=job.id,
            job_name=job.name,
            branch="main",
            sequence=0,
        )],
    )


def test_create_removes_persisted_plan_when_scheduler_registration_fails():
    db, user, _server, _old_job, new_job = release_session()

    def pass_preflight(session, plan, revision=None):
        plan.preflight_status = "PASSED"
        session.commit()
        return plan

    with (
        patch("app.api.release.validate_release_plan_input"),
        patch("app.api.release.run_release_preflight", pass_preflight),
        patch.object(scheduler_manager, "add_release_job", side_effect=RuntimeError("scheduler down")),
        patch.object(scheduler_manager, "remove_release_job") as remove_job,
    ):
        with pytest.raises(HTTPException) as error:
            create_plan(request(), scheduled_input(new_job), BackgroundTasks(), db, user)

    assert error.value.status_code == 503
    assert db.query(ReleasePlan).count() == 0
    assert db.query(ReleaseTask).count() == 0
    remove_job.assert_called()


def test_update_rolls_back_database_and_restores_old_schedule_on_failure():
    db, user, server, old_job, new_job = release_session()
    old_time = datetime.now() + timedelta(minutes=20)
    plan = ReleasePlan(
        name="original",
        type="SCHEDULED",
        execute_time=old_time,
        interval_minutes=0,
        pipeline_failure_strategy="STOP",
        status="WAITING",
        creator_id=user.id,
    )
    db.add(plan)
    db.flush()
    old_task = ReleaseTask(
        plan_id=plan.id,
        server_id=server.id,
        job_id=old_job.id,
        job_name=old_job.name,
        branch="main",
        sequence=0,
        status="WAITING",
        scheduled_time=old_time,
    )
    db.add(old_task)
    db.commit()
    old_task_id = old_task.id

    def pass_preflight(session, current, revision=None):
        current.preflight_status = "PASSED"
        session.commit()
        return current

    with (
        patch("app.api.release.validate_release_plan_input"),
        patch("app.api.release.run_release_preflight", pass_preflight),
        patch.object(scheduler_manager, "remove_release_job") as remove_job,
        patch.object(
            scheduler_manager,
            "add_release_job",
            side_effect=[RuntimeError("scheduler down"), None],
        ) as add_job,
    ):
        with pytest.raises(HTTPException) as error:
            update_plan(
                request("PUT", f"/plans/{plan.id}"),
                plan.id,
                scheduled_input(new_job, "replacement"),
                db,
                user,
            )

    assert error.value.status_code == 503
    restored = db.get(ReleasePlan, plan.id)
    db.refresh(restored)
    assert restored.name == "original"
    assert len(restored.tasks) == 1
    assert restored.tasks[0].id == old_task_id
    assert restored.tasks[0].job_name == "old-deploy"
    assert add_job.call_count == 2
    restored_call = add_job.call_args_list[-1]
    assert restored_call.args[0:3] == (plan.id, old_task_id, old_time)
    remove_job.assert_called()


def test_update_does_not_restore_old_data_after_concurrent_execution_claim():
    db, user, server, old_job, new_job = release_session()
    old_time = datetime.now() + timedelta(minutes=20)
    plan = ReleasePlan(
        name="original",
        type="SCHEDULED",
        execute_time=old_time,
        interval_minutes=0,
        pipeline_failure_strategy="STOP",
        status="WAITING",
        creator_id=user.id,
        preflight_status="PASSED",
    )
    db.add(plan)
    db.flush()
    db.add(ReleaseTask(
        plan_id=plan.id,
        server_id=server.id,
        job_id=old_job.id,
        job_name=old_job.name,
        branch="main",
        sequence=0,
        status="WAITING",
        scheduled_time=old_time,
    ))
    db.commit()

    def pass_preflight(session, current, revision=None):
        current.preflight_status = "PASSED"
        session.commit()
        return current

    def claim_then_fail(*args, **kwargs):
        current = db.get(ReleasePlan, plan.id)
        current.status = "RUNNING"
        db.commit()
        raise RuntimeError("scheduler down")

    with (
        patch("app.api.release.validate_release_plan_input"),
        patch("app.api.release.run_release_preflight", pass_preflight),
        patch.object(scheduler_manager, "remove_release_job"),
        patch.object(scheduler_manager, "add_release_job", side_effect=claim_then_fail),
    ):
        with pytest.raises(HTTPException) as error:
            update_plan(
                request("PUT", f"/plans/{plan.id}"),
                plan.id,
                scheduled_input(new_job, "replacement"),
                db,
                user,
            )

    assert error.value.status_code == 409
    current = db.get(ReleasePlan, plan.id)
    db.refresh(current)
    assert current.status == "RUNNING"
    assert current.name == "replacement"
    assert current.tasks[0].job_name == "new-deploy"


def test_schedule_recovery_claim_is_conditional_and_advances_revision():
    db, user, server, old_job, _new_job = release_session()
    plan = ReleasePlan(
        name="scheduled",
        type="SCHEDULED",
        execute_time=datetime.now() + timedelta(minutes=10),
        status="WAITING",
        creator_id=user.id,
        preflight_status="PASSED",
        preflight_revision=3,
    )
    db.add(plan)
    db.commit()

    assert release_api._claim_schedule_recovery(db, plan.id, 3) == 4
    db.rollback()

    task = ReleaseTask(
        plan_id=plan.id,
        server_id=server.id,
        job_id=old_job.id,
        job_name=old_job.name,
        branch="main",
        status="RUNNING",
    )
    db.add(task)
    db.commit()
    assert release_api._claim_schedule_recovery(db, plan.id, 3) is None
    db.rollback()

    task.status = "WAITING"
    plan.status = "RUNNING"
    db.commit()
    assert release_api._claim_schedule_recovery(db, plan.id, 3) is None


def test_scheduled_create_keeps_saved_plan_when_preflight_crashes():
    db, user, _server, _old_job, new_job = release_session()

    with (
        patch("app.api.release.validate_release_plan_input"),
        patch("app.api.release.run_release_preflight", side_effect=RuntimeError("secret")),
        patch.object(scheduler_manager, "add_release_job") as add_job,
    ):
        result = create_plan(
            request(), scheduled_input(new_job), BackgroundTasks(), db, user
        )

    assert result.preflight_status == "FAILED"
    assert result.status == "WAITING"
    assert result.tasks[0].job_name == "new-deploy"
    add_job.assert_called_once()


def test_update_keeps_replacement_when_preflight_crashes():
    db, user, server, old_job, new_job = release_session()
    plan = ReleasePlan(
        name="original",
        type="SCHEDULED",
        execute_time=datetime.now() + timedelta(minutes=20),
        status="WAITING",
        creator_id=user.id,
    )
    db.add(plan)
    db.flush()
    db.add(ReleaseTask(
        plan_id=plan.id,
        server_id=server.id,
        job_id=old_job.id,
        job_name=old_job.name,
        branch="main",
        status="WAITING",
    ))
    db.commit()

    with (
        patch("app.api.release.validate_release_plan_input"),
        patch("app.api.release.run_release_preflight", side_effect=RuntimeError("secret")),
        patch.object(scheduler_manager, "remove_release_job"),
        patch.object(scheduler_manager, "add_release_job") as add_job,
    ):
        result = update_plan(
            request("PUT", f"/plans/{plan.id}"),
            plan.id,
            scheduled_input(new_job, "replacement"),
            db,
            user,
        )

    assert result.name == "replacement"
    assert result.preflight_status == "FAILED"
    assert result.tasks[0].job_name == "new-deploy"
    add_job.assert_called_once()


def test_update_rejects_task_claimed_after_plan_read():
    db, user, server, old_job, new_job = release_session()
    plan = ReleasePlan(
        name="original",
        type="SCHEDULED",
        execute_time=datetime.now() + timedelta(minutes=20),
        status="WAITING",
        creator_id=user.id,
        preflight_status="PASSED",
    )
    db.add(plan)
    db.flush()
    task = ReleaseTask(
        plan_id=plan.id,
        server_id=server.id,
        job_id=old_job.id,
        job_name=old_job.name,
        branch="main",
        status="RUNNING",
    )
    db.add(task)
    db.commit()

    with (
        patch("app.api.release.validate_release_plan_input"),
        patch.object(scheduler_manager, "remove_release_job") as remove_job,
    ):
        with pytest.raises(HTTPException) as error:
            update_plan(
                request("PUT", f"/plans/{plan.id}"),
                plan.id,
                scheduled_input(new_job, "replacement"),
                db,
                user,
            )

    assert error.value.status_code == 409
    assert db.get(ReleaseTask, task.id).status == "RUNNING"
    remove_job.assert_not_called()


def test_update_removes_old_schedule_after_database_commit():
    db, user, server, old_job, new_job = release_session()
    plan = ReleasePlan(
        name="original",
        type="SCHEDULED",
        execute_time=datetime.now() + timedelta(minutes=20),
        status="WAITING",
        creator_id=user.id,
    )
    db.add(plan)
    db.flush()
    old_task = ReleaseTask(
        plan_id=plan.id,
        server_id=server.id,
        job_id=old_job.id,
        job_name=old_job.name,
        branch="main",
        status="WAITING",
    )
    db.add(old_task)
    db.commit()
    old_task_id = old_task.id

    def pass_preflight(session, current, revision=None):
        current.preflight_status = "PASSED"
        session.commit()
        return current

    def remove_without_write_lock(*args):
        assert not db.in_transaction()

    with (
        patch("app.api.release.validate_release_plan_input"),
        patch("app.api.release.run_release_preflight", pass_preflight),
        patch.object(scheduler_manager, "remove_release_job", side_effect=remove_without_write_lock),
        patch.object(scheduler_manager, "add_release_job"),
    ):
        result = update_plan(
            request("PUT", f"/plans/{plan.id}"),
            plan.id,
            scheduled_input(new_job, "replacement"),
            db,
            user,
        )

    assert result.tasks[0].id != old_task_id
