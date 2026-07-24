from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from datetime import datetime

class ReleaseTaskCreate(BaseModel):
    server_id: int
    job_id: Optional[int] = None
    job_name: str
    branch: str
    parameters: Optional[Dict[str, Any]] = None
    sequence: int = 0
    depends_on_sequence: Optional[int] = None # Helper to compute depends_on_task_id in pipeline creation

class ReleasePlanCreate(BaseModel):
    name: str
    type: str # IMMEDIATE, SCHEDULED, BATCH, PIPELINE
    execute_time: Optional[datetime] = None
    interval_minutes: int = 0
    pipeline_failure_strategy: str = "STOP" # STOP, CONTINUE
    tasks: List[ReleaseTaskCreate]

class ReleasePlanUpdate(BaseModel):
    name: Optional[str] = None
    execute_time: Optional[datetime] = None
    interval_minutes: Optional[int] = None
    pipeline_failure_strategy: Optional[str] = None

class ReleaseTaskResponse(BaseModel):
    id: int
    plan_id: int
    server_id: int
    job_id: Optional[int] = None
    view_id: Optional[int] = None
    job_name: str
    branch: str
    parameters: Dict[str, Any]
    sequence: int
    depends_on_task_id: Optional[int] = None
    status: str
    build_number: Optional[int] = None
    console_url: Optional[str] = None
    build_url: Optional[str] = None
    error_message: Optional[str] = None
    scheduled_time: Optional[datetime] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    duration: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

class ReleasePlanResponse(BaseModel):
    id: int
    name: str
    type: str
    execute_time: Optional[datetime] = None
    interval_minutes: int
    pipeline_failure_strategy: str
    status: str
    creator_id: int
    created_at: datetime
    updated_at: datetime
    preflight_status: str = "UNCHECKED"
    preflight_revision: int = 0
    preflight_checked_at: Optional[datetime] = None
    preflight_result: Optional[Dict[str, Any]] = None
    tasks: List[ReleaseTaskResponse] = []

    class Config:
        from_attributes = True

class ReleaseHistoryResponse(BaseModel):
    id: int
    task_id: Optional[int] = None
    plan_id: Optional[int] = None
    server_name: Optional[str] = None
    job_name: Optional[str] = None
    branch: Optional[str] = None
    build_number: Optional[int] = None
    status: Optional[str] = None
    trigger_by: Optional[str] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    duration: int
    is_external: bool = False
    created_at: datetime

    class Config:
        from_attributes = True

class BuildLogResponse(BaseModel):
    log_text: str
    next_start: int
    has_more: bool
