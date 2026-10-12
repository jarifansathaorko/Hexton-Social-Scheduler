"""Column types that keep the storage convention honest.

The convention in this application is that **every timestamp is UTC**. That sounds
obvious until you know what SQLAlchemy's SQLite dialect actually does with a
``DateTime``: it renders the value from its *wall clock* and discards the offset, and
on the way back it returns a naive ``datetime``.

Two concrete failures follow, and both are invisible until someone schedules a job in
a zone that is not UTC:

1. ``next_attempt_at <= now`` compares the stored wall clock against ``now``'s wall
   clock. Storing ``2026-10-11 18:00+06:00`` (Dhaka) and comparing it with
   ``2026-10-11 12:00Z`` — the *same instant* — fails, because the driver compares
   ``18:00`` with ``12:00``. A job would be skipped or taken early depending only on
   which zone the operator's PC is set to.
2. :func:`hexton.domain.scheduling.ensure_utc` refuses a naive value, and the API's
   formatting helpers call it. So a read of any populated timestamp column raises
   ``ValueError`` and the request fails.

Normalising in a :class:`~sqlalchemy.types.TypeDecorator` fixes both at once and makes
the rule true regardless of what any individual call site passes, rather than relying
on every one of them to remember.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator


class UTCDateTime(TypeDecorator):
    """A ``DateTime`` that is always bound and returned in UTC.

    A naive value written by a caller is taken to already be UTC — the schema's
    convention — rather than being guessed from the machine's zone, which is exactly
    the silent shift this type exists to prevent.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> Any:
        if value is None or value.tzinfo is None:
            return value
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> Any:
        if value is None or value.tzinfo is not None:
            return value
        return value.replace(tzinfo=UTC)
