import pytest
from app.models.release import ReleasePlan, ReleaseTask
from app.schemas.release import ReleasePlanCreate
from app.services.notification import send_release_notification
from unittest.mock import MagicMock

def test_release_plan_notify_dingtalk_defaults():
    plan_in = ReleasePlanCreate(
        name="test-plan",
        type="IMMEDIATE",
        pipeline_failure_strategy="STOP",
        tasks=[]
    )
    assert plan_in.notify_dingtalk is False

def test_send_release_notification_skips_dingtalk_when_notify_dingtalk_false(monkeypatch):
    mock_send = MagicMock()
    import app.services.notification as notif_module
    monkeypatch.setattr(notif_module, "send_notification_to_channel", mock_send)
    
    plan = ReleasePlan(id=1, name="Plan 1", notify_dingtalk=False)
    task = ReleaseTask(id=10, plan_id=1, job_name="test-job", branch="main", build_number=1, status="SUCCESS", duration=10)
    
    config_dingtalk = MagicMock()
    config_dingtalk.channel_type = "DINGTALK"
    config_dingtalk.trigger_events = ["success"]
    config_dingtalk.name = "DingTalk Bot"
    
    def mock_query(model):
        m = MagicMock()
        if model == notif_module.NotifyConfig:
            m.filter.return_value.all.return_value = [config_dingtalk]
        elif model == ReleaseTask:
            m.filter.return_value.first.return_value = task
        elif model == ReleasePlan:
            m.filter.return_value.first.return_value = plan
        return m

    mock_db = MagicMock()
    mock_db.query.side_effect = mock_query
    
    monkeypatch.setattr(notif_module, "SyncSessionLocal", lambda: mock_db)
    
    send_release_notification(10, "success")
    
    # Should skip sending to DingTalk because plan.notify_dingtalk is False
    mock_send.assert_not_called()

def test_send_release_notification_sends_dingtalk_when_notify_dingtalk_true(monkeypatch):
    mock_send = MagicMock()
    import app.services.notification as notif_module
    monkeypatch.setattr(notif_module, "send_notification_to_channel", mock_send)
    
    plan = ReleasePlan(id=2, name="Plan 2", notify_dingtalk=True)
    task = ReleaseTask(id=20, plan_id=2, job_name="test-job-2", branch="main", build_number=2, status="SUCCESS", duration=5)
    
    config_dingtalk = MagicMock()
    config_dingtalk.channel_type = "DINGTALK"
    config_dingtalk.trigger_events = ["success"]
    config_dingtalk.name = "DingTalk Bot"
    
    def mock_query(model):
        m = MagicMock()
        if model == notif_module.NotifyConfig:
            m.filter.return_value.all.return_value = [config_dingtalk]
        elif model == ReleaseTask:
            m.filter.return_value.first.return_value = task
            m.filter.return_value.all.return_value = [task]
        elif model == ReleasePlan:
            m.filter.return_value.first.return_value = plan
        return m

    mock_db = MagicMock()
    mock_db.query.side_effect = mock_query
    
    monkeypatch.setattr(notif_module, "SyncSessionLocal", lambda: mock_db)
    
    from app.services.notification import send_plan_summary_notification
    send_plan_summary_notification(2)
    
    # Should successfully call send_notification_to_channel with ActionCard
    mock_send.assert_called_once()
