"""Music player control tool — lets the AI agent control playback."""

import json
from pathlib import Path
from .base import Tool


class MusicTool(Tool):
    name = "music_player"
    description = (
        "Control the built-in music player. Actions: play (optionally by song name/artist), "
        "pause, resume, next, prev, stop, status, search, volume. "
        "Use this when the user asks to play music, skip songs, pause, etc."
    )
    parameters = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["play", "pause", "resume", "next", "prev", "stop", "status", "search", "volume"],
                "description": "The player action to perform",
            },
            "query": {
                "type": "string",
                "description": "Song name, artist, or album to search/play (for play and search actions)",
            },
            "volume": {
                "type": "number",
                "description": "Volume level 0-100 (for volume action)",
            },
        },
        "required": ["action"],
    }

    _broadcast_fn = None

    def execute(self, action: str, query: str = "", volume: float = -1, **kwargs) -> str:
        cache_path = Path("data/music_cache.json")

        if action == "status":
            return self._status_text()

        if action == "search":
            if not query:
                return "Please provide a search query (song name, artist, or album)."
            results = self._search_library(query, cache_path)
            if not results:
                return f"No songs found matching '{query}'."
            lines = [f"Found {len(results)} result(s):"]
            for i, s in enumerate(results[:10], 1):
                fmt = f" [{s['format']}"
                if s.get("sample_rate"):
                    fmt += f" {s['sample_rate']}Hz"
                if s.get("bit_depth"):
                    fmt += f" {s['bit_depth']}bit"
                fmt += "]"
                lines.append(f"  {i}. {s['title']} — {s['artist']} ({s['album']}){fmt}")
            return "\n".join(lines)

        if action == "play" and query:
            results = self._search_library(query, cache_path)
            if not results:
                return f"No songs found matching '{query}'."
            song = results[0]
            self._send_command("play", {"song": song})
            fmt_info = f"{song['format']}"
            if song.get("sample_rate"):
                fmt_info += f" {song['sample_rate']}Hz"
            if song.get("bit_depth"):
                fmt_info += f" {song['bit_depth']}bit"
            if song.get("lossless"):
                fmt_info += " Lossless"
            return f"Now playing: {song['title']} — {song['artist']} [{fmt_info}]"

        if action == "play":
            self._send_command("resume", {})
            return "Resuming playback."

        if action in ("pause", "resume", "next", "prev", "stop"):
            self._send_command(action, {})
            labels = {"pause": "Paused", "resume": "Resumed", "next": "Skipped to next", "prev": "Back to previous", "stop": "Stopped"}
            return f"{labels.get(action, action.title())} playback."

        if action == "volume":
            if volume < 0:
                return "Please specify a volume level (0-100)."
            vol = max(0, min(100, int(volume)))
            self._send_command("volume", {"volume": vol})
            return f"Volume set to {vol}%."

        return f"Unknown action: {action}"

    def _search_library(self, query: str, cache_path: Path) -> list[dict]:
        if not cache_path.exists():
            return []
        try:
            songs = json.loads(cache_path.read_text(encoding="utf-8"))
        except Exception:
            return []
        q = query.lower()
        results = []
        for s in songs:
            score = 0
            if q in s["title"].lower():
                score += 3
            if q in s["artist"].lower():
                score += 2
            if q in s["album"].lower():
                score += 1
            if score > 0:
                results.append((score, s))
        results.sort(key=lambda x: -x[0])
        return [r[1] for r in results]

    def _send_command(self, action: str, payload: dict):
        """Queue a WebSocket broadcast command for the frontend player."""
        import httpx
        try:
            httpx.post(
                "http://127.0.0.1:6185/api/music/control",
                json={"action": action, "payload": payload},
                timeout=3,
            )
        except Exception:
            pass

    def _status_text(self) -> str:
        return "Music player is available. Use 'search' to find songs, 'play' to start playback."
