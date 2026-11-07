"""Sequential, in-memory workshop registration and FIFO promotion."""

from datetime import datetime, timedelta, timezone


class WorkshopRegistration:
    """Manage two places until a required UTC-aware start time."""

    def __init__(self, start_time, clock=None):
        self._validate_time(start_time)
        if clock is not None and not callable(clock):
            raise ValueError("clock must be callable")
        self._start_time = start_time
        self._clock = clock if clock is not None else lambda: datetime.now(timezone.utc)
        self._confirmed = []
        self._waitlist = []
        self._pending_places = 0

    @staticmethod
    def _validate_time(value):
        if not isinstance(value, datetime) or value.utcoffset() != timedelta(0):
            raise ValueError("time must be a UTC-aware datetime with zero offset")

    @staticmethod
    def _validate_id(participant_id):
        if not isinstance(participant_id, str) or not participant_id.strip():
            raise ValueError("participant ID must be a nonblank string")

    def _before_start(self):
        now = self._clock()
        self._validate_time(now)
        if now >= self._start_time:
            raise ValueError("workshop has started")
        return now

    @property
    def confirmed(self):
        """Immutable snapshot in confirmation order."""
        return tuple(self._confirmed)

    @property
    def waitlist(self):
        """Immutable snapshot in arrival order."""
        return tuple(self._waitlist)

    @property
    def pending_places(self):
        """Number of free places reserved for explicit staff promotion."""
        return self._pending_places

    def register(self, participant_id):
        """Return 'confirmed' or 'waitlisted'; reject invalid/duplicate IDs."""
        self._before_start()
        self._validate_id(participant_id)
        if participant_id in self._confirmed or participant_id in self._waitlist:
            raise ValueError("participant ID is already registered")
        if not self._waitlist and len(self._confirmed) + self._pending_places < 2:
            self._confirmed.append(participant_id)
            return "confirmed"
        self._waitlist.append(participant_id)
        return "waitlisted"

    def cancel(self, participant_id):
        """Cancel an existing ID, applying the strict 120-minute cutoff."""
        now = self._before_start()
        self._validate_id(participant_id)
        if participant_id in self._waitlist:
            self._waitlist.remove(participant_id)
            return None
        if participant_id not in self._confirmed:
            raise ValueError("participant ID is not registered")
        self._confirmed.remove(participant_id)
        if self._start_time - now > timedelta(minutes=120):
            if self._waitlist:
                self._confirmed.append(self._waitlist.pop(0))
        else:
            self._pending_places += 1
        return None

    def promote(self):
        """Explicitly fill one free place with the oldest waiting ID."""
        self._before_start()
        if len(self._confirmed) == 2 or not self._waitlist:
            raise ValueError("promotion needs a free place and a waiting participant")
        participant_id = self._waitlist.pop(0)
        self._confirmed.append(participant_id)
        if self._pending_places:
            self._pending_places -= 1
        return participant_id
