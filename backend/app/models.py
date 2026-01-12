import os

from sqlalchemy import Column, Integer, Text, Computed
from pgvector.sqlalchemy import Vector
from .database import Base

class Document(Base):
    __tablename__ = "documents"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(Text, nullable=False)
    content = Column(Text, nullable=False)

    #pgvector column
    # TODO - remove this hardcoding later
    embedding = Column(Vector(384))