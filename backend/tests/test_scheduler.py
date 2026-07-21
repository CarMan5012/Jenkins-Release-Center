import pytest
from datetime import datetime, timedelta
from app.services.scheduler import scheduler_manager

def dummy_task(plan_id: int, task_id: int):
    pass

def test_scheduler_add_and_remove():
    """
    Test dynamic scheduling. Overrides default store with MemoryJobStore for unit testing.
    """
    from apscheduler.jobstores.memory import MemoryJobStore
    
    # Clean and reconfigure to run completely in memory without hitting production DB
    scheduler_manager.scheduler.remove_all_jobs()
    scheduler_manager.scheduler.configure(jobstores={'default': MemoryJobStore()}, force=True)
    
    scheduler_manager.start()
    
    # Add a job for 10 seconds into the future
    exec_time = datetime.now() + timedelta(seconds=10)
    scheduler_manager.add_release_job(99, 88, exec_time, dummy_task, 99, 88)
    
    # Assert existence
    job = scheduler_manager.scheduler.get_job("plan_99_task_88")
    assert job is not None
    assert job.id == "plan_99_task_88"
    assert job.args == (99, 88)
    
    # Remove job
    scheduler_manager.remove_release_job(99, 88)
    job_deleted = scheduler_manager.scheduler.get_job("plan_99_task_88")
    assert job_deleted is None
    
    scheduler_manager.shutdown()
