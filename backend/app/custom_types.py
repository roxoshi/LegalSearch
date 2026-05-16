from pydantic import BaseModel


class SearchResult(BaseModel):
    id: int
    chunk_id: int
    case_id: str
    title: str
    citation: str
    content: str
    court: str
    decision_date: str
    rrf_score: float


class PartyOptions(BaseModel):
    petitioners: list[str]
    respondents: list[str]
