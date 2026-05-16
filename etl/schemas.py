from pydantic import BaseModel, ConfigDict, Field

ANALYSIS_FIELD_LABELS = {
    "summary": "Summary",
    "facts": "Facts",
    "issues": "Issues",
    "petitioner_arguments": "Petitioner's Arguments",
    "respondent_arguments": "Respondent's Arguments",
    "analysis_of_law": "Analysis of Law",
    "precedent_analysis": "Precedent Analysis",
    "courts_reasoning": "Court's Reasoning",
    "conclusion": "Conclusion",
    "ratio_decidendi": "Ratio Decidendi",
}


class AnalysisJSON(BaseModel):
    """LLM-produced structured analysis of a legal judgment (10 fields)."""

    model_config = ConfigDict(extra="ignore")

    summary: str
    facts: str
    issues: str
    petitioner_arguments: str
    respondent_arguments: str
    analysis_of_law: str
    precedent_analysis: str
    courts_reasoning: str
    conclusion: str
    ratio_decidendi: str


class MetadataJSON(BaseModel):
    """Case metadata sourced from parquet files or defaults."""

    model_config = ConfigDict(extra="ignore")

    case_id: str = Field(default="")
    title: str = Field(default="")
    petitioner: str = Field(default="Unknown")
    respondent: str = Field(default="Unknown")
    judge: str = Field(default="Unknown")
    citation: str = Field(default="Unknown")
    court: str = Field(default="Unknown Court")
    decision_date: str = Field(default="")
    disposal_nature: str = Field(default="")
