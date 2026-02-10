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

    # ML-extracted fields from filter step
    is_gst_core: bool | None = Field(default=None, description="Whether this is a core GST case")
    extracted_provisions: list[str] | None = Field(
        default=None, description="Legal provisions extracted by NER"
    )
    extracted_statutes: list[str] | None = Field(
        default=None, description="Statutes extracted by NER"
    )

    class Config:
        extra = "ignore"
