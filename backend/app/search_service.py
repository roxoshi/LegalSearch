from sqlalchemy import select
from sqlalchemy.orm import Session
from .models import Document, DocumentChunk

def search_legal_cases(db: Session, model, query_text: str, limit: int = 5):
    query_embedding = model.encode(query_text).tolist()
    stmt = (
        select(DocumentChunk, Document)
        .join(Document)
        .order_by(DocumentChunk.embedding.cosine_distance(query_embedding))
        .limit(limit)
    )
    
    results = db.execute(stmt).all()

    formatted_results = []
    for chunk, doc in results:
        formatted_results.append({
            "case_title": doc.title,
            "citation": doc.citation,
            "snippet": chunk.chunk_content,
            "case_id": doc.case_id
        })
    
    return formatted_results
