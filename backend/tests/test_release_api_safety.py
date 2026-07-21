from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import BackgroundTasks, HTTPException, Request

from app.api.release import create_plan, update_plan
from app.schemas.release import ReleasePlanCreate, ReleaseTaskCreate


class NoDatabaseAccess:
    def __getattr__(self, name):
        raise AssertionError(f"database touched before schedule validation: {name}")


def request_without_headers():
    return Request({
        "type": "http",
        "method": "POST",
        "path": "/plans",
        "headers": [],
        "query_string": b"",
        "scheme": "http",
        "server": ("testserver", 80),
        "client": ("127.0.0.1", 12345),
    })


def scheduled_plan(execute_time):
    return ReleasePlanCreate(
        name="safe schedule",
        type="SCHEDULED",
        execute_time=execute_time,
        tasks=[ReleaseTaskCreate(
            server_id=1,
            job_id=1,
            job_name="deploy",
            branch="main",
        )],
    )


def test_create_rejects_missing_schedule_before_database_write():
    with pytest.raises(HTTPException) as error:
        create_plan(
            request_without_headers(),
            scheduled_plan(None),
            BackgroundTasks(),
            NoDatabaseAccess(),
            SimpleNamespace(id=7),
        )

    assert error.value.status_code == 400
    assert "调度时间" in error.value.detail


def test_update_rejects_past_schedule_before_database_or_scheduler_changes():
    with pytest.raises(HTTPException) as error:
        update_plan(
            request_without_headers(),
            99,
            scheduled_plan(datetime.now() - timedelta(minutes=1)),
            NoDatabaseAccess(),
            SimpleNamespace(id=7),
        )

    assert error.value.status_code == 400
    assert "未来" in error.value.detail
