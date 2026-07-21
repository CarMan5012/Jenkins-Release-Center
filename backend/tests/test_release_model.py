from app.models.release import ReleaseTask


def test_release_task_job_id_is_not_a_jenkins_job_foreign_key():
    foreign_key_targets = {
        foreign_key.target_fullname
        for foreign_key in ReleaseTask.__table__.foreign_keys
    }

    assert "jenkins_job.id" not in foreign_key_targets
    assert "jenkins_server.id" in foreign_key_targets
