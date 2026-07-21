from pydantic import BaseModel, field_validator, field_serializer
from typing import Optional, List
from datetime import datetime
from app.core.security import rsa_decrypt

class SystemConfigBase(BaseModel):
    config_key: str
    config_value: str
    description: Optional[str] = None

class SystemConfigCreate(SystemConfigBase):
    pass

class SystemConfigResponse(SystemConfigBase):
    id: int
    updated_at: datetime

    class Config:
        from_attributes = True

class NotifyConfigBase(BaseModel):
    name: str
    channel_type: str # DINGTALK, WECHAT, WEBHOOK
    webhook_url: str
    secret: Optional[str] = None
    keyword: Optional[str] = None
    is_active: int = 1
    trigger_events: List[str]

class NotifyConfigCreate(NotifyConfigBase):
    @field_validator("secret", mode="before")
    @classmethod
    def decrypt_secret(cls, v: Optional[str]) -> Optional[str]:
        if v and len(v) > 100:
            try:
                return rsa_decrypt(v)
            except Exception:
                pass
        return v

    @field_validator("webhook_url", mode="before")
    @classmethod
    def decrypt_webhook_url(cls, v: str) -> str:
        if v and len(v) > 100:
            try:
                return rsa_decrypt(v)
            except Exception:
                pass
        return v

class NotifyConfigResponse(NotifyConfigBase):
    id: int
    created_at: datetime
    updated_at: datetime

    @field_serializer('secret')
    def serialize_secret(self, secret: Optional[str]) -> Optional[str]:
        if secret:
            return "••••••••"
        return None

    @field_serializer('webhook_url')
    def serialize_webhook_url(self, webhook_url: str) -> str:
        if not webhook_url:
            return ""
        import urllib.parse
        try:
            parsed = urllib.parse.urlparse(webhook_url)
            if parsed.query:
                qs = urllib.parse.parse_qs(parsed.query)
                changed = False
                for k in qs.keys():
                    val = qs[k][0]
                    if len(val) > 10:
                        qs[k] = [val[:6] + "••••••••" + val[-6:] if len(val) > 12 else "••••••••"]
                        changed = True
                if changed:
                    new_query = urllib.parse.urlencode(qs, doseq=True)
                    new_url = urllib.parse.urlunparse((
                        parsed.scheme, parsed.netloc, parsed.path,
                        parsed.params, new_query, parsed.fragment
                    ))
                    return urllib.parse.unquote(new_url)
        except Exception:
            pass
        if len(webhook_url) > 35:
            return webhook_url[:25] + "••••••••"
        return webhook_url

    class Config:
        from_attributes = True

class AuditLogResponse(BaseModel):
    id: int
    user_id: Optional[int] = None
    username: str
    action: str
    ip_address: Optional[str] = None
    details: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True
