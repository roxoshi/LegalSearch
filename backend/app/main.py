from typing import List

from fastapi import FastAPI, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import text
from sentence_transformers import SentenceTransformer, util
from fastapi.middleware.cors import CORSMiddleware

from .database import get_database, engine
from contextlib import asynccontextmanager
from . import models
from .custom_types import SearchResult

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# CONFIG = {}

embed_model = SentenceTransformer("all-MiniLM-L6-v2")

MOCK_DOCS = [
    {"id": 1, "title": "MLOps Best Practices", "content": "Deployment and monitoring of machine learning models in production."},
    {"id": 2, "title": "Vector Databases", "content": "How to store embeddings for efficient similarity search using pgvector."},
    {"id": 3, "title": "FastAPI Guide", "content": "Building high-performance web APIs with Python and Pydantic."},
    {"id": 4, "title": "Next.js Fundamentals", "content": "Server-side rendering and static site generation for modern web apps."},
    {"id": 5, "title": "PostgreSQL Full Text", "content": "Using GIN indexes and tsvectors for fast keyword searching."}
]

# Pre-calculate embeddings for mock docs
doc_texts = [d["content"] for d in MOCK_DOCS]
doc_embeddings = embed_model.encode(doc_texts, convert_to_tensor=True)


@asynccontextmanager
async def lifespan(app):
    models.Base.metadata.create_all(bind=engine)

# @app.get("/search")
# def hybrid_search(q: str = Query(...),
#                   db: Session = Depends(get_database)) -> List[SearchResult]:
#     query_vector = embed_model.encode(q).tolist()
#
#     # Using RRF (Reciprocal Rank Fusion) for hybrid search
#     hybrid_query = text("""
#         WITH semantic_search AS (
#             SELECT id, RANK() OVER (ORDER BY embeding <=> :vector) AS rank
#             FROM documents
#             ORDER BY embedding <=> :vector
#             LIMIT 50;
#         ),
#         keyword_search AS (
#             SELECT id, RANK() OVER (ORDER BY ts_rank_cd(to_tsvector('english', content),
#                 plainto_tsquery('english', :query)) DESC) AS rank
#             FROM documents
#             WHERE to_tsvector('english', content) @@ plainto_tsquery('english', :query)
#             ORDER BY rank ASC
#             LIMIT 50;
#         )
#         SELECT COALESCE(s.id, k.id) as id,
#             (COALESCE(1.0 / (60 + s.rank), 0.0) + COALESCE(1.0 / (60 + k.rank), 0.0)) AS score
#         FROM semantic_search s
#         FULL OUTER JOIN keyword_search k ON s.id = k.id
#         ORDER BY score DESC
#         LIMIT 10;
#     """)
#
#     results = db.execute(hybrid_query, {"vector": str(query_vector), "query": q}).fetchall()
#     # TODO make this return type pytdan
#     return [SearchResult(id=r.id, rrf_score=r.score) for r in results]

@app.get("/search")
def mock_search(q: str = Query(...)):
    # 1. Simulate Vector Search
    query_embedding = embed_model.encode(q, convert_to_tensor=True)
    cos_scores = util.cos_sim(query_embedding, doc_embeddings)[0]

    # 2. Package results with "mock" scores
    results = []
    for i, doc in enumerate(MOCK_DOCS):
        # We'll just use the cosine similarity as a mock RRF score
        score = float(cos_scores[i])

        # Simple keyword boost (mocking hybrid)
        if q.lower() in doc["content"].lower():
            score += 0.5

        # results.append({
        #     "id": doc["id"],
        #     "title": doc["title"],
        #     "rrf_score": score
        # })
        results.append(
            SearchResult(id=doc["id"], title=doc["title"], rrf_score=score)
        )

    # Sort by score descending
    return sorted(results, key=lambda x: x.rrf_score, reverse=True)