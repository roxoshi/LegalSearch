import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from sentence_transformers import SentenceTransformer
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import models
from .custom_types import SearchResult
from .database import engine, get_database
from .models import Document, DocumentChunk

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

    # Base statement joining DocumentChunk and Document
    stmt = select(DocumentChunk, Document).join(Document, DocumentChunk.document_id == Document.id)

    # Apply filters if provided
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

    # Fetch more candidates to ensure we find unique documents
    stmt = stmt.order_by(DocumentChunk.embedding.cosine_distance(query_vector)).limit(50)

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
