from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

from app.core.database import Base
from app.models.release import ReleasePlan
from app.schemas.release import ReleasePlanResponse
from app.services.init_db import ensure_release_plan_preflight_columns


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
