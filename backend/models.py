from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base


class Trace(Base):
    __tablename__ = "traces"

    id: Mapped[int] = mapped_column(primary_key=True)
    trace_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    span_id: Mapped[str] = mapped_column(String(32), index=True)
    service_name: Mapped[str] = mapped_column(String(256))
    operation_name: Mapped[str] = mapped_column(String(256))
    error_message: Mapped[str | None] = mapped_column(Text)
    status_code: Mapped[int] = mapped_column(Integer)
    duration_ms: Mapped[float] = mapped_column(Float)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(768))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Incident(Base):
    __tablename__ = "incidents"

    id: Mapped[int] = mapped_column(primary_key=True)
    trace_id: Mapped[str] = mapped_column(String(64), ForeignKey("traces.trace_id"))
    root_cause: Mapped[str] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(String(16))  # LOW/MEDIUM/HIGH/CRITICAL
    recommendations: Mapped[str] = mapped_column(Text)  # JSON string
    pdf_path: Mapped[str | None] = mapped_column(String(512))
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)
    is_pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    severity_rank: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
