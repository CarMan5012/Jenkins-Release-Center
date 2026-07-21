import json
import zipfile
import pytest
from unittest.mock import patch, MagicMock

from app.api.deps import get_current_active_admin
from app.api.jenkins import read_backup_details, router
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