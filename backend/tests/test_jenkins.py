import json
import zipfile
import pytest
from fastapi import HTTPException, Request, BackgroundTasks
from unittest.mock import patch, MagicMock
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.deps import get_current_active_admin, get_current_active_operator
from app.core.database import Base
from app.api.jenkins import get_git_branches, read_backup_details, run_job_directly, router
from app.models.jenkins import JenkinsJob, JenkinsServer
from app.models.user import User
from app.services.jenkins_client import JenkinsClient


@patch('requests.Session.get')
def test_jenkins_connection_success(mock_get):
    """
    Assert connection tests pass when Jenkins returns HTTP 200 OK.
    """
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_get.return_value = mock_response

    client = JenkinsClient("http://localhost:8080", "admin", "token123")
    success, message = client.test_connection()
    assert success is True
    assert "连接 Jenkins 成功" in message

@patch('requests.Session.post')
def test_jenkins_trigger_build_with_parameters(mock_post):
    """
    Assert build triggering generates proper Location HTTP header and parameter payloads.
    """
    mock_response = MagicMock()
    mock_response.status_code = 201
    mock_response.headers = {"Location": "http://localhost:8080/queue/item/42/"}
    mock_post.return_value = mock_response

    client = JenkinsClient("http://localhost:8080", "admin", "token123")
    queue_url = client.trigger_build("frontend-deploy", {"ENV": "production", "DEBUG": "false"})

    assert queue_url == "http://localhost:8080/queue/item/42/"
    mock_post.assert_called_once_with(
        "http://localhost:8080/job/frontend-deploy/buildWithParameters",
        data={"ENV": "production", "DEBUG": "false"},
        headers={},
        timeout=10
    )

def test_queue_wait_does_not_expire_while_jenkins_reports_pending(monkeypatch):
    clock = {"now": 0}
    pending = MagicMock(status_code=200)
    pending.json.return_value = {
        "executable": None,
        "why": "Waiting for next available executor",
    }
    started = MagicMock(status_code=200)
    started.json.return_value = {"executable": {"number": 42}}

    client = JenkinsClient("http://localhost:8080", "admin", "token123")
    client.session.get = MagicMock(side_effect=[pending, pending, started])

    fake_time = MagicMock()
    fake_time.time.side_effect = lambda: clock["now"]
    fake_time.sleep.side_effect = lambda seconds: clock.update(now=clock["now"] + 2)
    monkeypatch.setattr("app.services.jenkins_client.time", fake_time)

    assert client.get_build_number_from_queue(
        "http://localhost:8080/queue/item/42/", timeout=3
    ) == 42


@patch.object(JenkinsClient, 'get_crumb_headers', return_value={})
@patch('requests.Session.post')
def test_jenkins_stop_build_posts_to_stop_endpoint(mock_post, mock_crumb):
    mock_response = MagicMock()
    mock_response.status_code = 302
    mock_post.return_value = mock_response

    client = JenkinsClient("http://localhost:8080", "admin", "token123")
    client.stop_build("folder/frontend-deploy", 42)

    mock_post.assert_called_once_with(
        "http://localhost:8080/job/folder/job/frontend-deploy/42/stop",
        headers={},
        timeout=10,
        allow_redirects=False,
    )

@patch('requests.Session.get')
def test_recent_builds_request_branch_parameters(mock_get):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"builds": []}
    mock_get.return_value = mock_response

    client = JenkinsClient("http://localhost:8080", "admin", "token123")
    client.get_recent_builds("folder/frontend-deploy")

    requested_url = mock_get.call_args.args[0]
    assert "parameters[name,value]" in requested_url



def test_read_backup_details_returns_structured_json(tmp_path):
    archive_path = tmp_path / "backup.zip"
    expected = {
        "version": 2,
        "views": [{"name": "Backend", "job_names": ["api-job"]}],
        "jobs": [{"name": "api-job"}],
    }
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("details.json", json.dumps(expected))

    assert read_backup_details(archive_path) == {
        "available": True,
        **expected,
    }


def test_read_backup_details_marks_legacy_archive_unavailable(tmp_path):
    archive_path = tmp_path / "backup.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("summary.md", "# legacy")

    assert read_backup_details(archive_path) == {
        "available": False,
        "version": 0,
        "views": [],
        "jobs": [],
    }


def test_plaintext_backup_routes_require_admin():
    protected_paths = {
        "/servers/{server_id}/backups/{backup_id}/details",
        "/servers/{server_id}/backups/{backup_id}/download",
    }
    routes = {route.path: route for route in router.routes if route.path in protected_paths}

    assert routes.keys() == protected_paths
    for route in routes.values():
        assert any(
            dependency.call is get_current_active_admin
            for dependency in route.dependant.dependencies
        )


def test_delete_backup_route_requires_operator():
    delete_route = next(
        route for route in router.routes
        if route.path == "/servers/{server_id}/backups/{backup_id}" and "DELETE" in route.methods
    )
    assert any(
        dependency.call is get_current_active_operator
        for dependency in delete_route.dependant.dependencies
    )


@patch('requests.Session.get')
def test_get_branches_and_tags_choice_param(mock_get):
    """
    Verify get_branches_and_tags extracts choices from ChoiceParameterDefinition.
    """
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "property": [
            {
                "parameterDefinitions": [
                    {
                        "_class": "hudson.model.ChoiceParameterDefinition",
                        "name": "BRANCH",
                        "choices": ["master", "feature/login", "release/v1.0"]
                    }
                ]
            }
        ]
    }
    mock_get.return_value = mock_resp

    client = JenkinsClient("http://localhost:8080", "admin", "token123")
    branches = client.get_branches_and_tags("my-job")
    assert branches == ["master", "feature/login", "release/v1.0"]


@patch('requests.Session.get')
def test_get_branches_and_tags_list_json_response(mock_get):
    """
    Verify get_branches_and_tags handles fillValueItems List JSON response without AttributeError.
    """
    # 1st call for api/json returns GitParameter definition
    api_resp = MagicMock()
    api_resp.status_code = 200
    api_resp.json.return_value = {
        "actions": [
            {
                "parameterDefinitions": [
                    {
                        "_class": "net.uaznia.lukanus.hudson.plugins.gitparameter.GitParameterDefinition",
                        "name": "git_branch"
                    }
                ]
            }
        ]
    }

    # 2nd call for fillValueItems returns standard ListBoxModel List format: [{"name": "...", "value": "..."}]
    fill_resp = MagicMock()
    fill_resp.status_code = 200
    fill_resp.json.return_value = [
        {"name": "origin/master", "value": "origin/master"},
        {"name": "origin/dev", "value": "origin/dev"}
    ]

    mock_get.side_effect = [api_resp, fill_resp]

    client = JenkinsClient("http://localhost:8080", "admin", "token123")
    branches = client.get_branches_and_tags("my-job")
    assert "origin/master" in branches
    assert "origin/dev" in branches


@patch('app.api.jenkins.JenkinsClient')
def test_get_git_branches_rejects_inactive_server(mock_client):
    server = MagicMock(is_active=0)
    job = MagicMock(name="frontend-deploy")
    db = MagicMock()
    db.get.side_effect = [server, job]

    with pytest.raises(HTTPException) as exc_info:
        get_git_branches(1, 2, db)

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "该 Jenkins 实例已被禁用，无法获取分支信息"
    mock_client.assert_not_called()
    db.query.assert_not_called()


@patch("app.api.jenkins.JenkinsClient")
def test_get_git_branches_rejects_job_owned_by_another_server(mock_client):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    db.add_all(
        [
            JenkinsServer(
                id=1,
                name="jenkins-1",
                url="http://jenkins-1.example",
                username="admin",
                api_token="token",
            ),
            JenkinsServer(
                id=2,
                name="jenkins-2",
                url="http://jenkins-2.example",
                username="admin",
                api_token="token",
            ),
            JenkinsJob(id=2, server_id=2, name="deploy"),
        ]
    )
    db.commit()

    with pytest.raises(HTTPException) as error:
        get_git_branches(1, 2, db)

    assert error.value.status_code == 404
    mock_client.assert_not_called()
    db.close()


def test_cancel_queue_item_posts_exact_queue_id():
    client = JenkinsClient("https://jenkins.example", "admin", "token")
    response = MagicMock(status_code=302, text="")
    client.session.post = MagicMock(return_value=response)
    client.get_crumb_headers = MagicMock(
        return_value={"Jenkins-Crumb": "crumb"}
    )

    client.cancel_queue_item(42)

    client.session.post.assert_called_once_with(
        "https://jenkins.example/queue/cancelItem",
        params={"id": 42},
        headers={"Jenkins-Crumb": "crumb"},
        timeout=10,
        allow_redirects=False,
    )


def test_queue_poll_callback_cancellation_propagates():
    client = JenkinsClient("https://jenkins.example", "admin", "token")
    response = MagicMock(status_code=200)
    response.json.return_value = {"why": "waiting"}
    client.session.get = MagicMock(return_value=response)

    def cancelled(_why):
        raise RuntimeError("cancelled by user")

    with pytest.raises(RuntimeError, match="cancelled by user"):
        client.get_build_number_from_queue(
            "https://jenkins.example/queue/item/1/",
            timeout=1,
            on_poll=cancelled,
        )


def test_run_job_directly_rejects_cross_server_job():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()

    server_a = JenkinsServer(id=1, name="A", url="http://a.example", username="admin", api_token="tok")
    server_b = JenkinsServer(id=2, name="B", url="http://b.example", username="admin", api_token="tok")
    job_b = JenkinsJob(id=10, server_id=2, name="job-on-b")
    operator = User(id=1, username="op", password_hash="x", role="operator")
    db.add_all([server_a, server_b, job_b, operator])
    db.commit()

    req = Request({
        "type": "http",
        "method": "POST",
        "path": "/servers/1/jobs/10/run",
        "headers": [],
        "query_string": b"",
        "scheme": "http",
        "server": ("testserver", 80),
        "client": ("127.0.0.1", 12345),
    })

    with patch.object(JenkinsClient, "trigger_build") as trigger:
        with pytest.raises(HTTPException) as error:
            run_job_directly(
                req,
                server_a.id,
                job_b.id,
                {},
                BackgroundTasks(),
                db,
                operator,
            )

    assert error.value.status_code == 404
    trigger.assert_not_called()
    db.close()


def test_ensure_jenkins_last_synced_at_column_idempotent():
    from sqlalchemy import text, inspect
    from app.services.init_db import ensure_jenkins_last_synced_at_column

    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE jenkins_server (id INTEGER PRIMARY KEY, name VARCHAR(100))"))

    ensure_jenkins_last_synced_at_column(engine)
    ensure_jenkins_last_synced_at_column(engine)

    inspector = inspect(engine)
    columns = {column["name"] for column in inspector.get_columns("jenkins_server")}
    assert "last_synced_at" in columns


def test_sync_jenkins_data_updates_last_synced_at_on_success():
    from datetime import datetime
    from app.api.jenkins import sync_jenkins_data

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    db = session_factory()

    server = JenkinsServer(id=1, name="jenkins-test", url="http://jenkins.test", username="admin", api_token="tok", last_synced_at=None)
    db.add(server)
    db.commit()
    db.close()

    mock_client = MagicMock()
    mock_client.get_views.return_value = [{"name": "All", "url": "http://jenkins.test/view/All/"}]
    mock_client.get_jobs_in_view.return_value = [{"name": "test-job", "description": "desc", "lastBuild": None}]

    with patch("app.api.jenkins.SyncSessionLocal", side_effect=session_factory), \
         patch("app.api.jenkins.JenkinsClient", return_value=mock_client):
        sync_jenkins_data(1)

    db = session_factory()
    updated_server = db.get(JenkinsServer, 1)
    assert updated_server.last_synced_at is not None
    assert isinstance(updated_server.last_synced_at, datetime)
    db.close()


def test_sync_jenkins_data_preserves_last_synced_at_on_failure():
    from datetime import datetime
    from app.api.jenkins import sync_jenkins_data

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    db = session_factory()

    previous_sync_time = datetime(2026, 1, 1, 12, 0, 0)
    server = JenkinsServer(id=1, name="jenkins-test", url="http://jenkins.test", username="admin", api_token="tok", last_synced_at=previous_sync_time)
    db.add(server)
    db.commit()
    db.close()

    mock_client = MagicMock()
    mock_client.get_views.side_effect = RuntimeError("Network error during sync")

    with patch("app.api.jenkins.SyncSessionLocal", side_effect=session_factory), \
         patch("app.api.jenkins.JenkinsClient", return_value=mock_client):
        with pytest.raises(RuntimeError, match="Network error during sync"):
            sync_jenkins_data(1)

    db = session_factory()
    failed_server = db.get(JenkinsServer, 1)
    assert failed_server.last_synced_at == previous_sync_time
    db.close()


def test_jenkins_server_schema_includes_last_synced_at():
    from datetime import datetime
    from app.schemas.jenkins import JenkinsServerResponse

    now = datetime.now()
    server = JenkinsServer(
        id=1,
        name="test",
        url="http://test",
        username="admin",
        api_token="tok",
        is_active=1,
        created_at=now,
        updated_at=now,
        last_synced_at=now,
    )
    schema = JenkinsServerResponse.model_validate(server)
    assert schema.last_synced_at == now


def test_sync_all_active_jenkins_servers():
    from app.services.jenkins_sync_task import sync_all_active_jenkins_servers

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    db = session_factory()

    active_server = JenkinsServer(id=1, name="active", url="http://a.test", username="u", api_token="t", is_active=1)
    inactive_server = JenkinsServer(id=2, name="inactive", url="http://i.test", username="u", api_token="t", is_active=0)
    db.add_all([active_server, inactive_server])
    db.commit()
    db.close()

    with patch("app.services.jenkins_sync_task.SyncSessionLocal", side_effect=session_factory), \
         patch("app.api.jenkins.sync_jenkins_data") as mock_sync:
        sync_all_active_jenkins_servers()

    mock_sync.assert_called_once_with(1)


def test_reload_jenkins_auto_sync_job():
    from app.services.scheduler import SchedulerManager
    from app.models.system import SystemConfig

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    db = session_factory()

    db.add_all([
        SystemConfig(config_key="jenkins_auto_sync_enabled", config_value="1", description="desc"),
        SystemConfig(config_key="jenkins_auto_sync_time", config_value="08:00", description="desc")
    ])
    db.commit()
    db.close()

    manager = SchedulerManager()
    with patch("app.services.scheduler.SyncSessionLocal", side_effect=session_factory), \
         patch.object(manager.scheduler, "add_job") as mock_add_job:
        manager.reload_jenkins_auto_sync_job()

    mock_add_job.assert_called_once()
    assert mock_add_job.call_args[1]["id"] == "auto_sync_jenkins_data"
    assert mock_add_job.call_args[1]["hour"] == 8
    assert mock_add_job.call_args[1]["minute"] == 0


def test_jenkins_api_tracker():
    from app.services.jenkins_client import JenkinsApiTracker

    stats_before = JenkinsApiTracker.get_stats()
    initial_today = stats_before["today_count"]
    initial_total = stats_before["total_count"]

    JenkinsApiTracker.record_request("test_cat")
    stats_after = JenkinsApiTracker.get_stats()

    assert stats_after["today_count"] == initial_today + 1
    assert stats_after["total_count"] == initial_total + 1
    assert stats_after["by_category"].get("test_cat", 0) >= 1
    assert stats_after["last_request_at"] is not None
