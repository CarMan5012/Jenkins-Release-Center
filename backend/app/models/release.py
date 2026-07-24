from datetime import datetime
from typing import Any, Optional
from sqlalchemy import String, Integer, DateTime, Text, ForeignKey, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.database import Base

class ReleasePlan(Base):
    __tablename__ = "release_plan"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    type: Mapped[str] = mapped_column(String(30), nullable=False) # IMMEDIATE, SCHEDULED, BATCH, PIPELINE
    execute_time: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    interval_minutes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    pipeline_failure_strategy: Mapped[str] = mapped_column(String(30), default="STOP", nullable=False) # STOP, CONTINUE
    status: Mapped[str] = mapped_column(String(30), default="WAITING", nullable=False) # WAITING, RUNNING, SUCCESS, FAILED, CANCELLED
    creator_id: Mapped[int] = mapped_column(Integer, ForeignKey("user.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False)
    idempotency_key: Mapped[Optional[str]] = mapped_column(String(255), unique=True, nullable=True)
    preflight_status: Mapped[str] = mapped_column(String(20), default="UNCHECKED", nullable=False)
    preflight_checked_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    preflight_result: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)

    creator: Mapped["User"] = relationship("User")
    tasks: Mapped[list["ReleaseTask"]] = relationship("ReleaseTask", back_populates="plan", cascade="all, delete-orphan")

class ReleaseTask(Base):
    __tablename__ = "release_task"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plan_id: Mapped[int] = mapped_column(Integer, ForeignKey("release_plan.id", ondelete="CASCADE"), nullable=False)
    server_id: Mapped[int] = mapped_column(Integer, ForeignKey("jenkins_server.id"), nullable=False)
    job_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    job_name: Mapped[str] = mapped_column(String(150), nullable=False)
    branch: Mapped[str] = mapped_column(String(150), nullable=False)
    parameters: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    depends_on_task_id: Mapped[int] = mapped_column(Integer, ForeignKey("release_task.id", ondelete="SET NULL"), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="WAITING", nullable=False) # WAITING, RUNNING, SUCCESS, FAILED, SKIPPED, CANCELLED
    build_number: Mapped[int] = mapped_column(Integer, nullable=True)
    console_url: Mapped[str] = mapped_column(String(255), nullable=True)
    build_url: Mapped[str] = mapped_column(String(255), nullable=True)
    error_message: Mapped[str] = mapped_column(Text, nullable=True)
    scheduled_time: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    duration: Mapped[int] = mapped_column(Integer, default=0, nullable=False) # seconds
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False)

    @property
    def view_id(self) -> int:
        return self.job.view_id if self.job else None

    plan: Mapped["ReleasePlan"] = relationship("ReleasePlan", back_populates="tasks")
    server: Mapped["JenkinsServer"] = relationship("JenkinsServer")
    job: Mapped["JenkinsJob"] = relationship(
        "JenkinsJob",
        primaryjoin="foreign(ReleaseTask.job_id) == JenkinsJob.id",
        viewonly=True,
    )
    histories: Mapped[list["ReleaseHistory"]] = relationship("ReleaseHistory", back_populates="task", cascade="all, delete-orphan")

from sqlalchemy.dialects.mysql import LONGTEXT

class ReleaseHistory(Base):
    __tablename__ = "release_history"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("release_task.id", ondelete="CASCADE"), nullable=True)
    plan_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("release_plan.id", ondelete="CASCADE"), nullable=True)
    server_name: Mapped[str] = mapped_column(String(100), nullable=True)
    job_name: Mapped[str] = mapped_column(String(150), nullable=True)
    branch: Mapped[str] = mapped_column(String(150), nullable=True)
    build_number: Mapped[int] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=True)
    trigger_by: Mapped[str] = mapped_column(String(100), nullable=True) # operator username
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    duration: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    logs: Mapped[str] = mapped_column(Text().with_variant(LONGTEXT, "mysql"), nullable=True) # Full log cache
    raw_response: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    is_external: Mapped[bool] = mapped_column(default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, nullable=False)

    task: Mapped["ReleaseTask"] = relationship("ReleaseTask", back_populates="histories")
