import enum
from datetime import datetime
from flask_login import UserMixin
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy import String, Text, Integer, DateTime, ForeignKey, Enum, JSON, Float

class Base(DeclarativeBase):
    pass

class Role(enum.Enum):
    ADMIN = "ADMIN"
    REVIEWER = "REVIEWER"

class Status(enum.Enum):
    UPLOADED = "UPLOADED"
    PROCESSING = "PROCESSING"
    READY = "READY"
    FAILED = "FAILED"

class GapStatus(enum.Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    READY = "READY"
    FAILED = "FAILED"

class GapLabel(enum.Enum):
    SUPPORTED = "SUPPORTED"
    CONTRADICTED = "CONTRADICTED"
    UNKNOWN = "UNKNOWN"

class User(Base, UserMixin):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[Role] = mapped_column(Enum(Role), default=Role.ADMIN)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(64))
    entity_type: Mapped[str] = mapped_column(String(64))
    entity_id: Mapped[int] = mapped_column(Integer)
    meta_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

class Interview(Base):
    __tablename__ = "interviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(255))
    company_domain: Mapped[str | None] = mapped_column(String(255), nullable=True)
    language: Mapped[str] = mapped_column(String(16), default="fi")
    audio_storage_key: Mapped[str] = mapped_column(nullable=False)
    is_finnish: Mapped[bool] = mapped_column(default=True)
    status: Mapped[Status] = mapped_column(Enum(Status), default=Status.UPLOADED)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


    texts: Mapped[list["InterviewText"]] = relationship(back_populates="interview")

class InterviewText(Base):
    __tablename__ = "interview_texts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    interview_id: Mapped[int] = mapped_column(ForeignKey("interviews.id"))
    transcript_fi: Mapped[str | None] = mapped_column(Text, nullable=True)
    transcript_en: Mapped[str | None] = mapped_column(Text, nullable=True)
    transcript_en_storage_key: Mapped[str | None] = mapped_column(nullable=True)
    model_asr: Mapped[str | None] = mapped_column(String(255), nullable=True)
    model_translation: Mapped[str | None] = mapped_column(String(255), nullable=True)
    segments_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    interview: Mapped["Interview"] = relationship(back_populates="texts")

class SupportDoc(Base):
    __tablename__ = "support_docs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(255))
    source_type: Mapped[str] = mapped_column(String(32), default="pdf")  # pdf/html/docx/text
    file_storage_key: Mapped[str] = mapped_column(nullable=False)
    is_finnish: Mapped[bool] = mapped_column(default=False)
    extracted_text_en_storage_key: Mapped[str | None] = mapped_column(nullable=True)
    status: Mapped[Status] = mapped_column(Enum(Status), default=Status.UPLOADED)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class LightRagIndexRef(Base):
    __tablename__ = "lightrag_index_refs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(64))  # INTERVIEW_TEXT / SUPPORT_DOC
    entity_id: Mapped[int] = mapped_column(Integer)
    lightrag_namespace: Mapped[str] = mapped_column(String(255), default="default")
    lightrag_doc_ids_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    indexed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

class GapReport(Base):
    __tablename__ = "gap_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    interview_id: Mapped[int] = mapped_column(ForeignKey("interviews.id"))
    support_doc_ids_json: Mapped[dict] = mapped_column(JSON)  # {"doc_ids":[1,2,3]}
    status: Mapped[GapStatus] = mapped_column(Enum(GapStatus), default=GapStatus.QUEUED)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    summary_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    pdf_path: Mapped[str | None] = mapped_column(String(500), nullable=True)

class GapItem(Base):
    __tablename__ = "gap_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    report_id: Mapped[int] = mapped_column(ForeignKey("gap_reports.id"))
    claim_text: Mapped[str] = mapped_column(Text)
    claim_refs_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    label: Mapped[GapLabel] = mapped_column(Enum(GapLabel), default=GapLabel.UNKNOWN)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    doc_evidence_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
