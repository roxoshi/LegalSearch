
from sqlalchemy import Column, Integer, Text, ForeignKey, Boolean
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import relationship
from pgvector.sqlalchemy import Vector
from .database import Base

class Document(Base):
    __tablename__ = "documents"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(Text, nullable=False)
    petitioner = Column(Text, nullable=False)
    respondent = Column(Text, nullable=False)
    judge = Column(Text, nullable=False)
    citation = Column(Text, nullable=False)
    decision_date = Column(Text, nullable=False)
    court = Column(Text, nullable=False)
    case_id = Column(Text, nullable=False, unique=True)
    content = Column(Text, nullable=False)
    display_content = Column(Text, nullable=True)

    # ML-extracted fields from filter step
    is_gst_core = Column(Boolean, nullable=True, default=None)
    extracted_provisions = Column(ARRAY(Text), nullable=True)
    extracted_statutes = Column(ARRAY(Text), nullable=True)

    chunks = relationship("DocumentChunk", back_populates="document")

class DocumentChunk(Base):
    __tablename__ = "document_chunks"

    id = Column(Integer,primary_key=True, index=True)
    document_id = Column(Integer, ForeignKey("documents.id"), nullable=False)
    chunk_content = Column(Text, nullable=False)
    embedding = Column(Vector(384))
    document = relationship("Document", back_populates="chunks")
