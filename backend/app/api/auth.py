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

def generate_clean_captcha(code: str, width: int = 240, height: int = 80):
    from PIL import Image, ImageDraw, ImageFont
    import os

    # 2x 超采样 (Supersampling) 保证高分辨率下绝对清晰不模糊
    scale = 2
    sw, sh = width * scale, height * scale

    bg_color = (248, 250, 252)
    img = Image.new("RGBA", (sw, sh), bg_color)
    draw = ImageDraw.Draw(img)

    # 尝试加载高清晰 TrueType 字体
    font = None
    font_paths = [
        "C:/Windows/Fonts/arialbd.ttf",
        "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/segoeui.ttf",
        "C:/Windows/Fonts/tahoma.ttf",
        "C:/Windows/Fonts/verdanab.ttf",
        "C:/Windows/Fonts/impact.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"
    ]
    for fp in font_paths:
        if os.path.exists(fp):
            try:
                font = ImageFont.truetype(fp, size=84)
                break
            except Exception:
                pass
    if font is None:
        try:
            font = ImageFont.load_default(size=80)
        except Exception:
            font = ImageFont.load_default()

    # 清晰高对比度深色系
    colors = [
        (15, 23, 42),    # slate-900
        (30, 58, 138),   # blue-900
        (88, 28, 135),   # purple-900
        (6, 78, 59),     # emerald-900
        (124, 45, 18),   # orange-900
        (136, 19, 55),   # rose-900
    ]

    # 绘制柔和光滑的背景干涉线
    for _ in range(3):
        x1 = random.randint(0, sw // 2)
        y1 = random.randint(0, sh)
        x2 = random.randint(sw // 2, sw)
        y2 = random.randint(0, sh)
        draw.line([(x1, y1), (x2, y2)], fill=(203, 213, 225), width=3)

    char_count = len(code)
    spacing = sw / (char_count + 0.5)

    for i, char in enumerate(code):
        char_box_size = 128
        char_img = Image.new("RGBA", (char_box_size, char_box_size), (0, 0, 0, 0))
        char_draw = ImageDraw.Draw(char_img)
        color = random.choice(colors)

        try:
            bbox = char_draw.textbbox((0, 0), char, font=font)
            w = bbox[2] - bbox[0]
            h = bbox[3] - bbox[1]
            char_draw.text(((char_box_size - w) / 2 - bbox[0], (char_box_size - h) / 2 - bbox[1]), char, font=font, fill=color)
        except Exception:
            char_draw.text((30, 20), char, font=font, fill=color)

        angle = random.randint(-8, 8)
        rotated = char_img.rotate(angle, expand=0, resample=Image.Resampling.BICUBIC)

        x = spacing * (i + 0.4) + random.randint(-4, 4)
        y = (sh - char_box_size) / 2 + random.randint(-6, 6)

        img.paste(rotated, (int(x), int(y)), mask=rotated)

    # 少量点噪点
    for _ in range(30):
        nx = random.randint(0, sw - 1)
        ny = random.randint(0, sh - 1)
        draw.ellipse([nx, ny, nx+2, ny+2], fill=random.choice(colors))

    # 转换为 RGB 并下采样回 240x80 使用 LANCZOS 高画质缩放
    final_img = Image.new("RGB", (sw, sh), bg_color)
    final_img.paste(img, (0, 0), mask=img)
    resample_filter = getattr(Image.Resampling, 'LANCZOS', getattr(Image, 'LANCZOS', Image.BICUBIC))
    final_img = final_img.resize((width, height), resample=resample_filter)

    return final_img

@router.get("/captcha")
def get_captcha():
    now = time.time()
    # Cleanup expired captchas
    expired_keys = [k for k, v in captcha_store.items() if v["expires"] < now]
    for k in expired_keys:
        captcha_store.pop(k, None)

    # 排除易混淆字符（如 0, O, I, 1）
    allowed_chars = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
    code = ''.join(random.choices(allowed_chars, k=4))

    try:
        image = generate_clean_captcha(code, width=240, height=80)
    except Exception as e:
        try:
            from captcha.image import ImageCaptcha
            image_captcha = ImageCaptcha(width=240, height=80, font_sizes=(44, 48, 52))
            image = image_captcha.generate_image(code)
        except Exception:
            raise HTTPException(status_code=500, detail=f"CAPTCHA generation error: {str(e)}")

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
        f"用户 {user.username} 成功登录系统"
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
        f"用户 {current_user.username} 成功退出系统"
    )
    return {"message": "已成功注销登录。"}

