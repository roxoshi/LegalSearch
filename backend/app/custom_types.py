from pydantic import BaseModel
class SearchResult(BaseModel):
    id: int
    chunk_id: int
    case_id: str
    title: str
    citation: str
    content: str
    rrf_score: float