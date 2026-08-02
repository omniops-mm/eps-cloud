"""Audit trail for event-table changes.

Appending today's row is normal daily use and is not audited. Everything else,
editing any value, deleting a row, or writing into a past day, gets one
edit_log row per changed field. daily_notes is exempt: prose changes on every
keystroke and auditing it would store the full text twice per typo.
"""

import datetime

from sqlalchemy.orm import Session

from app.models import EditLog


def maybe_audit(
    session: Session,
    *,
    table: str,
    row_id: str,
    field: str,
    old: object,
    new: object,
    entry_date: datetime.date,
    today: datetime.date,
) -> None:
    """Write an edit_log row unless this is today's row being created."""
    is_new_row = old is None
    if is_new_row and entry_date == today:
        return
    if old == new:
        return
    session.add(
        EditLog(
            table_name=table,
            row_id=row_id,
            field=field,
            old_value=None if old is None else str(old),
            new_value=None if new is None else str(new),
        )
    )
