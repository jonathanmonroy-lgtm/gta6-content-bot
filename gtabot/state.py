"""Small JSON state file: what has been used already and what was published."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

SEEN_RETENTION_DAYS = 30
HISTORY_LIMIT = 200


def now() -> datetime:
    return datetime.now(timezone.utc)


class State:
    def __init__(self, path: Path):
        self.path = path
        self.data = {"seen": {}, "history": []}
        if path.exists():
            self.data.update(json.loads(path.read_text(encoding="utf-8")))

    # Items -------------------------------------------------------------
    def is_seen(self, item_id: str) -> bool:
        return item_id in self.data["seen"]

    def mark_seen(self, item_id: str) -> None:
        self.data["seen"][item_id] = now().isoformat()

    # History -----------------------------------------------------------
    def record(self, entry: dict) -> None:
        entry = {"at": now().isoformat(), **entry}
        self.data["history"].append(entry)
        self.data["history"] = self.data["history"][-HISTORY_LIMIT:]

    def count_since(self, kind: str, hours: int = 24) -> int:
        cutoff = now() - timedelta(hours=hours)
        return sum(
            1
            for h in self.data["history"]
            if h.get(kind) and datetime.fromisoformat(h["at"]) >= cutoff
        )

    def recent_topics(self, limit: int = 15) -> list[str]:
        return [h["topic"] for h in self.data["history"][-limit:] if h.get("topic")]

    # Persistence -------------------------------------------------------
    def save(self) -> None:
        cutoff = now() - timedelta(days=SEEN_RETENTION_DAYS)
        self.data["seen"] = {
            k: v for k, v in self.data["seen"].items() if datetime.fromisoformat(v) >= cutoff
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
