import json
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
import requests
from fastapi import BackgroundTasks, HTTPException, Request
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

from app.api import release as release_api
from app.core.database import Base
from app.models.jenkins import JenkinsJob, JenkinsServer
from app.models.release import ReleasePlan, ReleaseTask
from app.models.user import User
from app.schemas.release import ReleasePlanCreate, ReleasePlanResponse, ReleaseTaskCreate
from app.services.init_db import ensure_release_plan_preflight_columns
from app.services import release_preflight, release_service
from app.services.release_preflight import aggregate_status, preflight_block_reason, url_origin
from app.services.scheduler import scheduler_manager


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://jenkins.example/path", ("https", "jenkins.example", 443)),
        ("HTTP://JENKINS.EXAMPLE", ("http", "jenkins.example", 80)),
        ("http://jenkins.example:8080/path", ("http", "jenkins.example", 8080)),
    ],
)
def test_url_origin_normalizes_http_origins(url, expected):
    assert url_origin(url) == expected


@pytest.mark.parametrize("url", ["ftp://jenkins.example", "https:///missing-host"])
def test_url_origin_rejects_invalid_origins(url):
    with pytest.raises(ValueError, match="Origin"):
        url_origin(url)


def test_aggregate_status_uses_highest_severity():
    assert aggregate_status([]) == "PASSED"
    assert aggregate_status(["PASSED", "WARNING"]) == "WARNING"
    assert aggregate_status(["WARNING", "FAILED", "PASSED"]) == "FAILED"


@pytest.mark.parametrize(
    ("status", "blocked"),
    [("UNCHECKED", True), ("FAILED", True), ("UNKNOWN", True), ("PASSED", False), ("WARNING", False)],
)
def test_preflight_block_reason_only_allows_passed_and_warning(status, blocked):
    reason = preflight_block_reason(ReleasePlan(preflight_status=status))
    assert bool(reason) is blocked
    if reason:
        assert len(reason) <= 20


def test_preflight_block_reason_fails_closed_for_unknown_status():
    assert preflight_block_reason(ReleasePlan(preflight_status="UNKNOWN")) == "发布前检查状态异常"


class FakeResponse:
    def __init__(self, status_code=200, payload=None, headers=None):
        self.status_code = status_code
        self.payload = {} if payload is None else payload
        self.headers = headers or {}

    def json(self):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def persist_plan(db, jobs=("deploy",), parameters=None, active=1, server_url="http://jenkins.example/"):
    user = User(username="tester", password_hash="hash")
    server = JenkinsServer(
        name="jenkins",
        url=server_url,
        username="ci",
        api_token="encrypted",
        is_active=active,
    )
    db.add_all([user, server])
    db.flush()
    plan = ReleasePlan(
        name="deploy",
        type="IMMEDIATE",
        interval_minutes=0,
        pipeline_failure_strategy="STOP",
        status="WAITING",
        creator_id=user.id,
    )
    db.add(plan)
    db.flush()
    for sequence, job_name in enumerate(jobs):
        job = JenkinsJob(server_id=server.id, name=job_name)
        db.add(job)
        db.flush()
        db.add(
            ReleaseTask(
                plan_id=plan.id,
                server_id=server.id,
                job_id=job.id,
                job_name=job_name,
                branch="main",
                parameters=(parameters or {}).get(job_name, {}),
                sequence=sequence,
            )
        )
    db.commit()
    db.refresh(plan)
    return plan, server


@pytest.mark.parametrize(
    ("preflight_status", "plan_status"),
    [("FAILED", "WAITING"), ("UNCHECKED", "RUNNING")],
)
def test_execute_release_task_blocks_invalid_persisted_preflight(
    db, monkeypatch, preflight_status, plan_status
):
    plan, _ = persist_plan(db)
    task = plan.tasks[0]
    plan.preflight_status = preflight_status
    plan.status = plan_status
    db.commit()
    monkeypatch.setattr(release_service, "SyncSessionLocal", lambda: Session(db.bind))

    with patch.object(release_service.threading, "Thread") as thread:
        release_service.execute_release_task(plan.id, task.id)

    db.expire_all()
    blocked_task = db.get(ReleaseTask, task.id)
    thread.assert_not_called()
    assert blocked_task.status == "FAILED"
    assert blocked_task.error_message == preflight_block_reason(db.get(ReleasePlan, plan.id))
    assert blocked_task.finished_at is not None
    assert db.get(ReleasePlan, plan.id).status == "FAILED"


def test_execute_release_task_blocks_every_waiting_pipeline_task(db, monkeypatch):
    plan, _ = persist_plan(db, jobs=("root", "dependent"))
    root, dependent = sorted(plan.tasks, key=lambda task: task.sequence)
    plan.type = "PIPELINE"
    plan.preflight_status = "FAILED"
    dependent.depends_on_task_id = root.id
    db.commit()
    monkeypatch.setattr(release_service, "SyncSessionLocal", lambda: Session(db.bind))

    with patch.object(release_service.threading, "Thread") as thread:
        release_service.execute_release_task(plan.id, root.id)

    db.expire_all()
    tasks = db.query(ReleaseTask).filter(ReleaseTask.plan_id == plan.id).all()
    thread.assert_not_called()
    assert {task.status for task in tasks} == {"FAILED"}
    assert {task.error_message for task in tasks} == {preflight_block_reason(plan)}
    assert all(task.finished_at is not None for task in tasks)


@pytest.mark.parametrize("preflight_status", ["PASSED", "WARNING"])
def test_execute_release_task_allows_valid_persisted_preflight(
    db, monkeypatch, preflight_status
):
    plan, _ = persist_plan(db)
    task = plan.tasks[0]
    plan.preflight_status = preflight_status
    db.commit()
    monkeypatch.setattr(release_service, "SyncSessionLocal", lambda: Session(db.bind))

    with patch.object(release_service.threading, "Thread") as thread:
        release_service.execute_release_task(plan.id, task.id)

    thread.assert_called_once_with(
        target=release_service.execute_task_workflow, args=(plan.id, task.id)
    )
    thread.return_value.start.assert_called_once_with()


@pytest.mark.parametrize("case", ["missing_plan", "other_plan"])
def test_execute_release_task_checks_ownership_before_mutation(db, monkeypatch, case):
    plan, _ = persist_plan(db)
    task = plan.tasks[0]
    if case == "other_plan":
        other_plan = ReleasePlan(
            name="other",
            type="IMMEDIATE",
            interval_minutes=0,
            pipeline_failure_strategy="STOP",
            status="WAITING",
            creator_id=plan.creator_id,
            preflight_status="FAILED",
        )
        db.add(other_plan)
        db.commit()
        plan_id = other_plan.id
    else:
        plan_id = 999
    monkeypatch.setattr(release_service, "SyncSessionLocal", lambda: Session(db.bind))

    with patch.object(release_service.threading, "Thread") as thread:
        release_service.execute_release_task(plan_id, task.id)

    db.expire_all()
    unchanged_task = db.get(ReleaseTask, task.id)
    thread.assert_not_called()
    assert unchanged_task.status == "WAITING"
    assert unchanged_task.error_message is None
    assert unchanged_task.finished_at is None


@pytest.mark.parametrize("case", ["missing_task", "non_waiting"])
def test_execute_release_task_does_not_start_inappropriate_work(db, monkeypatch, case):
    plan, _ = persist_plan(db)
    task = plan.tasks[0]
    plan.preflight_status = "PASSED"
    if case == "non_waiting":
        task.status = "SUCCESS"
    db.commit()
    monkeypatch.setattr(release_service, "SyncSessionLocal", lambda: Session(db.bind))
    task_id = 999 if case == "missing_task" else task.id

    with patch.object(release_service.threading, "Thread") as thread:
        release_service.execute_release_task(plan.id, task_id)

    thread.assert_not_called()


@pytest.mark.parametrize(
    ("preflight_status", "plan_status"),
    [("FAILED", "WAITING"), ("UNCHECKED", "RUNNING")],
)
def test_execute_task_workflow_claim_rechecks_preflight_atomically(
    db, monkeypatch, preflight_status, plan_status
):
    plan, _ = persist_plan(db, jobs=("root", "dependent"))
    task = min(plan.tasks, key=lambda current: current.sequence)
    plan.preflight_status = preflight_status
    plan.status = plan_status
    db.commit()
    monkeypatch.setattr(release_service, "SyncSessionLocal", lambda: Session(db.bind))

    with (
        patch.object(release_service, "send_release_notification") as notification,
        patch.object(
            release_service, "JenkinsClient", side_effect=RuntimeError("must not connect")
        ) as client,
    ):
        release_service.execute_task_workflow(plan.id, task.id)

    db.expire_all()
    notification.assert_not_called()
    client.assert_not_called()
    plan = db.get(ReleasePlan, plan.id)
    assert plan.status == "FAILED"
    assert {current.status for current in plan.tasks} == {"FAILED"}
    assert {current.error_message for current in plan.tasks} == {
        preflight_block_reason(plan)
    }
    assert all(current.finished_at is not None for current in plan.tasks)


def install_client(monkeypatch, responder):
    class FakeJenkinsClient:
        calls = []

        def __init__(self, url, username, token):
            self.base_url = url.rstrip("/") + "/"
            self.session = self

        def get(self, url, **kwargs):
            self.calls.append((url, kwargs))
            response = responder(url)
            if isinstance(response, Exception):
                raise response
            return response

    monkeypatch.setattr(release_preflight, "JenkinsClient", FakeJenkinsClient, raising=False)
    return FakeJenkinsClient


def successful_response(url):
    if url.endswith("api/json?tree=url") and "/job/" not in url:
        return FakeResponse(payload={"url": "http://jenkins.example/"})
    if url == "http://jenkins.example":
        return FakeResponse()
    return FakeResponse(
        payload={
            "name": "deploy",
            "property": [{"parameterDefinitions": [{"name": "ENV"}]}],
            "actions": [{"parameterDefinitions": [{"name": "TAG"}]}],
        }
    )


def api_request(method="POST", path="/plans"):
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


def test_create_runs_preflight_after_plan_and_tasks_are_persisted(db):
    existing, server = persist_plan(db)
    user = existing.creator
    job = db.query(JenkinsJob).filter(JenkinsJob.server_id == server.id).first()
    seen = []

    def fake_preflight(session, plan):
        seen.append((plan.id, [task.id for task in plan.tasks]))
        plan.preflight_status = "PASSED"
        return plan

    with (
        patch.object(release_api, "validate_release_plan_input"),
        patch.object(scheduler_manager, "add_release_job"),
        patch.object(release_api, "run_release_preflight", fake_preflight, create=True),
    ):
        result = release_api.create_plan(
            api_request(), scheduled_input(job), BackgroundTasks(), db, user
        )

    assert seen == [(result.id, [result.tasks[0].id])]
    assert result.preflight_status == "PASSED"


def test_immediate_create_persists_failure_when_preflight_crashes(db):
    existing, server = persist_plan(db)
    job = db.query(JenkinsJob).filter(JenkinsJob.server_id == server.id).first()
    plan_in = ReleasePlanCreate(
        name="crashing immediate",
        type="IMMEDIATE",
        tasks=[ReleaseTaskCreate(
            server_id=server.id,
            job_id=job.id,
            job_name=job.name,
            branch="main",
        )],
    )

    with (
        patch.object(release_api, "validate_release_plan_input"),
        patch.object(
            release_api,
            "run_release_preflight",
            side_effect=RuntimeError("secret-token"),
        ),
    ):
        with pytest.raises(RuntimeError, match="secret-token"):
            release_api.create_plan(
                api_request(), plan_in, BackgroundTasks(), db, existing.creator
            )

    plan = db.query(ReleasePlan).filter_by(name=plan_in.name).one()
    assert plan.status == "FAILED"
    assert all(task.status == "FAILED" for task in plan.tasks)
    assert all(task.finished_at is not None for task in plan.tasks)
    assert all(task.error_message == "发布前检查异常" for task in plan.tasks)
    assert "secret-token" not in plan.tasks[0].error_message


def test_update_resets_and_runs_preflight_after_scheduler_success(db):
    plan, server = persist_plan(db)
    user = plan.creator
    job = db.query(JenkinsJob).filter(JenkinsJob.server_id == server.id).first()
    plan.preflight_status = "FAILED"
    plan.preflight_checked_at = datetime.now()
    plan.preflight_result = {"stale": True}
    db.commit()
    seen = []

    def fake_preflight(session, updated):
        assert updated.preflight_status == "UNCHECKED"
        assert updated.preflight_checked_at is None
        assert updated.preflight_result is None
        seen.append(updated.id)
        updated.preflight_status = "PASSED"
        return updated

    with (
        patch.object(release_api, "validate_release_plan_input"),
        patch.object(scheduler_manager, "remove_release_job"),
        patch.object(scheduler_manager, "add_release_job"),
        patch.object(release_api, "run_release_preflight", fake_preflight, create=True),
    ):
        result = release_api.update_plan(
            api_request("PUT", f"/plans/{plan.id}"),
            plan.id,
            scheduled_input(job, "replacement"),
            db,
            user,
        )

    assert seen == [plan.id]
    assert result.preflight_status == "PASSED"


def test_manual_preflight_returns_result_and_missing_plan_is_404(db):
    plan, _ = persist_plan(db)
    plan.preflight_status = "WARNING"
    plan.preflight_result = {"summary": "persisted"}
    db.commit()

    with patch.object(
        release_api, "run_release_preflight", return_value=plan, create=True
    ) as preflight:
        result = release_api.preflight_plan(plan.id, db, plan.creator)

    assert result.preflight_result == {"summary": "persisted"}
    preflight.assert_called_once_with(db, result)

    with pytest.raises(HTTPException) as error:
        release_api.preflight_plan(999, db, plan.creator)
    assert error.value.status_code == 404
    assert len(error.value.detail) <= 20


@pytest.mark.parametrize("status", ["UNCHECKED", "FAILED"])
def test_trigger_blocks_failed_preflight_before_scheduler_mutation(db, status):
    plan, _ = persist_plan(db)
    plan.preflight_status = status
    db.commit()

    with patch.object(scheduler_manager, "remove_release_job") as remove_job:
        with pytest.raises(HTTPException) as error:
            release_api.trigger_plan_immediately(
                api_request(path=f"/plans/{plan.id}/trigger"),
                plan.id,
                BackgroundTasks(),
                db,
                plan.creator,
            )

    assert error.value.status_code == 400
    assert error.value.detail == preflight_block_reason(plan)
    remove_job.assert_not_called()
    assert db.get(ReleasePlan, plan.id).status == "WAITING"


def test_trigger_allows_warning_preflight(db):
    plan, _ = persist_plan(db)
    plan.preflight_status = "WARNING"
    db.commit()

    with patch.object(scheduler_manager, "remove_release_job") as remove_job:
        result = release_api.trigger_plan_immediately(
            api_request(path=f"/plans/{plan.id}/trigger"),
            plan.id,
            BackgroundTasks(),
            db,
            plan.creator,
        )

    assert result["success"] is True
    remove_job.assert_called_once()


def test_trigger_atomic_claim_rechecks_preflight_before_side_effects(db):
    plan, _ = persist_plan(db)
    plan.preflight_status = "WARNING"
    db.commit()
    background_tasks = BackgroundTasks()

    def fail_preflight_during_claim(current):
        current.preflight_status = "FAILED"
        db.commit()

    with (
        patch.object(
            release_api,
            "preflight_block_reason",
            side_effect=fail_preflight_during_claim,
        ),
        patch.object(scheduler_manager, "remove_release_job") as remove_job,
    ):
        with pytest.raises(HTTPException) as error:
            release_api.trigger_plan_immediately(
                api_request(path=f"/plans/{plan.id}/trigger"),
                plan.id,
                background_tasks,
                db,
                plan.creator,
            )

    db.refresh(plan)
    assert error.value.status_code == 409
    assert plan.status == "WAITING"
    assert plan.preflight_status == "FAILED"
    remove_job.assert_not_called()
    assert background_tasks.tasks == []


def test_scheduler_failures_do_not_run_preflight(db):
    plan, server = persist_plan(db)
    user = plan.creator
    job = db.query(JenkinsJob).filter(JenkinsJob.server_id == server.id).first()

    with (
        patch.object(release_api, "validate_release_plan_input"),
        patch.object(scheduler_manager, "add_release_job", side_effect=RuntimeError("down")),
        patch.object(scheduler_manager, "remove_release_job"),
        patch.object(release_api, "run_release_preflight", create=True) as preflight,
    ):
        with pytest.raises(HTTPException):
            release_api.create_plan(
                api_request(), scheduled_input(job), BackgroundTasks(), db, user
            )
        assert preflight.call_count == 0

    plan.type = "SCHEDULED"
    plan.execute_time = datetime.now() + timedelta(minutes=20)
    plan.tasks[0].scheduled_time = plan.execute_time
    db.commit()
    with (
        patch.object(release_api, "validate_release_plan_input"),
        patch.object(scheduler_manager, "remove_release_job"),
        patch.object(scheduler_manager, "add_release_job", side_effect=RuntimeError("down")),
        patch.object(release_api, "run_release_preflight", create=True) as preflight,
    ):
        with pytest.raises(HTTPException):
            release_api.update_plan(
                api_request("PUT", f"/plans/{plan.id}"),
                plan.id,
                scheduled_input(job),
                db,
                user,
            )
        assert preflight.call_count == 0


def test_run_release_preflight_passes_and_caches_server_reads(db, monkeypatch):
    plan, _ = persist_plan(
        db,
        jobs=("folder/deploy one", "deploy-two"),
        parameters={"folder/deploy one": {"ENV": "prod"}, "deploy-two": {"TAG": "v1"}},
    )
    client = install_client(monkeypatch, successful_response)

    result = release_preflight.run_release_preflight(db, plan)

    assert result.preflight_status == "PASSED"
    assert result.preflight_checked_at is not None
    assert result.preflight_result["summary"] == "2 个任务，状态 PASSED"
    assert len(result.preflight_result["tasks"]) == 2
    assert all(task["status"] == "PASSED" for task in result.preflight_result["tasks"])
    urls = [url for url, _ in client.calls]
    assert urls.count("http://jenkins.example/api/json?tree=url") == 1
    assert urls.count("http://jenkins.example") == 1
    assert any("job/folder/job/deploy%20one/api/json" in url for url in urls)
    assert all(kwargs == {"timeout": 10, "allow_redirects": False} for _, kwargs in client.calls)


def test_cross_scheme_canonical_redirect_fails_origin(db, monkeypatch):
    plan, _ = persist_plan(db)

    def responder(url):
        if url.endswith("api/json?tree=url"):
            return FakeResponse(payload={"url": "http://jenkins.example/"})
        return FakeResponse(status_code=302, headers={"Location": "https://jenkins.example/"})

    install_client(monkeypatch, responder)
    result = release_preflight.run_release_preflight(db, plan)

    assert result.preflight_status == "FAILED"
    assert result.preflight_result["tasks"][0]["checks"][0]["code"] == "origin"


def test_timeout_is_warning_without_exception_detail(db, monkeypatch):
    plan, _ = persist_plan(db)
    install_client(monkeypatch, lambda url: requests.Timeout("slow secret"))

    result = release_preflight.run_release_preflight(db, plan)

    assert result.preflight_status == "WARNING"
    assert "slow" not in json.dumps(result.preflight_result, ensure_ascii=False)


def test_disabled_server_fails_without_network(db, monkeypatch):
    plan, _ = persist_plan(db, active=0)
    client = install_client(monkeypatch, successful_response)

    result = release_preflight.run_release_preflight(db, plan)

    assert result.preflight_status == "FAILED"
    assert client.calls == []


@pytest.mark.parametrize("status_code", [401, 403])
def test_auth_failure_is_failed(db, monkeypatch, status_code):
    plan, _ = persist_plan(db)

    def responder(url):
        if url.endswith("api/json?tree=url"):
            return FakeResponse(status_code=status_code)
        return FakeResponse()

    install_client(monkeypatch, responder)
    assert release_preflight.run_release_preflight(db, plan).preflight_status == "FAILED"


@pytest.mark.parametrize(("status_code", "expected"), [(404, "FAILED"), (500, "WARNING")])
def test_job_http_failure_status(db, monkeypatch, status_code, expected):
    plan, _ = persist_plan(db)

    def responder(url):
        if "/job/" in url:
            return FakeResponse(status_code=status_code)
        return successful_response(url)

    install_client(monkeypatch, responder)
    assert release_preflight.run_release_preflight(db, plan).preflight_status == expected


def test_empty_job_json_is_failed(db, monkeypatch):
    plan, _ = persist_plan(db)

    def responder(url):
        if "/job/" in url:
            return FakeResponse(payload={})
        return successful_response(url)

    install_client(monkeypatch, responder)
    result = release_preflight.run_release_preflight(db, plan)

    assert result.preflight_status == "FAILED"
    assert result.preflight_result["tasks"][0]["checks"][-1]["code"] == "job"


@pytest.mark.parametrize(
    "error",
    [requests.exceptions.InvalidURL("bad"), requests.exceptions.InvalidSchema("bad")],
)
def test_non_transient_request_error_is_failed(db, monkeypatch, error):
    plan, _ = persist_plan(db)
    install_client(monkeypatch, lambda url: error)

    assert release_preflight.run_release_preflight(db, plan).preflight_status == "FAILED"


def test_invalid_configured_origin_fails_before_network(db, monkeypatch):
    plan, _ = persist_plan(db, server_url="ftp://jenkins.example/")
    client = install_client(monkeypatch, successful_response)

    result = release_preflight.run_release_preflight(db, plan)

    assert result.preflight_status == "FAILED"
    assert result.preflight_result["tasks"][0]["checks"][0]["code"] == "origin"
    assert client.calls == []


def test_unknown_persisted_parameter_fails(db, monkeypatch):
    plan, _ = persist_plan(db, parameters={"deploy": {"MISSING": "x"}})
    install_client(monkeypatch, successful_response)

    result = release_preflight.run_release_preflight(db, plan)

    assert result.preflight_status == "FAILED"
    assert result.preflight_result["tasks"][0]["checks"][-1]["code"] == "parameters"


@pytest.mark.parametrize("root_url", ["not-an-origin", 123])
def test_malformed_root_url_fails_origin(db, monkeypatch, root_url):
    plan, _ = persist_plan(db)

    def responder(url):
        if url.endswith("api/json?tree=url"):
            return FakeResponse(payload={"url": root_url})
        return FakeResponse()

    install_client(monkeypatch, responder)
    result = release_preflight.run_release_preflight(db, plan)

    assert result.preflight_status == "FAILED"
    assert result.preflight_result["tasks"][0]["checks"][0]["code"] == "origin"


def test_mixed_task_status_uses_failed_precedence(db, monkeypatch):
    plan, _ = persist_plan(db, jobs=("warning", "failed"))

    def responder(url):
        if "/job/warning/" in url:
            return FakeResponse(status_code=500)
        if "/job/failed/" in url:
            return FakeResponse(status_code=404)
        return successful_response(url)

    install_client(monkeypatch, responder)
    result = release_preflight.run_release_preflight(db, plan)

    assert [task["status"] for task in result.preflight_result["tasks"]] == ["WARNING", "FAILED"]
    assert result.preflight_status == "FAILED"


def test_second_preflight_replaces_previous_result(db, monkeypatch):
    plan, _ = persist_plan(db)
    state = {"job_status": 200}

    def responder(url):
        if "/job/" in url:
            return FakeResponse(
                status_code=state["job_status"],
                payload={"name": "deploy", "property": [], "actions": []},
            )
        return successful_response(url)

    install_client(monkeypatch, responder)
    first = release_preflight.run_release_preflight(db, plan).preflight_checked_at
    state["job_status"] = 404

    result = release_preflight.run_release_preflight(db, plan)

    assert result.preflight_status == "FAILED"
    assert result.preflight_checked_at >= first
    assert len(result.preflight_result["tasks"]) == 1


def test_upgrade_adds_release_plan_preflight_columns():
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE release_plan (id INTEGER PRIMARY KEY)"))

    ensure_release_plan_preflight_columns(engine)

    columns = {column["name"] for column in inspect(engine).get_columns("release_plan")}
    assert {"preflight_status", "preflight_checked_at", "preflight_result"} <= columns


def test_release_plan_preflight_defaults_are_serializable():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        plan = ReleasePlan(
            name="deploy",
            type="IMMEDIATE",
            interval_minutes=0,
            pipeline_failure_strategy="STOP",
            status="WAITING",
            creator_id=1,
            tasks=[],
        )
        session.add(plan)
        session.flush()
        response = ReleasePlanResponse.model_validate(plan)

    assert response.preflight_status == "UNCHECKED"
    assert response.preflight_checked_at is None
    assert response.preflight_result is None
