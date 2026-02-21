import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from fastapi import Depends, FastAPI, HTTPException, Query, Response, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import models
from .auth import (
    GoogleAuthRequest,
    OTPRequest,
    OTPVerify,
    ProfileUpdate,
    UserResponse,
    clear_auth_cookie,
    create_access_token,
    generate_otp,
    get_current_user_required,
    get_or_create_identity,
    hash_otp,
    is_email,
    send_otp_email,
    set_auth_cookie,
    verify_google_id_token,
    OTP_EXPIRY_MINUTES,
    OTP_MAX_ATTEMPTS,
)
from .custom_types import SearchResult
from .database import engine, get_database
from .embeddings import EmbeddingModel
from .models import Document, DocumentChunk, OTPCode, User, UserIdentity

app = FastAPI()

environment = os.getenv("ENVIRONMENT", "development")

allowed_origins = [
    "http://localhost:3000",
]
frontend_url = os.getenv("FRONTEND_URL", "")
if frontend_url:
    allowed_origins.append(frontend_url)

# In dev/development: allow any HTTP origin so LAN IPs work (mirrors api.ts dynamic hostname logic).
# In staging/prod: restrict to explicit allowed_origins only.
_cors_kwargs: dict = {
    "allow_credentials": True,
    "allow_methods": ["*"],
    "allow_headers": ["*"],
}
if environment in ("development", "dev"):
    _cors_kwargs["allow_origin_regex"] = r"http://.*"
else:
    _cors_kwargs["allow_origins"] = allowed_origins

app.add_middleware(CORSMiddleware, **_cors_kwargs)

embed_model = EmbeddingModel(
    os.getenv("MODEL_NAME", "sentence-transformers/all-MiniLM-L6-v2")
)


@asynccontextmanager
async def lifespan(app):
    models.Base.metadata.create_all(bind=engine)
    yield


@app.get("/search")
def vector_search(
    q: str = Query(...),
    court: str = Query(None),
    year: str = Query(None),
    judge: str = Query(None),
    is_gst: str = Query(None),
    decision_date: str = Query(None),
    db: Session = Depends(get_database),
) -> list[SearchResult]:
    query_vector = embed_model.encode(q).tolist()

    stmt = select(DocumentChunk, Document).join(
        Document, DocumentChunk.document_id == Document.id
    )

    if court:
        stmt = stmt.where(Document.court.ilike(f"%{court}%"))
    if year:
        stmt = stmt.where(Document.decision_date.like(f"%{year}%"))
    if judge:
        stmt = stmt.where(Document.judge.ilike(f"%{judge}%"))
    if is_gst is not None and is_gst.lower() in ("true", "false", "yes", "no"):
        is_gst_value = is_gst.lower() in ("true", "yes")
        stmt = stmt.where(Document.is_gst_core == is_gst_value)
    if decision_date:
        stmt = stmt.where(Document.decision_date.like(f"{decision_date}%"))

    stmt = stmt.order_by(
        DocumentChunk.embedding.cosine_distance(query_vector)
    ).limit(50)

    results = db.execute(stmt).all()

    search_results = []
    seen_doc_ids = set()

    for chunk, doc in results:
        if doc.id not in seen_doc_ids:
            search_results.append(
                SearchResult(
                    id=doc.id,
                    chunk_id=chunk.id,
                    case_id=doc.case_id,
                    title=doc.title,
                    citation=doc.citation,
                    content=chunk.chunk_content,
                    rrf_score=0.0,
                )
            )
            seen_doc_ids.add(doc.id)
            if len(search_results) >= 5:
                break

    return search_results


@app.get("/document/{id}")
def get_document(id: int, db: Session = Depends(get_database)):
    doc = db.query(Document).filter(Document.id == id).first()
    if not doc:
        return {"error": "Document not found"}
    return doc


@app.get("/document/{id}/summary")
def get_document_summary(id: int, db: Session = Depends(get_database)):
    doc = db.query(Document).filter(Document.id == id).first()
    if not doc:
        return {"error": "Document not found"}

    content = doc.content or doc.display_content or ""
    summary = content[:1000]
    if len(content) > 1000:
        summary += "..."

    return {"summary": summary}


# ==================== Authentication Endpoints ====================


@app.post("/auth/request-otp")
def request_otp(body: OTPRequest, db: Session = Depends(get_database)):
    identifier = body.identifier.strip().lower()

    if not is_email(identifier):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Please provide a valid email address",
        )

    provider = "email"

    # Ensure identity exists (creates user if needed)
    get_or_create_identity(db, identifier, provider)

    # Delete any existing OTPs for this identifier
    db.query(OTPCode).filter(OTPCode.identifier == identifier).delete()

    # Generate and store OTP
    otp = generate_otp()
    otp_record = OTPCode(
        identifier=identifier,
        otp_hash=hash_otp(otp),
        expires_at=datetime.now(timezone.utc)
        + timedelta(minutes=OTP_EXPIRY_MINUTES),
        attempts=0,
    )
    db.add(otp_record)
    db.commit()

    # Send OTP
    send_otp_email(identifier, otp)

    return {"message": "OTP sent", "identifier": identifier}


@app.post("/auth/verify-otp")
def verify_otp(
    body: OTPVerify,
    response: Response,
    db: Session = Depends(get_database),
):
    identifier = body.identifier.strip().lower()

    otp_record = (
        db.query(OTPCode)
        .filter(OTPCode.identifier == identifier)
        .first()
    )

    if not otp_record:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No OTP found. Please request a new one.",
        )

    # Check attempts
    if otp_record.attempts >= OTP_MAX_ATTEMPTS:
        db.delete(otp_record)
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many attempts. Please request a new OTP.",
        )

    # Check expiry (handle both naive and aware datetimes for SQLite compatibility)
    expires_at = otp_record.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) > expires_at:
        db.delete(otp_record)
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="OTP has expired. Please request a new one.",
        )

    # Check OTP
    if hash_otp(body.otp) != otp_record.otp_hash:
        otp_record.attempts += 1
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid OTP.",
        )

    # OTP is valid — clean up
    db.delete(otp_record)

    # Mark identity as verified
    provider = "email"
    identity = (
        db.query(UserIdentity)
        .filter(
            UserIdentity.provider == provider,
            UserIdentity.provider_id == identifier,
        )
        .first()
    )

    if not identity:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Identity not found.",
        )

    identity.is_verified = True
    user = identity.user

    # Check if profile needs completion
    needs_profile = not user.first_name or not user.last_name

    # If profile data provided, update user
    if body.first_name and body.last_name:
        user.first_name = body.first_name
        user.last_name = body.last_name
        if body.year_of_birth:
            user.year_of_birth = body.year_of_birth
        needs_profile = False

    db.commit()

    # Issue JWT
    token = create_access_token(data={"sub": str(user.id)})
    set_auth_cookie(response, token)

    return {
        "user": UserResponse.from_user(user).model_dump(),
        "needs_profile": needs_profile,
    }


@app.post("/auth/google")
def google_auth(
    body: GoogleAuthRequest,
    response: Response,
    db: Session = Depends(get_database),
):
    idinfo = verify_google_id_token(body.credential)

    email = idinfo.get("email")
    if not email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Google account has no email",
        )

    # Get or create google identity
    identity = (
        db.query(UserIdentity)
        .filter(
            UserIdentity.provider == "google",
            UserIdentity.provider_id == email,
        )
        .first()
    )

    if identity:
        user = identity.user
    else:
        # Check if email identity already exists (link accounts)
        email_identity = (
            db.query(UserIdentity)
            .filter(
                UserIdentity.provider == "email",
                UserIdentity.provider_id == email,
            )
            .first()
        )

        if email_identity:
            user = email_identity.user
        else:
            # Create new user
            from uuid import uuid4

            user = User(
                id=uuid4(),
                first_name=idinfo.get("given_name", ""),
                last_name=idinfo.get("family_name", ""),
            )
            db.add(user)
            db.flush()

        # Create google identity
        google_identity = UserIdentity(
            user_id=user.id,
            provider="google",
            provider_id=email,
            is_verified=True,
        )
        db.add(google_identity)
        db.commit()

    # Issue JWT
    token = create_access_token(data={"sub": str(user.id)})
    set_auth_cookie(response, token)

    needs_profile = not user.first_name or not user.last_name

    return {
        "user": UserResponse.from_user(user).model_dump(),
        "needs_profile": needs_profile,
    }


@app.post("/auth/logout")
def logout(response: Response):
    clear_auth_cookie(response)
    return {"message": "Logged out"}


@app.get("/auth/me")
def get_me(current_user: User = Depends(get_current_user_required)):
    return UserResponse.from_user(current_user)


@app.post("/auth/profile")
def update_profile(
    body: ProfileUpdate,
    db: Session = Depends(get_database),
    current_user: User = Depends(get_current_user_required),
):
    current_user.first_name = body.first_name
    current_user.last_name = body.last_name
    if body.year_of_birth is not None:
        current_user.year_of_birth = body.year_of_birth
    db.commit()
    db.refresh(current_user)
    return UserResponse.from_user(current_user)


# ==================== Staging-Only: Dev Login ====================

if os.getenv("ENVIRONMENT") == "staging":
    from pydantic import BaseModel as _BaseModel

    class DevLoginRequest(_BaseModel):
        email: str
        first_name: str = "Test"
        last_name: str = "User"

    @app.post("/auth/dev-login")
    def dev_login(
        body: DevLoginRequest,
        response: Response,
        db: Session = Depends(get_database),
    ):
        """Staging-only: bypass OTP/Google and log in directly."""
        identifier = body.email.strip().lower()
        identity, _ = get_or_create_identity(db, identifier, "email")
        identity.is_verified = True
        user = identity.user
        if not user.first_name:
            user.first_name = body.first_name
        if not user.last_name:
            user.last_name = body.last_name
        db.commit()

        token = create_access_token(data={"sub": str(user.id)})
        set_auth_cookie(response, token)
        return {
            "user": UserResponse.from_user(user).model_dump(),
            "needs_profile": False,
        }
