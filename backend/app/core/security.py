from datetime import datetime, timedelta, timezone
from typing import Any, Union
import jwt
from passlib.context import CryptContext
from cryptography.fernet import Fernet
import base64
import hashlib
from app.core.config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Derive a valid Fernet key from our AES_SECRET_KEY settings using SHA-256
key_bytes = hashlib.sha256(settings.AES_SECRET_KEY.encode()).digest()
fernet_key = base64.urlsafe_b64encode(key_bytes)
cipher_suite = Fernet(fernet_key)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)

def create_access_token(subject: Union[str, Any], expires_delta: timedelta = None) -> str:
    expire = datetime.now(timezone.utc) + (
        expires_delta
        or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    to_encode = {"exp": expire, "sub": str(subject)}
    encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    return encoded_jwt

def encrypt_secret(plain_text: str) -> str:
    if not plain_text:
        return ""
    encrypted_bytes = cipher_suite.encrypt(plain_text.encode())
    return encrypted_bytes.decode()

def decrypt_secret(encrypted_text: str) -> str:
    if not encrypted_text:
        return ""
    try:
        decrypted_bytes = cipher_suite.decrypt(encrypted_text.encode())
        return decrypted_bytes.decode()
    except Exception:
        raise ValueError("Decryption failed. Please check the secret key configuration.")


import os
from cryptography.hazmat.primitives.asymmetric import rsa, padding
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.backends import default_backend

# Define where to store the private key
PRIVATE_KEY_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data", "private_key.pem")

_private_key = None

def get_rsa_private_key():
    global _private_key
    if _private_key is not None:
        return _private_key
    
    if os.path.exists(PRIVATE_KEY_PATH):
        with open(PRIVATE_KEY_PATH, "rb") as key_file:
            _private_key = serialization.load_pem_private_key(
                key_file.read(),
                password=None,
                backend=default_backend()
            )
    else:
        # Generate 2048-bit RSA key pair
        _private_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048,
            backend=default_backend()
        )
        pem = _private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption()
        )
        os.makedirs(os.path.dirname(PRIVATE_KEY_PATH), exist_ok=True)
        with open(PRIVATE_KEY_PATH, "wb") as key_file:
            key_file.write(pem)
            
    return _private_key

def get_rsa_public_key_pem() -> str:
    priv_key = get_rsa_private_key()
    pub_key = priv_key.public_key()
    pem = pub_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return pem.decode("utf-8")

def rsa_decrypt(ciphertext_b64: str) -> str:
    if not ciphertext_b64:
        return ""
    try:
        priv_key = get_rsa_private_key()
        ciphertext = base64.b64decode(ciphertext_b64)
        decrypted = priv_key.decrypt(
            ciphertext,
            padding.PKCS1v15()
        )
        return decrypted.decode("utf-8")
    except Exception as e:
        raise ValueError(f"RSA decryption failed: {str(e)}")

