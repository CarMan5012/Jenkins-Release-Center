from datetime import datetime
from typing import Optional
from sqlalchemy import String, Integer, DateTime, Text, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.database import Base
from sqlalchemy.dialects.mysql import LONGTEXT

class JenkinsServer(Base):
    __tablename__ = "jenkins_server"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    url: Mapped[str] = mapped_column(String(255), nullable=False)
    username: Mapped[str] = mapped_column(String(100), nullable=False)
    api_token: Mapped[str] = mapped_column(String(255), nullable=False) # Encrypted
    description: Mapped[str] = mapped_column(Text, nullable=True)
    is_active: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False)

    views: Mapped[list["JenkinsView"]] = relationship("JenkinsView", back_populates="server", cascade="all, delete-orphan")
    jobs: Mapped[list["JenkinsJob"]] = relationship("JenkinsJob", back_populates="server", cascade="all, delete-orphan")

class JenkinsView(Base):
    __tablename__ = "jenkins_view"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    server_id: Mapped[int] = mapped_column(Integer, ForeignKey("jenkins_server.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False) # Environment View name
    url: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False)

    server: Mapped["JenkinsServer"] = relationship("JenkinsServer", back_populates="views")
    jobs: Mapped[list["JenkinsJob"]] = relationship("JenkinsJob", back_populates="view")

    __table_args__ = (
        UniqueConstraint("server_id", "name", name="uix_server_view_name"),
    )

class JenkinsJob(Base):
    __tablename__ = "jenkins_job"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    server_id: Mapped[int] = mapped_column(Integer, ForeignKey("jenkins_server.id", ondelete="CASCADE"), nullable=False)
    view_id: Mapped[int] = mapped_column(Integer, ForeignKey("jenkins_view.id", ondelete="SET NULL"), nullable=True)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    folder: Mapped[str] = mapped_column(String(255), nullable=True)
    description: Mapped[str] = mapped_column(Text, nullable=True)
    last_build_number: Mapped[int] = mapped_column(Integer, nullable=True)
    last_build_result: Mapped[str] = mapped_column(String(30), nullable=True)
    last_build_time: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False)

    server: Mapped["JenkinsServer"] = relationship("JenkinsServer", back_populates="jobs")
    view: Mapped["JenkinsView"] = relationship("JenkinsView", back_populates="jobs")

    __table_args__ = (
        UniqueConstraint("server_id", "name", name="uix_server_job_name"),
    )

class JenkinsBackup(Base):
    __tablename__ = "jenkins_backup"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    server_id: Mapped[int] = mapped_column(Integer, ForeignKey("jenkins_server.id", ondelete="CASCADE"), nullable=False)
    backup_time: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="BACKUPING", nullable=False) # BACKUPING, SUCCESS, FAILED
    job_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    summary_md: Mapped[str] = mapped_column(Text().with_variant(LONGTEXT, "mysql"), nullable=True)
    zip_path: Mapped[str] = mapped_column(String(255), nullable=True)
    idempotency_key: Mapped[Optional[str]] = mapped_column(String(255), unique=True, nullable=True)
    
    server: Mapped["JenkinsServer"] = relationship("JenkinsServer")
