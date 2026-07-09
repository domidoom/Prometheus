"""Conversation state management."""

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional


class ConversationManager:
    """Manages conversation state and history."""

    def __init__(
        self,
        history_limit: int = 20,
        data_dir: Path = None,
    ):
        self.history_limit = history_limit
        self.data_dir = data_dir or Path("data")
        self.messages: List[Dict[str, str]] = []
        self.session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.break_until: Optional[datetime] = None
        self.user_preferences: Dict[str, str] = {}
        self._unsaved_count = 0
        self._load_memories()

    def add_message(self, role: str, content: str, **kwargs) -> None:
        """Add a message to conversation history."""
        message = {"role": role, "content": content}
        message.update(kwargs)
        self.messages.append(message)

        if len(self.messages) > self.history_limit:
            self.messages = self.messages[-self.history_limit:]

        self._unsaved_count += 1
        # Save every 3 messages to reduce disk I/O
        if self._unsaved_count >= 3:
            self._save_session()
            self._unsaved_count = 0

    def get_messages(self) -> List[Dict[str, str]]:
        """Get current conversation messages."""
        return self.messages.copy()

    def clear(self) -> None:
        """Clear conversation history."""
        self.messages = []
        self._save_session()

    def is_on_break(self) -> bool:
        """Check if currently on a break."""
        if self.break_until is None:
            return False
        if datetime.now() >= self.break_until:
            self.break_until = None
            return False
        return True

    def set_break(self, minutes: int) -> None:
        """Set a break timer."""
        self.break_until = datetime.now() + timedelta(minutes=minutes)

    def clear_break(self) -> None:
        """Clear break timer."""
        self.break_until = None

    def get_break_remaining(self) -> Optional[int]:
        """Get remaining break time in minutes."""
        if not self.is_on_break():
            return None
        delta = self.break_until - datetime.now()
        return int(delta.total_seconds() / 60)

    def set_preference(self, key: str, value: str) -> None:
        """Store a user preference."""
        self.user_preferences[key] = value
        self._save_memories()

    def get_preference(self, key: str, default: str = "") -> str:
        """Get a user preference."""
        return self.user_preferences.get(key, default)

    def _save_session(self) -> None:
        """Save current session to file."""
        session_file = self.data_dir / "conversation_history" / f"{self.session_id}.json"
        session_file.parent.mkdir(parents=True, exist_ok=True)
        session_file.write_text(json.dumps(self.messages, indent=2))

    def _save_memories(self) -> None:
        """Save user memories/preferences."""
        memories_file = self.data_dir / "memories" / "preferences.json"
        memories_file.parent.mkdir(parents=True, exist_ok=True)
        memories_file.write_text(json.dumps(self.user_preferences, indent=2))

    def _load_memories(self) -> None:
        """Load user memories/preferences."""
        memories_file = self.data_dir / "memories" / "preferences.json"
        if memories_file.exists():
            try:
                self.user_preferences = json.loads(memories_file.read_text())
            except json.JSONDecodeError:
                pass

    def import_claude_md(self, md_path: Path) -> int:
        """Import memories from Claude MD files."""
        imported = 0
        md_dir = self.data_dir / "md_imports"
        md_dir.mkdir(parents=True, exist_ok=True)

        if md_path.is_file():
            files = [md_path]
        else:
            files = list(md_path.rglob("*.md"))

        for file in files:
            try:
                content = file.read_text()
                key = f"memory_{file.stem}"
                self.user_preferences[key] = content[:1000]
                imported += 1
            except Exception:
                continue

        if imported > 0:
            self._save_memories()

        return imported

    def get_system_context(self) -> str:
        """Get system context string for LLM."""
        context = []
        context.append(f"Current time: {datetime.now().strftime('%Y-%m-%d %H:%M')}")

        if self.user_preferences:
            prefs = [f"{k}: {v[:100]}..." for k, v in list(self.user_preferences.items())[:5]]
            context.append(f"User preferences: {', '.join(prefs)}")

        if self.is_on_break():
            remaining = self.get_break_remaining()
            context.append(f"On break for {remaining} more minutes")

        return "\n".join(context)
