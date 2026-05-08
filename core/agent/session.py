"""Session persistence - save and resume conversations.

Stores conversation history and model config as JSON, keyed by session ID
(typically derived from the chat platform's user/group identity).
"""

import json
import re
import time
from pathlib import Path

from core.utils import atomic_write_text

DEFAULT_SESSIONS_DIR = Path("data/sessions")


def _safe_filename(session_id: str) -> str:
    """Replace characters illegal in Windows filenames, and block path traversal.

    Rejects session IDs containing '..', null bytes, or newlines to prevent
    directory traversal attacks.
    """
    if ".." in session_id or "\x00" in session_id or "\n" in session_id or "\r" in session_id:
        raise ValueError(f"Invalid session ID: {session_id!r}")
    return re.sub(r'[<>:"/\\|?*]', "_", session_id)


class SessionStore:
    def __init__(self, sessions_dir: str | Path | None = None):
        self.sessions_dir = Path(sessions_dir) if sessions_dir else DEFAULT_SESSIONS_DIR
        self.sessions_dir.mkdir(parents=True, exist_ok=True)

    def save(
        self,
        messages: list[dict],
        model: str,
        session_id: str,
        *,
        extra: dict | None = None,
    ) -> str:
        data = {
            "id": session_id,
            "model": model,
            "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "message_count": len(messages),
            "messages": messages,
        }
        if extra:
            data["extra"] = extra

        path = self.sessions_dir / f"{_safe_filename(session_id)}.json"
        payload = json.dumps(data, ensure_ascii=False, indent=2)
        atomic_write_text(path, payload, prefix=".session_")
        return session_id

    def load(self, session_id: str) -> tuple[list[dict], str] | None:
        path = self.sessions_dir / f"{_safe_filename(session_id)}.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data["messages"], data["model"]
        except (json.JSONDecodeError, KeyError, UnicodeDecodeError):
            return None

    def delete(self, session_id: str) -> bool:
        path = self.sessions_dir / f"{_safe_filename(session_id)}.json"
        if path.exists():
            path.unlink()
            return True
        return False

    def list_sessions(self, limit: int = 50) -> list[dict]:
        if not self.sessions_dir.exists():
            return []
        sessions = []
        for f in sorted(self.sessions_dir.glob("*.json"), reverse=True):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                preview = ""
                for m in data.get("messages", []):
                    if m.get("role") == "user" and m.get("content"):
                        preview = m["content"][:80]
                        break
                sessions.append(
                    {
                        "id": data.get("id", f.stem),
                        "model": data.get("model", "?"),
                        "saved_at": data.get("saved_at", "?"),
                        "message_count": data.get("message_count", 0),
                        "preview": preview,
                    }
                )
            except (json.JSONDecodeError, KeyError):
                continue
        return sessions[:limit]


# Module-level convenience functions using a default store
_default_store = SessionStore()


def save_session(messages: list[dict], model: str, session_id: str, **kw) -> str:
    return _default_store.save(messages, model, session_id, **kw)


def load_session(session_id: str):
    return _default_store.load(session_id)


def list_sessions(limit: int = 50):
    return _default_store.list_sessions(limit)
