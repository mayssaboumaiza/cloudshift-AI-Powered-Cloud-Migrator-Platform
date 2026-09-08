"""
rag_message_model.py — SQLAlchemy ORM model for persistent RAG chat history.

One row per message (user or assistant) per migration.
Loaded on frontend mount; appended after every chat exchange.
"""
from datetime import datetime, timezone
from sqlalchemy import String, Text, Integer, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from configuration.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class RagMessage(Base):
    __tablename__ = "rag_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    migration_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("migrations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)          # "user" | "assistant"
    content: Mapped[str] = mapped_column(Text, nullable=False)
    sources_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
