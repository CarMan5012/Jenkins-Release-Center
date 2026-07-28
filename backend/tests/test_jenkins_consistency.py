import os

os.environ["APP_ENV"] = "development"

from sqlalchemy import create_engine, inspect, text


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
    assert "uix_release_history_build_identity" in {
        index["name"] for index in inspect(engine).get_indexes("release_history")
    }
