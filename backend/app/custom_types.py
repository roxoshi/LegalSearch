from pydantic import BaseModel

class SearchResult(BaseModel):
    id: int
    title: str
    rrf_score: float