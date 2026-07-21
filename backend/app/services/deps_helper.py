from fastapi import Request
from app.core.config import settings

def is_request_trusted_https(request: Request) -> bool:
    if settings.ALLOW_INSECURE_HTTP:
        return True
        
    client_ip = request.client.host if request.client else None
    if not client_ip:
        return False
        
    trusted_ips = settings.trusted_proxy_ip_set
    
    # Check X-Forwarded-Proto if client_ip is trusted
    if client_ip in trusted_ips:
        proto = request.headers.get("x-forwarded-proto", "").lower()
        if proto == "https":
            return True
            
    # Direct HTTPS check
    if request.url.scheme == "https":
        return True
        
    return False

def get_client_ip(request: Request) -> str:
    client_ip = request.client.host if request.client else "unknown"
    trusted_ips = settings.trusted_proxy_ip_set
    if client_ip in trusted_ips:
        x_forwarded_for = request.headers.get("x-forwarded-for")
        if x_forwarded_for:
            # Take the first IP from X-Forwarded-For list
            return x_forwarded_for.split(",")[0].strip()
    return client_ip

def validate_jenkins_url(url: str, is_production: bool, allowed_origins: list = None):
    from urllib.parse import urlparse
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("仅允许 http 或 https 协议")
    if parsed.username or parsed.password:
        raise ValueError("禁止在 URL 中包含用户名或密码信息")
    if parsed.fragment:
        raise ValueError("禁止在 URL 中包含片段(fragment)")
    
    # Port restrictions
    if parsed.port:
        forbidden_ports = {21, 22, 23, 25, 53, 110, 143, 445, 1433, 1521, 3306, 5432, 6379, 27017, 27018}
        if parsed.port in forbidden_ports:
            raise ValueError(f"禁止使用非预期端口: {parsed.port}")
            
    if is_production:
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if not allowed_origins or origin not in allowed_origins:
            raise ValueError(f"URL Origin [{origin}] 不在允许的 Jenkins Origin 白名单中")


def normalize_idempotency_key(raw_key: str | None, user_id: int) -> str | None:
    if raw_key is None:
        return None
    if len(raw_key) > 128:
        raise ValueError("Idempotency-Key must not exceed 128 characters")
    key = raw_key.strip()
    if not key:
        return None
    return f"{user_id}:{key}"
