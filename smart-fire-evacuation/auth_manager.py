"""
auth_manager.py
---------------
Handles JSON Web Token (JWT) encoding and validation for ESP32 devices and cloud connectors.
"""
import time
import jwt
from typing import Optional, Dict, Any
from config.settings import JWT_PRIVATE_KEY, JWT_PUBLIC_KEY, JWT_ALGORITHM, DEVICE_TOKEN_EXPIRE_SEC
from logger import get_logger

log = get_logger("auth_manager")

def generate_device_token(device_id: str, role: str = "device") -> str:
    """Generate a JWT token for a specific device."""
    payload = {
        "device_id": device_id,
        "role": role,
        "iat": int(time.time()),
        "exp": int(time.time()) + DEVICE_TOKEN_EXPIRE_SEC
    }
    if not JWT_PRIVATE_KEY:
        log.warning("JWT_PRIVATE_KEY is not configured. Falling back to an insecure secret string for testing.")
        token = jwt.encode(payload, "fallback-insecure-secret", algorithm="HS256")
        return token
        
    token = jwt.encode(payload, JWT_PRIVATE_KEY, algorithm=JWT_ALGORITHM)
    return token

def decode_token(token: str) -> Optional[Dict[str, Any]]:
    """Decode and validate a JWT token. Returns payload dict on success, None on failure."""
    
    # If using fallback
    if not JWT_PUBLIC_KEY:
        try:
            return jwt.decode(token, "fallback-insecure-secret", algorithms=["HS256"])
        except Exception:
            return None
            
    try:
        payload = jwt.decode(token, JWT_PUBLIC_KEY, algorithms=[JWT_ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        log.warning("JWT validation failed: Token expired")
        return None
    except jwt.InvalidTokenError as e:
        log.warning(f"JWT validation failed: {str(e)}")
        return None
