import json

import pytest
import requests
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

from app.core.database import Base
from app.models.jenkins import JenkinsJob, JenkinsServer
from app.models.release import ReleasePlan, ReleaseTask
from app.models.user import User
from app.schemas.release import ReleasePlanResponse
from app.services.init_db import ensure_release_plan_preflight_columns
from app.services import release_preflight
from app.services.release_preflight import aggregate_status, preflight_block_reason, url_origin


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
    [("UNCHECKED", True), ("FAILED", True), ("PASSED", False), ("WARNING", False)],
)
def test_preflight_block_reason_only_blocks_unchecked_and_failed(status, blocked):
    reason = preflight_block_reason(status)
    assert bool(reason) is blocked
    if reason:
        assert len(reason) <= 20


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


def persist_plan(db, jobs=("deploy",), parameters=None, active=1):
    user = User(username="tester", password_hash="hash")
    server = JenkinsServer(
        name="jenkins",
        url="http://jenkins.example/",
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
            return FakeResponse(status_code=state["job_status"], payload={"property": [], "actions": []})
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
