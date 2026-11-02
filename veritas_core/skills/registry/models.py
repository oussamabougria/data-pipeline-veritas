"""SQLAlchemy models for the skill registry."""

from __future__ import annotations

from sqlalchemy import Boolean, Column, String, Text, ARRAY
from sqlalchemy.dialects.postgresql import UUID, TIMESTAMPTZ
from sqlalchemy.orm import DeclarativeBase
import uuid


class Base(DeclarativeBase):
    pass


class SkillRegistryRecord(Base):
    __tablename__ = "skill_registry"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    skill_id = Column(String(64), nullable=False)
    version = Column(String(16), nullable=False)
    author = Column(String(128), nullable=False)
    archetype = Column(String(12), nullable=False)
    tags = Column(ARRAY(Text), nullable=False, default=list)
    format = Column(String(8), nullable=False)
    content = Column(Text, nullable=False)
    source = Column(String(32), nullable=False)
    iso_standards = Column(ARRAY(Text), nullable=False, default=list)
    checksum_sha256 = Column(String(64), nullable=False)
    validated = Column(Boolean, nullable=False, default=False)
    active = Column(Boolean, nullable=False, default=False)
