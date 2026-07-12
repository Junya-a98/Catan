from dataclasses import dataclass


DEFAULT_FEEDBACK_DURATION_MS = 2400


@dataclass(frozen=True)
class FeedbackMessage:
    text: str
    level: str = "info"
    expires_at_ms: int = 0


class FeedbackManager:
    def __init__(self):
        self._current = None

    def show(self, text, *, level="info", now_ms=0, duration_ms=DEFAULT_FEEDBACK_DURATION_MS):
        self._current = FeedbackMessage(
            text=text,
            level=level,
            expires_at_ms=now_ms + duration_ms,
        )

    def clear(self):
        self._current = None

    def get_active(self, now_ms):
        if self._current is None:
            return None
        if now_ms >= self._current.expires_at_ms:
            self._current = None
            return None
        return self._current
