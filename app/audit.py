"""
Automatic audit log: every insert/update/delete that goes through a SQLAlchemy
session is recorded (who, when, which record, old -> new values), so new
features get logged without having to remember to add logging to them.
"""

import enum
import json
from contextvars import ContextVar
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import event, inspect
from sqlalchemy.orm import Session

from app.models import AuditLog

current_user_email: ContextVar[Optional[str]] = ContextVar("current_user_email", default=None)

SKIP_TABLES = {"audit_log"}
SKIP_COLUMNS = {"created_at", "updated_at", "last_login_at"}
LABEL_FIELDS = ("address", "zip_code", "name", "email", "month", "description")


def _jsonable(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, enum.Enum):
        return value.value
    return value


def _label(obj) -> Optional[str]:
    for field in LABEL_FIELDS:
        value = getattr(obj, field, None) if field in inspect(obj).mapper.column_attrs else None
        if value not in (None, ""):
            return str(_jsonable(value))[:255]
    return None


def _snapshot(obj) -> dict:
    state = inspect(obj)
    return {
        attr.key: _jsonable(getattr(obj, attr.key))
        for attr in state.mapper.column_attrs
        if attr.key not in SKIP_COLUMNS and attr.key != "id"
    }


def _changes(obj) -> dict:
    state = inspect(obj)
    changes = {}
    for attr in state.mapper.column_attrs:
        if attr.key in SKIP_COLUMNS:
            continue
        history = state.attrs[attr.key].history
        if history.has_changes():
            old = history.deleted[0] if history.deleted else None
            new = history.added[0] if history.added else None
            if _jsonable(old) != _jsonable(new):
                changes[attr.key] = [_jsonable(old), _jsonable(new)]
    return changes


def _entry(action: str, obj, changes: dict) -> dict:
    return {
        "created_at": datetime.utcnow(),
        "user_email": current_user_email.get() or "system",
        "action": action,
        "table_name": obj.__tablename__,
        "record_id": getattr(obj, "id", None),
        "label": _label(obj),
        "changes": json.dumps(changes, ensure_ascii=False) if changes else None,
    }


@event.listens_for(Session, "after_flush")
def _record_changes(session: Session, flush_context) -> None:
    rows = []
    for obj in session.new:
        if getattr(obj, "__tablename__", None) not in SKIP_TABLES:
            rows.append(_entry("create", obj, _snapshot(obj)))
    for obj in session.dirty:
        if getattr(obj, "__tablename__", None) in SKIP_TABLES or not session.is_modified(obj, include_collections=False):
            continue
        changes = _changes(obj)
        if changes:
            rows.append(_entry("update", obj, changes))
    for obj in session.deleted:
        if getattr(obj, "__tablename__", None) not in SKIP_TABLES:
            rows.append(_entry("delete", obj, _snapshot(obj)))
    if rows:
        session.connection().execute(AuditLog.__table__.insert(), rows)


def log_event(session: Session, action: str, label: str) -> None:
    """For events that aren't a data change, like signing in."""
    session.connection().execute(
        AuditLog.__table__.insert(),
        [{
            "created_at": datetime.utcnow(),
            "user_email": current_user_email.get() or "system",
            "action": action,
            "table_name": "session",
            "record_id": None,
            "label": label,
            "changes": None,
        }],
    )
