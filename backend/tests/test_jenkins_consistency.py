import os
from datetime import datetime
from unittest.mock import MagicMock

os.environ["APP_ENV"] = "development"

import pytest
from sqlalchemy import create_engine, inspect, text

from app.services import jenkins_client


def test_jenkins_primitives_extract_queue_id():
    client = jenkins_client.JenkinsClient(
        "https://jenkins.example/", "admin", "token"
    )

    assert client.extract_queue_id(
        "https://jenkins.example/queue/item/123/"
    ) == 123
    with pytest.raises(ValueError):
        client.extract_queue_id("https://other.example/queue/item/123/")
    with pytest.raises(ValueError):
        client.extract_queue_id("https://jenkins.example/queue/item/not-a-number/")


def test_jenkins_primitives_extract_queue_id_respects_context_path():
    client = jenkins_client.JenkinsClient(
        "https://jenkins.example/jenkins/", "admin", "token"
    )

    assert client.extract_queue_id(
        "https://jenkins.example/jenkins/queue/item/123/"
    ) == 123
    with pytest.raises(ValueError):
        client.extract_queue_id("https://jenkins.example/queue/item/123/")
    with pytest.raises(ValueError):
        client.extract_queue_id(
            "https://jenkins.example/jenkins-other/queue/item/123/"
        )


@pytest.mark.parametrize(
    ("building", "result", "expected"),
    [
        (True, None, "BUILDING"),
        (False, "ABORTED", "CANCELLED"),
        (False, "FAILURE", "FAILED"),
        (False, "NOT_BUILT", "FAILED"),
        (False, "SUCCESS", "SUCCESS"),
        (False, "UNSTABLE", "UNSTABLE"),
        (False, None, "UNKNOWN"),
        (False, "SOMETHING_NEW", "UNKNOWN"),
    ],
)
def test_jenkins_primitives_normalize_status(building, result, expected):
    assert jenkins_client.normalize_jenkins_status(building, result) == expected


def test_jenkins_primitives_datetime():
    assert jenkins_client.jenkins_datetime(1_700_000_000_000) == datetime.fromtimestamp(
        1_700_000_000
    )
    assert jenkins_client.jenkins_datetime(None) is None
    assert jenkins_client.jenkins_datetime(0) is None
    assert jenkins_client.jenkins_datetime("1700000000000") is None


def test_jenkins_primitives_get_queue_item():
    found = MagicMock(status_code=200)
    found.json.return_value = {"id": 123}
    missing = MagicMock(status_code=404)
    client = jenkins_client.JenkinsClient(
        "https://jenkins.example/", "admin", "token"
    )
    client.session.get = MagicMock(side_effect=[found, missing])

    assert client.get_queue_item(123) == {"id": 123}
    assert client.get_queue_item(123) is None
    assert client.session.get.call_args_list == [
        (("https://jenkins.example/queue/item/123/api/json",), {"timeout": 10}),
        (("https://jenkins.example/queue/item/123/api/json",), {"timeout": 10}),
    ]
    found.raise_for_status.assert_called_once_with()
    missing.raise_for_status.assert_not_called()


def test_jenkins_primitives_recent_builds_keep_queue_id():
    response = MagicMock(status_code=200)
    response.json.return_value = {
        "allBuilds": [{"number": 7, "queueId": 123}]
    }
    client = jenkins_client.JenkinsClient(
        "https://jenkins.example/", "admin", "token"
    )
    client.session.get = MagicMock(return_value=response)

    assert client.get_recent_builds("deploy") == [{"number": 7, "queueId": 123}]
    requested_url = client.session.get.call_args.args[0]
    assert requested_url.count("queueId") == 2


def test_jenkins_primitives_build_status_exposes_queue_id():
    response = MagicMock(status_code=200)
    response.json.return_value = {
        "building": True,
        "result": None,
        "timestamp": 1_700_000_000_000,
        "duration": 5_000,
        "url": "https://jenkins.example/job/deploy/7/",
        "queueId": 123,
    }
    client = jenkins_client.JenkinsClient(
        "https://jenkins.example/", "admin", "token"
    )
    client.session.get = MagicMock(return_value=response)

    status = client.get_build_status("deploy", 7)

    assert status["queue_id"] == 123


def test_legacy_schema_upgrade_is_idempotent_and_deduplicates_builds():
    from app.services.init_db import ensure_jenkins_consistency_schema

    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE jenkins_server (id INTEGER PRIMARY KEY, name VARCHAR(100))"))
        connection.execute(text("CREATE TABLE release_task (id INTEGER PRIMARY KEY, build_number INTEGER)"))
        connection.execute(text(
            "CREATE TABLE release_history ("
            "id INTEGER PRIMARY KEY, task_id INTEGER, server_name VARCHAR(100), "
            "job_name VARCHAR(150), build_number INTEGER, status VARCHAR(30))"
        ))
        connection.execute(text("INSERT INTO jenkins_server (id, name) VALUES (1, 's1'), (2, 's2')"))
        connection.execute(text("INSERT INTO release_task (id, build_number) VALUES (10, 7)"))
        connection.execute(text(
            "INSERT INTO release_history "
            "(id, task_id, server_name, job_name, build_number, status) VALUES "
            "(1, 10, 's1', 'deploy', 7, 'SUCCESS'), "
            "(2, NULL, 's1', 'deploy', 7, 'SUCCESS'), "
            "(3, NULL, 's2', 'deploy', 7, 'SUCCESS')"
        ))

    ensure_jenkins_consistency_schema(engine)
    ensure_jenkins_consistency_schema(engine)

    inspector = inspect(engine)
    assert "jenkins_queue_id" in {
        column["name"] for column in inspector.get_columns("release_task")
    }
    assert "server_id" in {
        column["name"] for column in inspector.get_columns("release_history")
    }
    with engine.connect() as connection:
        rows = connection.execute(text(
            "SELECT server_id, task_id FROM release_history ORDER BY server_id"
        )).all()
    assert [tuple(row) for row in rows] == [(1, 10), (2, None)]
    target_index = next(
        index
        for index in inspect(engine).get_indexes("release_history")
        if index["name"] == "uix_release_history_build_identity"
    )
    assert target_index["unique"]
    assert target_index["column_names"] == ["server_id", "job_name", "build_number"]


def test_schema_upgrade_rejects_same_name_non_unique_index():
    from app.services.init_db import ensure_jenkins_consistency_schema

    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE jenkins_server (id INTEGER PRIMARY KEY, name VARCHAR(100))"))
        connection.execute(text("CREATE TABLE release_task (id INTEGER PRIMARY KEY, build_number INTEGER)"))
        connection.execute(text(
            "CREATE TABLE release_history ("
            "id INTEGER PRIMARY KEY, task_id INTEGER, server_id INTEGER, "
            "server_name VARCHAR(100), job_name VARCHAR(150), "
            "build_number INTEGER, status VARCHAR(30))"
        ))
        connection.execute(text(
            "CREATE INDEX uix_release_history_build_identity "
            "ON release_history (server_id, job_name, build_number)"
        ))
        connection.execute(text(
            "INSERT INTO release_history "
            "(id, task_id, server_id, server_name, job_name, build_number, status) VALUES "
            "(1, 10, 1, 's1', 'deploy', 7, 'SUCCESS'), "
            "(2, NULL, 1, 's1', 'deploy', 7, 'SUCCESS')"
        ))

    with pytest.raises(
        RuntimeError,
        match="Index uix_release_history_build_identity conflicts",
    ):
        ensure_jenkins_consistency_schema(engine)

    with engine.connect() as connection:
        ids = connection.execute(text("SELECT id FROM release_history ORDER BY id")).scalars().all()
    assert ids == [1, 2]


def test_schema_upgrade_deduplicates_before_server_backfill():
    from app.services.init_db import ensure_jenkins_consistency_schema

    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE jenkins_server (id INTEGER PRIMARY KEY, name VARCHAR(100))"))
        connection.execute(text("CREATE TABLE release_task (id INTEGER PRIMARY KEY, build_number INTEGER)"))
        connection.execute(text(
            "CREATE TABLE release_history ("
            "id INTEGER PRIMARY KEY, task_id INTEGER, server_id INTEGER, "
            "server_name VARCHAR(100), job_name VARCHAR(150), "
            "build_number INTEGER, status VARCHAR(30))"
        ))
        connection.execute(text(
            "CREATE UNIQUE INDEX uix_release_history_build_identity "
            "ON release_history (server_id, job_name, build_number)"
        ))
        connection.execute(text("INSERT INTO jenkins_server (id, name) VALUES (1, 's1')"))
        connection.execute(text(
            "INSERT INTO release_history "
            "(id, task_id, server_id, server_name, job_name, build_number, status) VALUES "
            "(1, 10, NULL, 's1', 'deploy', 7, 'SUCCESS'), "
            "(2, NULL, NULL, 's1', 'deploy', 7, 'SUCCESS')"
        ))

    ensure_jenkins_consistency_schema(engine)
    ensure_jenkins_consistency_schema(engine)

    with engine.connect() as connection:
        rows = connection.execute(text(
            "SELECT id, task_id, server_id FROM release_history ORDER BY id"
        )).all()
    assert [tuple(row) for row in rows] == [(1, 10, 1)]
