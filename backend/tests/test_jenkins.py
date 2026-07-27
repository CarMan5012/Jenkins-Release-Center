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


@patch('requests.Session.post')
def test_jenkins_stop_build_posts_to_stop_endpoint(mock_post):
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