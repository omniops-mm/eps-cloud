"""The single row of user preferences.

The settings table holds exactly one row, enforced by a check constraint. A
brand new install has none at all, so reading has to tolerate that and fall back
to the same value the column default would have written.

The timezone is deliberately not here. It is deployment configuration, read from
the environment by app.clock, because it decides which calendar day every entry
belongs to and must not depend on a query that can fail.
"""

from sqlalchemy.orm import Session

from app.models import UserSettings

GRACE_DEFAULT = True


def read(session: Session) -> UserSettings | None:
    """The saved preferences, or None if none have ever been saved."""
    return session.get(UserSettings, 1)


def ensure(session: Session) -> UserSettings:
    """The preferences row, created with its defaults if it is missing.

    For paths that are already writing, such as the settings form. Read paths
    use read() instead, so opening a page never writes.
    """
    row = session.get(UserSettings, 1)
    if row is None:
        row = UserSettings(id=1)
        session.add(row)
        session.flush()
    return row


def grace_enabled(session: Session) -> bool:
    """Whether a long streak may absorb a missed day."""
    row = read(session)
    return GRACE_DEFAULT if row is None else row.grace_enabled
