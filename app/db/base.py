"""Shared SQLAlchemy base classes."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Base declarative type for ORM models."""
