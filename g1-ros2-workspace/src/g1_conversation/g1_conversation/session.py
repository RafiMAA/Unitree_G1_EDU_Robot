"""Per-passenger session management with PII-safe cleanup.

All passenger state lives in a single PassengerSession dataclass.
SessionManager.end_session() overwrites PII fields before dropping the
reference so nothing lingers in memory longer than necessary.
"""

import gc
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class PassengerSession:
    """All per-passenger state lives here.

    Nothing about a passenger is allowed to live anywhere else — no stray
    ``self.passenger_name`` on the node, no globals.
    """

    session_id: str
    started_at: datetime
    language: str = "en"
    passenger_name: Optional[str] = None
    phone_number: Optional[str] = None
    booking_id: Optional[str] = None
    turn_count: int = 0
    onboarding_stage: str = "awaiting_name"

    def is_active(self) -> bool:
        return True


class SessionManager:
    """Manages one passenger session at a time.

    Lifecycle::

        mgr = SessionManager()
        session = mgr.start_session()          # passenger approaches
        session.language = "fr"                 # detected from speech
        session.turn_count += 1
        ...
        mgr.end_session("passenger_goodbye")   # clears all PII
    """

    def __init__(self) -> None:
        self._session: Optional[PassengerSession] = None

    def start_session(self, session_id: Optional[str] = None) -> PassengerSession:
        """Start a new passenger session, replacing any lingering one."""
        if self._session is not None:
            self.end_session("replaced_by_new_session")

        if session_id is None:
            session_id = str(uuid.uuid4())

        self._session = PassengerSession(
            session_id=session_id,
            started_at=datetime.now(),
        )
        return self._session

    @property
    def current(self) -> Optional[PassengerSession]:
        return self._session

    @property
    def has_active_session(self) -> bool:
        return self._session is not None

    def end_session(self, reason: str = "unknown") -> None:
        """Clear all passenger data.

        Called when:
        - Passenger says goodbye / thank you
        - Vision system confirms they boarded the vehicle
        - Idle timeout (e.g. 90 s without speech)
        - Explicit reset command

        PII fields are overwritten before the reference is dropped.
        """
        if self._session is not None:
            # Overwrite PII — don't just drop the reference
            self._session.passenger_name = None
            self._session.phone_number = None
            self._session.booking_id = None

        self._session = None
        gc.collect()  # encourage prompt cleanup on a robot loop
