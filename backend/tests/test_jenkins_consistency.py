import os
from datetime import datetime
from unittest.mock import MagicMock, patch

os.environ["APP_ENV"] = "development"

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models.jenkins import JenkinsServer
from app.models.release import ReleasePlan, ReleaseTask
from app.models.user import User

from app.services import jenkins_client, release_service

def session_with_reconcile_task(**task_values):
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
        status=task_values.pop("status", "QUEUED"),
        **task_values,
    )
    db.add(task)
    db.commit()
    return db, task


def test_reconcile_keeps_bound_build_number_without_jenkins_lookup():
    db, task = session_with_reconcile_task(build_number=42, jenkins_queue_id=1001)
    client = MagicMock()

    assert release_service.resolve_task_build_number(db, task, client) == 42
    client.get_queue_item.assert_not_called()
    client.get_recent_builds.assert_not_called()


def test_reconcile_recovers_build_number_by_exact_queue_id():
    db, task = session_with_reconcile_task(jenkins_queue_id=1001)
    client = MagicMock()
    client.get_queue_item.return_value = {"executable": None}
    client.get_recent_builds.return_value = [
        {"number": 43, "queueId": 1002},
        {"number": 42, "queueId": 1001},
    ]

    assert release_service.resolve_task_build_number(db, task, client) == 42
    assert task.build_number == 42
    client.get_recent_builds.assert_called_once_with("deploy", 20)


def test_reconcile_leaves_queue_unresolved_without_exact_queue_id():
    db, task = session_with_reconcile_task(jenkins_queue_id=1001)
    client = MagicMock()
    client.get_queue_item.return_value = {"executable": {}}
    client.get_recent_builds.return_value = [{"number": 43, "queueId": 1002}]

    assert release_service.resolve_task_build_number(db, task, client) is None
    assert task.status == "QUEUED"
    assert task.build_number is None
    assert "queue" in task.error_message.lower()


def test_reconcile_uses_queue_executable_without_recent_build_scan():
    db, task = session_with_reconcile_task(jenkins_queue_id=1001)
    client = MagicMock()
    client.get_queue_item.return_value = {"executable": {"number": 42}}

    assert release_service.resolve_task_build_number(db, task, client) == 42
    assert task.build_number == 42
    client.get_recent_builds.assert_not_called()



def test_reconcile_does_not_restore_cancelled_task_to_building():
    db, task = session_with_reconcile_task(
        status="BUILDING",
        build_number=42,
        jenkins_queue_id=1001,
    )
    client = MagicMock()

    def cancel_then_report_building(_job_name, _build_number):
        task.status = "CANCELLED"
        db.commit()
        return {"building": True}

    client.get_build_status.side_effect = cancel_then_report_building
    with patch.object(release_service, "JenkinsClient", return_value=client):
        assert release_service.reconcile_single_task(db, task.id) is False

    db.refresh(task)
    assert task.status == "CANCELLED"


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

from datetime import timedelta


@pytest.mark.parametrize(
    ("jenkins_result", "task_status", "notification", "pipeline_handler"),
    [
        ("SUCCESS", "SUCCESS", "success", "handle_pipeline_success"),
        ("ABORTED", "CANCELLED", "failed", "handle_pipeline_failure"),
        ("FAILURE", "FAILED", "failed", "handle_pipeline_failure"),
    ],
)
def test_finalize_task_claims_terminal_state_once(
    jenkins_result, task_status, notification, pipeline_handler
):
    db, task = session_with_reconcile_task(status="BUILDING", build_number=42)
    status_info = {
        "building": False,
        "result": jenkins_result,
        "timestamp": 1_700_000_000_000,
        "duration": 7,
    }
    client = MagicMock()

    with (
        patch.object(release_service, "send_release_notification") as notify,
        patch.object(release_service, "write_history") as history,
        patch.object(release_service, "handle_pipeline_success") as success,
        patch.object(release_service, "handle_pipeline_failure") as failure,
    ):
        assert release_service.finalize_task_from_jenkins(db, task, status_info, client) is True
        assert release_service.finalize_task_from_jenkins(db, task, status_info, client) is False

    db.refresh(task)
    started_at = datetime.fromtimestamp(1_700_000_000)
    assert task.status == task_status
    assert task.duration == 7
    assert task.started_at == started_at
    assert task.finished_at == started_at + timedelta(seconds=7)
    assert task.error_message == (
        None if task_status == "SUCCESS" else f"Jenkins result: {task_status}"
    )
    notify.assert_called_once_with(task.id, notification)
    history.assert_called_once_with(
        db, task, task_status, 7, jenkins_result, client
    )
    {"handle_pipeline_success": success, "handle_pipeline_failure": failure}[
        pipeline_handler
    ].assert_called_once_with(db, task.plan_id, task.id)
    assert success.call_count + failure.call_count == 1

from app.api import release as release_api
from app.models.release import ReleaseHistory


def test_plan_get_endpoints_are_read_only():
    db, task = session_with_reconcile_task(status="BUILDING", build_number=42)
    original_status = task.status

    with (
        patch.object(release_service, "reconcile_single_task") as reconcile,
        patch.object(release_service, "check_and_finalize_plan") as finalize_plan,
    ):
        plans = release_api.list_plans(db=db, current_user=task.plan.creator)
        plan = release_api.get_plan(task.plan_id, db=db, current_user=task.plan.creator)

    assert [item.id for item in plans] == [task.plan_id]
    assert plan.id == task.plan_id
    assert task.status == original_status
    reconcile.assert_not_called()
    finalize_plan.assert_not_called()


def test_get_missing_plan_is_read_only_404():
    db, task = session_with_reconcile_task()

    with (
        patch.object(release_service, "reconcile_single_task") as reconcile,
        patch.object(release_service, "check_and_finalize_plan") as finalize_plan,
        pytest.raises(release_api.HTTPException) as error,
    ):
        release_api.get_plan(999, db=db, current_user=task.plan.creator)

    assert error.value.status_code == 404
    reconcile.assert_not_called()
    finalize_plan.assert_not_called()


def test_write_history_promotes_matching_external_build():
    db, task = session_with_reconcile_task(
        status="SUCCESS",
        build_number=42,
        started_at=datetime(2024, 1, 1, 12, 0),
        finished_at=datetime(2024, 1, 1, 12, 0, 7),
    )
    db.add(
        ReleaseHistory(
            server_id=task.server_id,
            server_name="jenkins",
            job_name=task.job_name,
            build_number=task.build_number,
            status="SUCCESS",
            is_external=True,
        )
    )
    db.commit()

    release_service.write_history(db, task, "SUCCESS", 7, "SUCCESS", None)

    histories = db.query(ReleaseHistory).all()
    assert len(histories) == 1
    history = histories[0]
    assert (history.task_id, history.plan_id, history.server_id) == (
        task.id,
        task.plan_id,
        task.server_id,
    )
    assert history.is_external is False
    assert (history.job_name, history.branch, history.build_number) == (
        task.job_name,
        task.branch,
        task.build_number,
    )


def test_write_history_prefers_internal_task_row_and_removes_external_identity():
    db, task = session_with_reconcile_task(status="FAILED", build_number=42)
    internal = ReleaseHistory(
        task_id=task.id,
        plan_id=task.plan_id,
        server_id=None,
        job_name=task.job_name,
        build_number=task.build_number,
        status="RUNNING",
        is_external=False,
    )
    external = ReleaseHistory(
        server_id=task.server_id,
        server_name="jenkins",
        job_name=task.job_name,
        build_number=task.build_number,
        status="FAILURE",
        is_external=True,
    )
    db.add_all([internal, external])
    db.commit()
    internal_id = internal.id

    release_service.write_history(db, task, "FAILED", 9, "FAILURE", None)

    histories = db.query(ReleaseHistory).all()
    assert [history.id for history in histories] == [internal_id]
    assert histories[0].task_id == task.id
    assert histories[0].server_id == task.server_id
    assert histories[0].is_external is False


def test_write_history_does_not_merge_missing_build_identities():
    db, first = session_with_reconcile_task(status="SUCCESS")
    first.started_at = datetime(2024, 1, 1, 12, 0)
    first.finished_at = datetime(2024, 1, 1, 12, 0, 1)
    db.commit()
    release_service.write_history(db, first, "SUCCESS", 1, "SUCCESS", None)

    second = ReleaseTask(
        plan_id=first.plan_id,
        server_id=first.server_id,
        job_name=first.job_name,
        branch="other",
        sequence=1,
        status="SUCCESS",
        started_at=datetime(2024, 1, 1, 12, 1),
        finished_at=datetime(2024, 1, 1, 12, 1, 1),
    )
    db.add(second)
    db.commit()

    release_service.write_history(db, second, "SUCCESS", 1, "SUCCESS", None)

    histories = db.query(ReleaseHistory).order_by(ReleaseHistory.id).all()
    assert [history.task_id for history in histories] == [first.id, second.id]


def test_workflow_keeps_polling_unknown_jenkins_result():
    db, task = session_with_reconcile_task(status="WAITING")
    client = MagicMock()
    client.trigger_build.return_value = "http://jenkins.example/queue/item/1/"
    client.extract_queue_id.return_value = 1
    client.get_build_number_from_queue.return_value = 42
    client.get_build_status.side_effect = [
        {
            "building": False,
            "result": None,
            "timestamp": 1_700_000_000_000,
            "duration": 0,
        },
        {
            "building": False,
            "result": "SUCCESS",
            "timestamp": 1_700_000_000_000,
            "duration": 7,
        },
    ]

    with (
        patch.object(release_service, "SyncSessionLocal", return_value=db),
        patch.object(release_service, "JenkinsClient", return_value=client),
        patch.object(release_service.time, "sleep"),
        patch.object(release_service, "send_release_notification"),
        patch.object(release_service, "write_history"),
        patch.object(release_service, "handle_pipeline_success") as success,
    ):
        release_service.execute_task_workflow(task.plan_id, task.id)

    db.expire_all()
    finished_task = db.get(ReleaseTask, task.id)
    assert client.get_build_status.call_count == 2
    assert finished_task.status == "SUCCESS"
    success.assert_called_once_with(db, task.plan_id, task.id)
