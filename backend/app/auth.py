import hashlib
import os
import re
import secrets
import smtplib
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional
from uuid import UUID, uuid4

from fastapi import Depends, HTTPException, Request, Response, status
from jose import JWTError, jwt
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .database import get_database
from .models import OTPCode, User, UserIdentity

# Configuration
SECRET_KEY = os.getenv("SECRET_KEY", "your-secret-key-change-in-production")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # 24 hours
AUTH_COOKIE_NAME = "access_token"

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")

# SMTP configuration
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM_EMAIL = os.getenv("SMTP_FROM_EMAIL", "")

OTP_EXPIRY_MINUTES = 5
OTP_MAX_ATTEMPTS = 5

_system_random = secrets.SystemRandom()


# Pydantic models
class OTPRequest(BaseModel):
    identifier: str


class OTPVerify(BaseModel):
    identifier: str
    otp: str
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    year_of_birth: Optional[int] = None


class GoogleAuthRequest(BaseModel):
    credential: str


class ProfileUpdate(BaseModel):
    first_name: str
    last_name: str
    year_of_birth: Optional[int] = None


class UserResponse(BaseModel):
    id: str
    first_name: str
    last_name: str
    year_of_birth: Optional[int]

    model_config = {"from_attributes": True}

    @classmethod
    def from_user(cls, user: User) -> "UserResponse":
        return cls(
            id=str(user.id),
            first_name=user.first_name,
            last_name=user.last_name,
            year_of_birth=user.year_of_birth,
        )


# OTP helpers
def generate_otp() -> str:
    return str(_system_random.randint(100000, 999999))


def hash_otp(otp: str) -> str:
    return hashlib.sha256(otp.encode()).hexdigest()


def is_email(identifier: str) -> bool:
    return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", identifier))


# JWT helpers
def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def set_auth_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=AUTH_COOKIE_NAME,
        value=token,
        httponly=True,
        secure=os.getenv("ENVIRONMENT", "development") not in ("development", "dev"),
        samesite="lax",
        max_age=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        path="/",
    )


def clear_auth_cookie(response: Response) -> None:
    response.delete_cookie(key=AUTH_COOKIE_NAME, path="/")


# User/identity helpers
def get_or_create_identity(
    db: Session, identifier: str, provider: str
) -> tuple[UserIdentity, bool]:
    """Find or create a user identity. Returns (identity, is_new_user)."""
    identity = (
        db.query(UserIdentity)
        .filter(
            UserIdentity.provider == provider,
            UserIdentity.provider_id == identifier,
        )
        .first()
    )
    if identity:
        return identity, False

    # Create new user + identity
    user = User(
        id=uuid4(),
        first_name="",
        last_name="",
    )
    db.add(user)
    db.flush()

    identity = UserIdentity(
        id=uuid4(),
        user_id=user.id,
        provider=provider,
        provider_id=identifier,
        is_verified=False,
    )
    db.add(identity)
    db.commit()
    return identity, True


# Google id_token verification
def verify_google_id_token(credential: str) -> dict:
    from google.auth.transport import requests as google_requests
    from google.oauth2 import id_token

    try:
        idinfo = id_token.verify_oauth2_token(
            credential,
            google_requests.Request(),
            GOOGLE_CLIENT_ID,
        )
        return idinfo
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid Google token: {e}",
        )


# Current user dependency
async def get_current_user(
    request: Request,
    db: Session = Depends(get_database),
) -> Optional[User]:
    token = request.cookies.get(AUTH_COOKIE_NAME)
    if not token:
        return None
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: str = payload.get("sub")
        if user_id is None:
            return None
    except JWTError:
        return None
    try:
        uid = UUID(user_id)
    except (ValueError, AttributeError):
        return None
    user = db.query(User).filter(User.id == uid).first()
    return user


async def get_current_user_required(
    request: Request,
    db: Session = Depends(get_database),
) -> User:
    user = await get_current_user(request, db)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )
    return user


# Email sending
def send_otp_email(to_email: str, otp: str) -> None:
    if not SMTP_HOST:
        # Log OTP in development when SMTP is not configured
        import logging

        logging.getLogger(__name__).warning(f"SMTP not configured. OTP for {to_email}: {otp}")
        return

    msg = MIMEMultipart()
    msg["From"] = SMTP_FROM_EMAIL
    msg["To"] = to_email
    msg["Subject"] = "Your Login Code - Legal Search Buddy"

    body = f"""
    <html>
    <body>
        <h2>Your verification code</h2>
        <p style="font-size: 32px; font-weight: bold; letter-spacing: 8px;">{otp}</p>
        <p>This code expires in {OTP_EXPIRY_MINUTES} minutes.</p>
        <p>If you didn't request this code, please ignore this email.</p>
    </body>
    </html>
    """
    msg.attach(MIMEText(body, "html"))

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        if SMTP_USER:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
        server.sendmail(SMTP_FROM_EMAIL or "noreply@localhost", to_email, msg.as_string())
