from typing import List
import os

from fastapi import FastAPI, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import select
from sentence_transformers import SentenceTransformer
from fastapi.middleware.cors import CORSMiddleware
from .database import get_database, engine
from contextlib import asynccontextmanager
from . import models
from .models import Document, DocumentChunk
from .custom_types import SearchResult

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
                  db: Session = Depends(get_database)) -> List[SearchResult]:
    query_vector = embed_model.encode(q).tolist()

    # Base statement joining DocumentChunk and Document
    stmt = select(DocumentChunk, Document).join(Document, DocumentChunk.document_id == Document.id)
    
    # Apply filters if provided
    if court:
        stmt = stmt.where(Document.court.ilike(f"%{court}%"))
    
    if year:
        # Assuming decision_date is stored as string like "YYYY-MM-DD" or similar
        # Since it is just standard text search on a text column
        stmt = stmt.where(Document.decision_date.like(f"%{year}%"))

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
