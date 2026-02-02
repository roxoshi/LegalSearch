from pydantic import BaseModel, Field

class DocumentJSON(BaseModel):
    case_id: str
    title: str
    petitioner: str = Field(default="Unknown")
    respondent: str = Field(default="Unknown")
    judge: str = Field(default="Unknown")
    citation: str = Field(default="Unknown")
    court: str = Field(default="Unknown Court")
    decision_date: str = Field(..., description="Judgment date (YYYY-MM-DD or similar)")
    
    text_content: str = Field(..., description="Text used for search indexing")
    
    class Config:
        extra = "ignore"
