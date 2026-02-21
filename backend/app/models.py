from uuid import uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import relationship

from .database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    first_name = Column(String(50), nullable=False)
    last_name = Column(String(50), nullable=False)
    year_of_birth = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    identities = relationship(
        "UserIdentity", back_populates="user", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint("year_of_birth > 1900", name="ck_users_year_of_birth"),
    )


class UserIdentity(Base):
    __tablename__ = "user_identities"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    provider = Column(String(20), nullable=False)  # 'email', 'phone', 'google'
    provider_id = Column(String(255), nullable=False)
    is_verified = Column(Boolean, default=False)

    user = relationship("User", back_populates="identities")

    __table_args__ = (
        UniqueConstraint("provider", "provider_id", name="uq_identity_provider_id"),
    )


class OTPCode(Base):
    __tablename__ = "otp_codes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    identifier = Column(String(255), index=True, nullable=False)
    otp_hash = Column(String(255), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    attempts = Column(Integer, default=0)


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
    extracted_provisions: Column = Column(ARRAY(Text), nullable=True)
    extracted_statutes: Column = Column(ARRAY(Text), nullable=True)

    # Timestamps for sync support
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    chunks = relationship("DocumentChunk", back_populates="document")


class DocumentChunk(Base):
    __tablename__ = "document_chunks"

    id = Column(Integer, primary_key=True, index=True)
    document_id = Column(Integer, ForeignKey("documents.id"), nullable=False)
    chunk_content = Column(Text, nullable=False)
    embedding = Column(Vector(384))
    document = relationship("Document", back_populates="chunks")
