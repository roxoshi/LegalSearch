from typing import List, Optional
import os

from fastapi import FastAPI, Depends, Query, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from sqlalchemy import select
from sentence_transformers import SentenceTransformer
from fastapi.middleware.cors import CORSMiddleware
from .database import get_database, engine
from contextlib import asynccontextmanager
from . import models
from .models import Document, DocumentChunk, User
from .custom_types import SearchResult
from .auth import (
    UserCreate, UserLogin, Token, UserResponse,
    create_user, authenticate_user, create_access_token,
    get_user_by_email, get_current_user, get_current_user_required,
    get_google_user_info, get_or_create_google_user,
    ACCESS_TOKEN_EXPIRE_MINUTES
)
from datetime import timedelta
from pydantic import BaseModel

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# CONFIG = {}

embed_model = SentenceTransformer(os.getenv("MODEL_NAME", "sentence-transformers/all-MiniLM-L6-v2"))

# MOCK_DOCS = [
#     {"id": 1, "title": "MLOps Best Practices", "content": "Deployment and monitoring of machine learning models in production."},
#     {"id": 2, "title": "Vector Databases", "content": "How to store embeddings for efficient similarity search using pgvector."},
#     {"id": 3, "title": "FastAPI Guide", "content": "Building high-performance web APIs with Python and Pydantic."},
#     {"id": 4, "title": "Next.js Fundamentals", "content": "Server-side rendering and static site generation for modern web apps."},
#     {"id": 5, "title": "PostgreSQL Full Text", "content": "Using GIN indexes and tsvectors for fast keyword searching."}
# ]

# # Pre-calculate embeddings for mock docs
# doc_texts = [d["content"] for d in MOCK_DOCS]
# doc_embeddings = embed_model.encode(doc_texts, convert_to_tensor=True)


@asynccontextmanager
async def lifespan(app):
    models.Base.metadata.create_all(bind=engine)

@app.get("/search")
def vector_search(q: str = Query(...),
                  court: str = Query(None),
                  year: str = Query(None),
                  judge: str = Query(None),
                  is_gst: str = Query(None),
                  decision_date: str = Query(None),
                  db: Session = Depends(get_database)) -> List[SearchResult]:
    query_vector = embed_model.encode(q).tolist()

    # Base statement joining DocumentChunk and Document
    stmt = select(DocumentChunk, Document).join(Document, DocumentChunk.document_id == Document.id)

    # Apply filters if provided
    if court:
        stmt = stmt.where(Document.court.ilike(f"%{court}%"))

    if year:
        stmt = stmt.where(Document.decision_date.like(f"%{year}%"))

    if judge:
        stmt = stmt.where(Document.judge.ilike(f"%{judge}%"))

    if is_gst is not None and is_gst.lower() in ('true', 'false', 'yes', 'no'):
        is_gst_value = is_gst.lower() in ('true', 'yes')
        stmt = stmt.where(Document.is_gst_core == is_gst_value)

    if decision_date:
        stmt = stmt.where(Document.decision_date.like(f"{decision_date}%"))

    # Fetch more candidates to ensure we find unique documents
    stmt = (
        stmt
        .order_by(DocumentChunk.embedding.cosine_distance(query_vector))
        .limit(50)
    )

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
                    rrf_score=0.0
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
    """
    Generate an AI summary for a document.
    Currently returns first 1000 characters as a mock implementation.
    TODO: Replace with actual LLM API call (OpenAI, Anthropic, etc.)
    """
    doc = db.query(Document).filter(Document.id == id).first()
    if not doc:
        return {"error": "Document not found"}

    # Mock LLM response - return first 1000 characters
    # Replace this with actual LLM API call in production
    content = doc.content or doc.display_content or ""
    summary = content[:1000]
    if len(content) > 1000:
        summary += "..."

    return {"summary": summary}


# ==================== Authentication Endpoints ====================

@app.post("/auth/signup", response_model=UserResponse)
def signup(user: UserCreate, db: Session = Depends(get_database)):
    """Register a new user with email and password."""
    # Check if user already exists
    existing_user = get_user_by_email(db, user.email)
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered"
        )

    # Validate password
    if len(user.password) < 8:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password must be at least 8 characters"
        )

    return create_user(db, user)


@app.post("/auth/login", response_model=Token)
def login(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_database)):
    """Login with email and password."""
    user = authenticate_user(db, form_data.username, form_data.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    access_token = create_access_token(
        data={"sub": user.email},
        expires_delta=timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    return {"access_token": access_token, "token_type": "bearer"}


class GoogleAuthRequest(BaseModel):
    access_token: str


@app.post("/auth/google", response_model=Token)
async def google_auth(request: GoogleAuthRequest, db: Session = Depends(get_database)):
    """Authenticate with Google OAuth token."""
    try:
        google_user = await get_google_user_info(request.access_token)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid Google token"
        )

    user = get_or_create_google_user(db, google_user)

    access_token = create_access_token(
        data={"sub": user.email},
        expires_delta=timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    return {"access_token": access_token, "token_type": "bearer"}


@app.get("/auth/me", response_model=UserResponse)
def get_current_user_info(current_user: User = Depends(get_current_user_required)):
    """Get current authenticated user info."""
    return current_user
