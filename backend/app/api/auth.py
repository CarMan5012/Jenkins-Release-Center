from fastapi import APIRouter, Depends, HTTPException, Request, Form
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
import uuid
import base64
import random
import string
from io import BytesIO


from app.core.database import get_db
from app.core.security import verify_password, create_access_token, get_rsa_public_key_pem, rsa_decrypt
from app.api.deps import get_current_user, log_action, oauth2_scheme
from app.core.config import settings
from app.services.deps_helper import get_client_ip
from collections import defaultdict
import time

login_attempts = defaultdict(lambda: {"count": 0, "lock_until": 0.0})
captcha_store = {} # captcha_id -> {"code": code, "expires": float}
from app.models.user import User, TokenBlacklist

from app.schemas.user import Token, UserResponse

router = APIRouter()

@router.get("/public-key")
def get_public_key():
    return {"public_key": get_rsa_public_key_pem()}

@router.get("/captcha")
def get_captcha():
    try:
        from captcha.image import ImageCaptcha
    except ImportError:
        raise HTTPException(status_code=500, detail="CAPTCHA library not installed")
        
    image_captcha = ImageCaptcha(width=120, height=40)
    now = time.time()
    # Cleanup expired captchas
    expired_keys = [k for k, v in captcha_store.items() if v["expires"] < now]
    for k in expired_keys:
        captcha_store.pop(k, None)
        
    code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=4))
    image = image_captcha.generate_image(code)
    
    buffered = BytesIO()
    image.save(buffered, format="PNG")
    img_str = base64.b64encode(buffered.getvalue()).decode()
    
    captcha_id = str(uuid.uuid4())
    captcha_store[captcha_id] = {"code": code.lower(), "expires": now + 300}
    
    return {"captcha_id": captcha_id, "image_base64": f"data:image/png;base64,{img_str}"}

@router.post("/login", response_model=Token)
def login(
    request: Request,
    form_data: OAuth2PasswordRequestForm = Depends(),
    captcha_id: str = Form(None),
    captcha_code: str = Form(None),
    db: Session = Depends(get_db)
):
    is_prod = settings.APP_ENV != "development"

    # CAPTCHA Validation (Only required if provided or in production)
    if is_prod or captcha_id:
        if not captcha_id or not captcha_code:
            raise HTTPException(status_code=400, detail="请输入验证码")
        
        stored = captcha_store.get(captcha_id)
        if not stored:
            raise HTTPException(status_code=400, detail="验证码已过期，请点击图片刷新")
            
        if stored["expires"] < time.time():
            captcha_store.pop(captcha_id, None)
            raise HTTPException(status_code=400, detail="验证码已过期，请点击图片刷新")
            
        if stored["code"] != captcha_code.lower():
            raise HTTPException(status_code=400, detail="验证码不正确")
            
        # One-time use
        captcha_store.pop(captcha_id, None)

    
    try:
        username = rsa_decrypt(form_data.username)
        password = rsa_decrypt(form_data.password)
    except Exception as e:
        if is_prod:
            raise HTTPException(status_code=400, detail="RSA解密失败")
        else:
            username = form_data.username
            password = form_data.password
            import warnings
            warnings.warn("RSA 解密失败，开发模式下回退到明文传输。")

    client_ip = get_client_ip(request)
    limit_key = (username, client_ip)
    
    now_time = time.time()
    if login_attempts[limit_key]["lock_until"] > now_time:
        remaining = int(login_attempts[limit_key]["lock_until"] - now_time)
        raise HTTPException(status_code=429, detail=f"登录失败次数过多，账号已锁定，请在 {remaining} 秒后再试")

    user = db.query(User).filter(User.username == username).first()
    if not user or not verify_password(password, user.password_hash):
        login_attempts[limit_key]["count"] += 1
        if login_attempts[limit_key]["count"] >= 5:
            login_attempts[limit_key]["lock_until"] = time.time() + 300 # Lock for 5 minutes
            raise HTTPException(status_code=429, detail="登录失败次数过多，账号已锁定5分钟")
        raise HTTPException(status_code=400, detail="用户名或密码不正确")
        
    if not user.is_active:
        raise HTTPException(status_code=400, detail="用户账号已被停用")
        
    # Reset limit attempts on success
    login_attempts[limit_key] = {"count": 0, "lock_until": 0.0}
        
    access_token = create_access_token(subject=user.username)
    log_action(
        db, 
        user, 
        "LOGIN", 
        client_ip, 
        f"User {user.username} successfully logged in."
    )
    
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "role": user.role,
        "username": user.username
    }

@router.get("/me", response_model=UserResponse)
def get_me(current_user: User = Depends(get_current_user)):
    return current_user

@router.post("/logout")
def logout(
    request: Request,
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    # Put token to database blacklist
    is_blacklisted = db.query(TokenBlacklist).filter(TokenBlacklist.token == token).first()
    if not is_blacklisted:
        blacklist_item = TokenBlacklist(token=token)
        db.add(blacklist_item)
        db.commit()
    
    log_action(
        db, 
        current_user, 
        "LOGOUT", 
        get_client_ip(request), 
        f"User {current_user.username} successfully logged out."
    )
    return {"message": "已成功注销登录。"}

