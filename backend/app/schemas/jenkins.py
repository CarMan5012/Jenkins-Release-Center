from pydantic import BaseModel, field_validator
from typing import Optional, List
from datetime import datetime
from app.core.security import rsa_decrypt

class JenkinsServerBase(BaseModel):
    name: str
    url: str
    username: str
    description: Optional[str] = None
    is_active: int = 1

class JenkinsServerCreate(JenkinsServerBase):
    api_token: str

    @field_validator("api_token", mode="before")
    @classmethod
    def decrypt_api_token(cls, v: str) -> str:
        if v and len(v) > 100:
            try:
                return rsa_decrypt(v)
            except Exception:
                pass
        return v

class JenkinsServerUpdate(BaseModel):
    name: Optional[str] = None
    url: Optional[str] = None
    username: Optional[str] = None
    api_token: Optional[str] = None
    description: Optional[str] = None
    is_active: Optional[int] = None

    @field_validator("api_token", mode="before")
    @classmethod
    def decrypt_api_token(cls, v: Optional[str]) -> Optional[str]:
        if v and len(v) > 100:
            try:
                return rsa_decrypt(v)
            except Exception:
                pass
        return v

class JenkinsServerResponse(JenkinsServerBase):
    id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

class JenkinsViewBase(BaseModel):
    name: str
    url: str
    description: Optional[str] = None

class JenkinsViewResponse(JenkinsViewBase):
    id: int
    server_id: int
    created_at: datetime
    
    class Config:
        from_attributes = True

class JenkinsJobBase(BaseModel):
    name: str
    folder: Optional[str] = None
    description: Optional[str] = None
    last_build_number: Optional[int] = None
    last_build_result: Optional[str] = None
    last_build_time: Optional[datetime] = None

class JenkinsJobResponse(JenkinsJobBase):
    id: int
    server_id: int
    view_id: Optional[int] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

class SyncJobsRequest(BaseModel):
    view_name: str

class JenkinsBackupResponse(BaseModel):
    id: int
    server_id: int
    backup_time: datetime
    status: str
    job_count: int
    zip_path: Optional[str] = None
    
    class Config:
        from_attributes = True
