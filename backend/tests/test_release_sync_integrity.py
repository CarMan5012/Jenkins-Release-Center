import pytest
from sqlalchemy import create_engine, Table, Column, Integer, String, text, MetaData
from sqlalchemy.exc import IntegrityError

def test_sync_deletion_integrity_error_red():
    """
    TDD RED Test:
    Verify that when a physical foreign key constraint exists,
    deleting a cached jenkins_job record that is referenced by a release_task
    raises a database IntegrityError.
    """
    engine = create_engine("sqlite:///:memory:")
    
    # Enable SQLite foreign key constraint enforcement
    with engine.connect() as conn:
        conn.execute(text("PRAGMA foreign_keys = ON;"))
        
    metadata = MetaData()
    
    # Define temp tables simulating the legacy database structure with ForeignKey
    jenkins_job = Table(
        'jenkins_job_temp', metadata,
        Column('id', Integer, primary_key=True),
        Column('name', String(100), nullable=False)
    )
    
    # Legacy release_task has a non-nullable job_id and a foreign key constraint
    from sqlalchemy import ForeignKey
    release_task = Table(
        'release_task_temp', metadata,
        Column('id', Integer, primary_key=True),
        Column('job_id', Integer, ForeignKey('jenkins_job_temp.id'), nullable=False),
        Column('job_name', String(100), nullable=True)
    )
    
    metadata.create_all(engine)
    
    # Insert test data
    with engine.begin() as conn:
        conn.execute(jenkins_job.insert().values(id=1, name="test-job"))
        conn.execute(release_task.insert().values(id=1, job_id=1, job_name="test-job"))
        
    # Attempting to delete the parent row (jenkins_job) must fail with IntegrityError
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(jenkins_job.delete().where(jenkins_job.c.id == 1))

def test_sync_deletion_after_decoupling_green():
    """
    TDD GREEN Test:
    Verify that after removing the physical foreign key constraint and making
    job_id nullable, deleting a cached jenkins_job record succeeds,
    and the release_task remains intact.
    """
    engine = create_engine("sqlite:///:memory:")
    with engine.connect() as conn:
        conn.execute(text("PRAGMA foreign_keys = ON;"))
        
    metadata = MetaData()
    
    jenkins_job = Table(
        'jenkins_job_temp', metadata,
        Column('id', Integer, primary_key=True),
        Column('name', String(100), nullable=False)
    )
    
    # Decoupled release_task: job_id is nullable, and NO ForeignKey constraint is declared
    release_task = Table(
        'release_task_temp', metadata,
        Column('id', Integer, primary_key=True),
        Column('job_id', Integer, nullable=True),
        Column('job_name', String(100), nullable=False) # Snapshot is non-nullable now
    )
    
    metadata.create_all(engine)
    
    # Insert test data
    with engine.begin() as conn:
        conn.execute(jenkins_job.insert().values(id=1, name="test-job"))
        conn.execute(release_task.insert().values(id=1, job_id=1, job_name="test-job"))
        
    # Deleting the parent row should now succeed
    with engine.begin() as conn:
        conn.execute(jenkins_job.delete().where(jenkins_job.c.id == 1))
        
    # Verify the release_task record is still there
    with engine.connect() as conn:
        result = conn.execute(release_task.select()).first()
        assert result is not None
        assert result.job_id == 1
        assert result.job_name == "test-job"
