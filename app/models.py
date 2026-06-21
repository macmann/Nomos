from datetime import datetime
from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .database import Base

class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

class AppSetting(Base, TimestampMixin):
    __tablename__ = "app_settings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    value: Mapped[str | None] = mapped_column(Text)
    encrypted_value: Mapped[str | None] = mapped_column(Text)
    is_secret: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class AdminSession(Base, TimestampMixin):
    __tablename__ = "admin_sessions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_token: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    username: Mapped[str] = mapped_column(String(120))
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)

class Case(Base):
    __tablename__ = "cases"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    external_case_id: Mapped[str | None] = mapped_column(String(120), index=True)
    case_type: Mapped[str] = mapped_column(String(60), default="unknown")
    process_step: Mapped[str | None] = mapped_column(String(80))
    customer_name: Mapped[str | None] = mapped_column(String(255))
    customer_email: Mapped[str | None] = mapped_column(String(255))
    customer_address: Mapped[str | None] = mapped_column(Text)
    meter_number: Mapped[str | None] = mapped_column(String(120))
    market_location_number: Mapped[str | None] = mapped_column(String(120))
    registration_sent_at: Mapped[datetime | None] = mapped_column(DateTime)
    grid_operator_name: Mapped[str | None] = mapped_column(String(255))
    target_phone_number: Mapped[str | None] = mapped_column(String(60))
    problem_description: Mapped[str | None] = mapped_column(Text)
    required_outcome: Mapped[str | None] = mapped_column(Text)
    language_mode: Mapped[str] = mapped_column(String(20), default="fixed")
    preferred_language: Mapped[str] = mapped_column(String(10), default="de-DE")
    status: Mapped[str] = mapped_column(String(60), default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    calls = relationship("Call", back_populates="case")

class Call(Base, TimestampMixin):
    __tablename__ = "calls"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    twilio_call_sid: Mapped[str | None] = mapped_column(String(120), index=True)
    direction: Mapped[str] = mapped_column(String(30), default="outbound")
    from_number: Mapped[str | None] = mapped_column(String(60))
    to_number: Mapped[str | None] = mapped_column(String(60))
    status: Mapped[str] = mapped_column(String(60), default="initiating", index=True)
    detected_language: Mapped[str | None] = mapped_column(String(10))
    active_language: Mapped[str | None] = mapped_column(String(10))
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime)
    duration_seconds: Mapped[int | None] = mapped_column(Integer)
    recording_url: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)
    case = relationship("Case", back_populates="calls")
    transcripts = relationship("CallTranscript", back_populates="call")
    events = relationship("CallEvent", back_populates="call")
    state = relationship("CallState", back_populates="call", uselist=False)

class CallState(Base):
    __tablename__ = "call_state"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    call_id: Mapped[int] = mapped_column(ForeignKey("calls.id"), unique=True, index=True)
    phase: Mapped[str] = mapped_column(String(60), default="opening", index=True)
    language: Mapped[str | None] = mapped_column(String(10))
    known_operator_name: Mapped[str | None] = mapped_column(String(255))
    known_market_location_number: Mapped[str | None] = mapped_column(String(120))
    corrected_market_location_number: Mapped[str | None] = mapped_column(String(120))
    partial_malo_digits: Mapped[str | None] = mapped_column(String(120))
    meter_status: Mapped[str | None] = mapped_column(String(120))
    reference_number: Mapped[str | None] = mapped_column(String(120))
    next_action: Mapped[str | None] = mapped_column(String(120))
    last_agent_question: Mapped[str | None] = mapped_column(Text)
    waiting_for_field: Mapped[str | None] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    call = relationship("Call", back_populates="state")

class CallTranscript(Base, TimestampMixin):
    __tablename__ = "call_transcripts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    call_id: Mapped[int] = mapped_column(ForeignKey("calls.id"), index=True)
    speaker: Mapped[str] = mapped_column(String(30))
    text: Mapped[str] = mapped_column(Text)
    language: Mapped[str | None] = mapped_column(String(10))
    confidence: Mapped[float | None] = mapped_column(Float)
    timestamp_ms: Mapped[int | None] = mapped_column(Integer)
    source: Mapped[str | None] = mapped_column(String(30), default="stt")
    call = relationship("Call", back_populates="transcripts")

class CallEvent(Base, TimestampMixin):
    __tablename__ = "call_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    call_id: Mapped[int] = mapped_column(ForeignKey("calls.id"), index=True)
    event_type: Mapped[str] = mapped_column(String(80), index=True)
    event_payload: Mapped[dict | None] = mapped_column(JSON)
    call = relationship("Call", back_populates="events")

class CallExtraction(Base, TimestampMixin):
    __tablename__ = "call_extractions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    call_id: Mapped[int] = mapped_column(ForeignKey("calls.id"), index=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    outcome: Mapped[str | None] = mapped_column(String(60)); root_cause: Mapped[str | None] = mapped_column(Text)
    market_location_number: Mapped[str | None] = mapped_column(String(120)); corrected_market_location_number: Mapped[str | None] = mapped_column(String(120))
    meter_number: Mapped[str | None] = mapped_column(String(120)); meter_status: Mapped[str | None] = mapped_column(String(60))
    reference_number: Mapped[str | None] = mapped_column(String(120)); registration_status: Mapped[str | None] = mapped_column(String(60))
    next_action: Mapped[str] = mapped_column(String(80), default="none")
    plain_language_note: Mapped[str | None] = mapped_column(Text)
    extracted_json: Mapped[dict | None] = mapped_column(JSON)
    confidence: Mapped[float | None] = mapped_column(Float)

class ActionRun(Base, TimestampMixin):
    __tablename__ = "action_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    call_id: Mapped[int | None] = mapped_column(ForeignKey("calls.id"), index=True)
    action_type: Mapped[str] = mapped_column(String(80))
    action_status: Mapped[str] = mapped_column(String(60))
    action_payload: Mapped[dict | None] = mapped_column(JSON)
    error_message: Mapped[str | None] = mapped_column(Text)
