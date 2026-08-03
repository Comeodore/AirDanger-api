import hashlib
import re
import time

_URL_RE = re.compile(r"https?://\S+|t\.me/\S+", re.IGNORECASE)
_NON_WORD_RE = re.compile(r"[^\w]+", re.UNICODE)


def normalize(text: str) -> str:
    text = _URL_RE.sub(" ", text)
    return _NON_WORD_RE.sub(" ", text).casefold().strip()

def digest(text: str) -> str:
    return hashlib.sha256(normalize(text).encode()).hexdigest()

class TTLSet:
    def __init__(self, ttl_seconds: float) -> None:
        self._ttl = ttl_seconds
        self._seen: dict[str, float] = {}

    def add(self, key: str, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        self._sweep(now)
        if key in self._seen:
            return False
        self._seen[key] = now + self._ttl
        return True

    def _sweep(self, now: float) -> None:
        expired = [k for k, deadline in self._seen.items() if deadline <= now]
        for k in expired:
            del self._seen[k]
