import asyncio
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import requests
from fastapi import Request

from app.core.exceptions import global_exception_handler
from app.services import deps_helper, jenkins_backup_service
from app.services.jenkins_client import SafeSession


ROOT = Path(__file__).resolve().parents[2]


def redirecting_session(location="https://login.example/"):
    session = SafeSession()
    adapter = MagicMock()

    def send(request, **kwargs):
        response = requests.Response()
        response.status_code = 301 if request.url == "https://jenkins.example/api/json" else 200
        response.url = request.url
        response.request = request
        response.raw = MagicMock()
        response._content = b""
        if response.status_code == 301:
            response.headers["Location"] = location
        return response

    adapter.send.side_effect = send
    session.mount("https://", adapter)
    return session


def test_safe_session_exposes_cross_origin_redirect_without_following_it():
    response = redirecting_session().get(
        "https://jenkins.example/api/json",
        allow_redirects=False,
    )

    assert response.status_code == 301
    assert response.headers["Location"] == "https://login.example/"


def test_safe_session_rejects_followed_cross_origin_redirect():
    with pytest.raises(requests.exceptions.InvalidSchema):
        redirecting_session().get("https://jenkins.example/api/json")


def test_safe_session_follows_same_origin_redirect():
    response = redirecting_session("https://jenkins.example/login").get(
        "https://jenkins.example/api/json"
    )

    assert response.status_code == 200
    assert response.url == "https://jenkins.example/login"


def test_idempotency_keys_are_scoped_to_user_and_bounded():
    normalize = getattr(deps_helper, "normalize_idempotency_key", None)
    assert callable(normalize), "normalize_idempotency_key is missing"
    assert normalize(" request-1 ", 42) == "42:request-1"
    assert normalize("   ", 42) is None
    with pytest.raises(ValueError, match="128"):
        normalize(" " + "x" * 128, 42)
    with pytest.raises(ValueError, match="128"):
        normalize("x" * 129, 42)


def test_jenkins_backup_job_paths_stay_inside_temp_root(tmp_path):
    safe_path = getattr(jenkins_backup_service, "safe_backup_job_path", None)
    assert callable(safe_path), "safe_backup_job_path is missing"
    assert safe_path(tmp_path, "folder/app").is_relative_to(tmp_path.resolve())
    with pytest.raises(ValueError, match="path"):
        safe_path(tmp_path, "../../../app/data/leak")

def test_jenkins_request_rejects_cross_origin_urls():
    request = getattr(jenkins_backup_service, "jenkins_request", None)
    assert callable(request), "jenkins_request is missing"
    with pytest.raises(ValueError, match="origin"):
        request(
            MagicMock(),
            "GET",
            "http://169.254.169.254/latest/meta-data",
            "https://jenkins.example/",
        )


def test_jenkins_request_disables_redirects():
    request = getattr(jenkins_backup_service, "jenkins_request", None)
    assert callable(request), "jenkins_request is missing"
    session = MagicMock()

    request(
        session,
        "GET",
        "https://jenkins.example/job/app/config.xml",
        "https://jenkins.example/",
        timeout=15,
    )

    session.get.assert_called_once_with(
        "https://jenkins.example/job/app/config.xml",
        timeout=15,
        allow_redirects=False,
    )


def test_global_500_response_does_not_disclose_exception_details():
    request = Request({
        "type": "http",
        "method": "GET",
        "path": "/boom",
        "headers": [],
        "query_string": b"",
        "scheme": "http",
        "server": ("testserver", 80),
        "client": ("127.0.0.1", 12345),
    })
    response = asyncio.run(
        global_exception_handler(request, RuntimeError("database password leaked"))
    )
    body = json.loads(response.body)

    assert response.status_code == 500
    assert body["message"] == "Internal Server Error"
    assert "password" not in response.body.decode().lower()


def test_local_compose_files_keep_services_on_loopback_and_backups_in_tmpfs():
    sqlite_compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    mysql_compose = (ROOT / "docker-compose.mysql.yml").read_text(encoding="utf-8")

    assert '"127.0.0.1:8021:8001"' in sqlite_compose
    assert '"127.0.0.1:8021:8001"' in mysql_compose
    assert '"127.0.0.1:3316:3306"' in mysql_compose
    assert "APP_ENV=development" in sqlite_compose
    assert "APP_ENV=development" in mysql_compose
    for content in (sqlite_compose, mysql_compose):
        assert "BACKUP_TMP_DIR=/tmp/jenkins-release-backups" in content
        assert "/tmp/jenkins-release-backups" in content.split("tmpfs:", 1)[1]
