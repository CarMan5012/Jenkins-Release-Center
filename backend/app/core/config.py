import os
import sys
import warnings
from typing import List, Union
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    API_V1_STR: str = "/api/v1"
    PROJECT_NAME: str = "Jenkins Release Scheduler"
    BASE_PATH: str = "/"
    
    # Environment
    APP_ENV: str = "production"
    ALLOW_INSECURE_HTTP: bool = True
    
    # Secret Files
    SECRET_KEY_FILE: str = "/run/secrets/jwt_key"
    AES_SECRET_KEY_FILE: str = "/run/secrets/aes_key"
    BACKUP_ENCRYPTION_KEY_FILE: str = "/run/secrets/backup_key"
    INITIAL_ADMIN_PASSWORD_FILE: str = "/run/secrets/admin_password"
    
    # Security
    SECRET_KEY: str = ""
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 480 # Default for production (8 hours), will be adjusted in __init__
    
    # AES Encryption Key (Must be a 32-byte url-safe base64-encoded key)
    AES_SECRET_KEY: str = ""
    
    # Backup Encryption
    BACKUP_ENCRYPTION_KEY: str = ""

    # Database
    MYSQL_SERVER: str = "localhost"
    MYSQL_USER: str = "root"
    MYSQL_PASSWORD: str = "root123456"
    MYSQL_DB: str = "jenkins_release_scheduler"
    MYSQL_PORT: int = 3306
    
    @property
    def SYNC_SQLALCHEMY_DATABASE_URI(self) -> str:
        return f"mysql+pymysql://{self.MYSQL_USER}:{self.MYSQL_PASSWORD}@{self.MYSQL_SERVER}:{self.MYSQL_PORT}/{self.MYSQL_DB}?charset=utf8mb4"

    # Initial Admin
    INITIAL_ADMIN_USERNAME: str = "admin"
    INITIAL_ADMIN_PASSWORD: str = ""

    # Trusted Proxies
    TRUSTED_PROXY_IPS: str = "127.0.0.1"

    @property
    def trusted_proxy_ip_set(self) -> set[str]:
        return {ip.strip() for ip in self.TRUSTED_PROXY_IPS.split(",") if ip.strip()}

    # Allowed Jenkins Origins for production SSRF protection
    JENKINS_ALLOWED_ORIGINS_STR: str = ""

    @property
    def JENKINS_ALLOWED_ORIGINS(self) -> List[str]:
        return [origin.strip() for origin in self.JENKINS_ALLOWED_ORIGINS_STR.split(",") if origin.strip()]

    def __init__(self, **values):
        super().__init__(**values)
        
        # Standardize BASE_PATH
        # e.g., "" -> "/", "/jenkins/" -> "/jenkins", "jenkins" -> "/jenkins"
        base_path = self.BASE_PATH.strip()
        if not base_path or base_path == "/":
            self.BASE_PATH = "/"
        else:
            if not base_path.startswith("/"):
                base_path = "/" + base_path
            if base_path.endswith("/"):
                base_path = base_path[:-1]
            self.BASE_PATH = base_path
        
        # 1. Default weak definitions
        default_secret_key = "your-super-secret-key-change-it-in-production"
        default_aes_secret_key = "7j3H8k9L2m4N5p6Q7r8S9t0U1v2W3x4Y5z6A7B8C9D0="
        default_backup_key = "dev-backup-key-must-change-in-prod-12345"
        default_admin_password = "admin123"
        
        is_dev = self.APP_ENV == "development"
        
        # Adjust ACCESS_TOKEN_EXPIRE_MINUTES
        if is_dev:
            self.ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7 # 7 days
        else:
            self.ACCESS_TOKEN_EXPIRE_MINUTES = 8 * 60 # 8 hours
            
        def load_secret_from_file(file_path: str, default_val: str, name: str) -> str:
            if file_path and os.path.exists(file_path):
                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        val = f.read().strip()
                    if val:
                        return val
                except Exception as e:
                    if not is_dev:
                        raise RuntimeError(f"生产启动失败：读取密钥文件 [{name}] ({file_path}) 失败: {str(e)}")
            
            if is_dev:
                warnings.warn(f"[{name}] 密钥文件不存在或为空，在开发环境下回退到默认值。")
                return default_val
            else:
                raise RuntimeError(f"生产启动失败：必要密钥文件 [{name}] ({file_path}) 缺失或为空")
                
        # Load sequentially
        self.SECRET_KEY = load_secret_from_file(self.SECRET_KEY_FILE, default_secret_key, "SECRET_KEY")
        self.AES_SECRET_KEY = load_secret_from_file(self.AES_SECRET_KEY_FILE, default_aes_secret_key, "AES_SECRET_KEY")
        self.BACKUP_ENCRYPTION_KEY = load_secret_from_file(self.BACKUP_ENCRYPTION_KEY_FILE, default_backup_key, "BACKUP_ENCRYPTION_KEY")
        self.INITIAL_ADMIN_PASSWORD = load_secret_from_file(self.INITIAL_ADMIN_PASSWORD_FILE, default_admin_password, "INITIAL_ADMIN_PASSWORD")
        
        # Perform production-only safety checks
        if not is_dev:
            if self.SECRET_KEY == default_secret_key:
                raise RuntimeError("生产启动失败：[SECRET_KEY] 不能使用默认弱密码")
            if self.AES_SECRET_KEY == default_aes_secret_key:
                raise RuntimeError("生产启动失败：[AES_SECRET_KEY] 不能使用默认弱密码")
            if self.BACKUP_ENCRYPTION_KEY == default_backup_key:
                raise RuntimeError("生产启动失败：[BACKUP_ENCRYPTION_KEY] 不能使用默认弱密码")
            if self.INITIAL_ADMIN_PASSWORD == default_admin_password:
                raise RuntimeError("生产启动失败：[INITIAL_ADMIN_PASSWORD] 不能使用默认管理员弱密码")

    class Config:
        case_sensitive = True
        env_file = ".env"

settings = Settings()

