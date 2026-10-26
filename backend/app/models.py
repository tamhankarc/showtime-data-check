"""Durable reference data and job metadata only. Deliberately no showtime table."""
from datetime import datetime, timezone
from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow():
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Country(Base):
    __tablename__ = "countries"
    code: Mapped[str] = mapped_column(String(3), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    timezone: Mapped[str] = mapped_column(String(80), default="UTC")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class Connection(Base):
    __tablename__ = "connections"
    __table_args__ = (UniqueConstraint("country_code", "provider_code"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    country_code: Mapped[str] = mapped_column(ForeignKey("countries.code"), index=True)
    provider_code: Mapped[str] = mapped_column(String(20))
    provider_country_code: Mapped[str] = mapped_column(String(10), default="")
    timezone: Mapped[str] = mapped_column(String(80), default="UTC")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_status: Mapped[str] = mapped_column(String(30), default="not_refreshed")
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    movie_count: Mapped[int] = mapped_column(Integer, default=0)
    catalog_window_days: Mapped[int | None] = mapped_column(Integer, nullable=True)


class MovieGroup(Base):
    __tablename__ = "movie_groups"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    country_code: Mapped[str] = mapped_column(ForeignKey("countries.code"), index=True)
    name: Mapped[str] = mapped_column(String(500))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class ProviderMovie(Base):
    __tablename__ = "provider_movies"
    __table_args__ = (UniqueConstraint("connection_id", "external_id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    connection_id: Mapped[int] = mapped_column(ForeignKey("connections.id"), index=True)
    external_id: Mapped[str] = mapped_column(String(160))
    name: Mapped[str] = mapped_column(String(500))
    original_name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    url_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    release_date: Mapped[datetime | None] = mapped_column(Date, nullable=True)
    parent_external_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("movie_groups.id"), index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    manually_grouped: Mapped[bool] = mapped_column(Boolean, default=False)


class Theater(Base):
    __tablename__ = "theaters"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    country_code: Mapped[str] = mapped_column(ForeignKey("countries.code"), index=True)
    name: Mapped[str] = mapped_column(String(500))
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    city: Mapped[str | None] = mapped_column(String(200), nullable=True)
    region: Mapped[str | None] = mapped_column(String(200), nullable=True)
    postal_code: Mapped[str | None] = mapped_column(String(60), nullable=True)
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    website: Mapped[str | None] = mapped_column(Text, nullable=True)
    chain: Mapped[str | None] = mapped_column(String(250), nullable=True)
    timezone: Mapped[str] = mapped_column(String(80), default="UTC")
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class ProviderTheater(Base):
    __tablename__ = "provider_theaters"
    __table_args__ = (UniqueConstraint("connection_id", "external_id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    connection_id: Mapped[int] = mapped_column(ForeignKey("connections.id"), index=True)
    external_id: Mapped[str] = mapped_column(String(160))
    name: Mapped[str] = mapped_column(String(500))
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    city: Mapped[str | None] = mapped_column(String(200), nullable=True)
    region: Mapped[str | None] = mapped_column(String(200), nullable=True)
    postal_code: Mapped[str | None] = mapped_column(String(60), nullable=True)
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    website: Mapped[str | None] = mapped_column(Text, nullable=True)
    chain: Mapped[str | None] = mapped_column(String(250), nullable=True)
    theater_id: Mapped[int | None] = mapped_column(ForeignKey("theaters.id"), nullable=True, index=True)
    review_status: Mapped[str] = mapped_column(String(30), default="needs_review")


class FormatMapping(Base):
    __tablename__ = "format_mappings"
    __table_args__ = (UniqueConstraint("country_code", "provider_code", "raw_format"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    country_code: Mapped[str] = mapped_column(String(3), default="")
    provider_code: Mapped[str] = mapped_column(String(20))
    raw_format: Mapped[str] = mapped_column(String(1000))
    published_format: Mapped[str] = mapped_column(String(500))
    audio_language: Mapped[str | None] = mapped_column(String(80), nullable=True)
    subtitle_language: Mapped[str | None] = mapped_column(String(80), nullable=True)
    is_dubbed: Mapped[bool] = mapped_column(Boolean, default=False)
    is_subtitled: Mapped[bool] = mapped_column(Boolean, default=False)
    is_luxury: Mapped[bool] = mapped_column(Boolean, default=False)


class MovieFormatOverride(Base):
    __tablename__ = "movie_format_overrides"
    __table_args__ = (UniqueConstraint("group_id", "format_mapping_id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("movie_groups.id"), index=True)
    format_mapping_id: Mapped[int] = mapped_column(ForeignKey("format_mappings.id"), index=True)
    published_format: Mapped[str] = mapped_column(String(500))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    kind: Mapped[str] = mapped_column(String(20))
    owner: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(30), default="queued", index=True)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[str] = mapped_column(String(500), default="Queued")
    parameters: Mapped[dict] = mapped_column(JSON, default=dict)
    provider_outcomes: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    username: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(20))
    credential_version: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class LoginThrottle(Base):
    __tablename__ = "login_throttles"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AppSetting(Base):
    __tablename__ = "app_settings"
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
