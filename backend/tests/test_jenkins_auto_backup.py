import pytest
from unittest.mock import patch, MagicMock
from app.services.jenkins_backup_service import auto_backup_active_jenkins_servers

def test_auto_backup_active_jenkins_servers():
    mock_server = MagicMock()
    mock_server.id = 1
    mock_server.name = "test_server"
    mock_server.is_active = 1

    with patch("app.services.jenkins_backup_service.SyncSessionLocal") as mock_db_cls, \
         patch("app.services.jenkins_backup_service.execute_jenkins_backup") as mock_exec:
        
        mock_db = MagicMock()
        mock_db_cls.return_value = mock_db
        mock_db.query.return_value.filter.return_value.all.return_value = [mock_server]

        auto_backup_active_jenkins_servers()
        assert mock_exec.called
