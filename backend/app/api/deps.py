from fastapi import Depends
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session
import jwt

from app.core.config import settings
from app.core.database import get_db
from app.core.exceptions import AuthException, ForbiddenException
from app.models.user import User, TokenBlacklist

oauth2_scheme = OAuth2PasswordBearer(tokenUrl=f"{settings.API_V1_STR}/auth/login")

def get_current_user(
    db: Session = Depends(get_db), 
    token: str = Depends(oauth2_scheme)
) -> User:
    # Check if token is in the blacklist (logged out)
    is_blacklisted = db.query(TokenBlacklist).filter(TokenBlacklist.token == token).first()
    if is_blacklisted:
        raise AuthException("Token has been invalidated (logged out).")

    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise AuthException("Invalid token credentials.")
    except jwt.PyJWTError:
        raise AuthException("Token signature is invalid or expired.")

        
    user = db.query(User).filter(User.username == username).first()
    if user is None:
        raise AuthException("User profile not found in system.")
    if not user.is_active:
        raise AuthException("User account is deactivated.")
    return user

def get_current_active_admin(
    current_user: User = Depends(get_current_user)
) -> User:
    if current_user.role != "admin":
        raise ForbiddenException("Admin level access required.")
    return current_user

def get_current_active_operator(
    current_user: User = Depends(get_current_user)
) -> User:
    if current_user.role not in ["admin", "operator"]:
        raise ForbiddenException("Operator level authorization required.")
    return current_user

def log_action(db: Session, user: User, action: str, ip: str, details: str = None):
    from app.models.system import AuditLog
    audit = AuditLog(
        user_id=user.id if user else None,
        username=user.username if user else "anonymous",
        action=action,
        ip_address=ip,
        details=details
    )
    db.add(audit)
    db.commit()
