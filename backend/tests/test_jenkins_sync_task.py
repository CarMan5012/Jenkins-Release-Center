import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models.jenkins import JenkinsServer, JenkinsJob
from app.models.release import ReleaseHistory
from app.services.jenkins_sync_task import sync_external_builds

@patch('app.services.jenkins_sync_task.SyncSessionLocal')
@patch('app.services.jenkins_sync_task.JenkinsClient')
def test_sync_external_builds_success(mock_client_class, mock_session_local):
    """
    TDD Test: Verify that sync_external_builds detects new completed builds on Jenkins
    that were NOT triggered by our system, and logs them into release_history with task_id=None.
    """
    # 1. Setup in-memory SQLite DB for testing
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    TestingSessionLocal = sessionmaker(bind=engine)
    
    db = TestingSessionLocal()
    mock_session_local.return_value = db
    
    # Preseed server and job
    server = JenkinsServer(
        id=1,
        name="test-server",
        url="http://localhost:8080",
        username="admin",
        api_token="token123",
        is_active=1
    )
    job = JenkinsJob(
        id=1,
        server_id=1,
        view_id=1,
        name="frontend-build",
        description="Frontend build cache record"
    )
    db.add(server)
    db.add(job)
    db.commit()
    
    # 2. Mock JenkinsClient responses
    mock_client = MagicMock()
    mock_client_class.return_value = mock_client
    
    # Mock recent builds: return 1 manual external build completed
    mock_client.get_recent_builds.return_value = [
        {
            "number": 42,
            "result": "SUCCESS",
            "timestamp": 1672531190000, # 2023-01-01 07:59:50
            "duration": 120000,          # 120 seconds
            "building": False,
            "actions": [
                {
                    "causes": [
                        {"userName": "ManualOperator"}
                    ],
                    "parameters": [
                        {"name": "BRANCH_NAME", "value": "release/2026.07"}
                    ]
                }
            ]
        }
    ]
    
    # Mock console log output
    mock_client.get_progressive_log.return_value = ("Jenkins build logs chunk.", 100, False)
    mock_client.get_build_numbers.return_value = {42}
    
    # 3. Trigger sync
    sync_external_builds()
    
    # 4. Verify DB entries
    history_records = db.query(ReleaseHistory).all()
    assert len(history_records) == 1
    
    record = history_records[0]
    assert record.task_id is None
    assert record.plan_id is None
    assert record.server_id == 1
    assert record.server_name == "test-server"
    assert record.job_name == "frontend-build"
    assert record.branch == "release/2026.07"
    assert record.build_number == 42
    assert record.status == "SUCCESS"
    assert record.trigger_by == "ManualOperator"
    assert record.duration == 120
    assert record.is_external is True
    assert record.logs == "Jenkins build logs chunk."
    assert record.raw_response["final_jenkins_result"] == "SUCCESS"
    assert record.raw_response["external_sync"] is True
    assert record.raw_response.get("queueId") is None
    
    # 5. Verification of idempotency: running sync again shouldn't create duplicated histories
    sync_external_builds()
    history_records_after = db.query(ReleaseHistory).all()
    assert len(history_records_after) == 1
    
    db.close()
