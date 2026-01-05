from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from filelock import FileLock

from app.services.dify_config_manager import _default_config_dir

DEFAULT_CONVERSATION_TITLE = "新对话"
MAX_CONVERSATIONS = 30
MAX_MESSAGES_PER_CONVERSATION = 60


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _normalize_title(text: str) -> str:
    t = re.sub(r"\s+", " ", str(text or "")).strip()
    if not t:
        return DEFAULT_CONVERSATION_TITLE
    return t[:32] + "…" if len(t) > 32 else t


def _safe_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


class RagHistoryManager:
    """
    Persist RAG chat history locally (file-based) so the desktop EXE can keep
    conversation history across restarts without relying on browser localStorage.
    """

    def __init__(self, filepath: str | Path | None = None):
        if filepath is None:
            filepath = _default_config_dir() / "rag_history.json"
        self.path = Path(filepath)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = FileLock(str(self.path) + ".lock")

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _write(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def _ensure_user_id(self, state: dict[str, Any]) -> str:
        user_id = str(state.get("user_id") or "").strip()
        if user_id:
            return user_id
        user_id = f"rag-{uuid4()}"
        state["user_id"] = user_id
        return user_id

    def _normalize_state(self, raw: dict[str, Any]) -> dict[str, Any]:
        state: dict[str, Any] = dict(raw or {})
        self._ensure_user_id(state)

        conversations = _safe_list(state.get("conversations"))
        normalized_convs: list[dict[str, Any]] = []
        for conv in conversations:
            if not isinstance(conv, dict):
                continue
            conv_id = str(conv.get("id") or "").strip()
            if not conv_id:
                continue
            created_at = str(conv.get("createdAt") or conv.get("created_at") or _now_iso())
            updated_at = str(conv.get("updatedAt") or conv.get("updated_at") or created_at)
            normalized_convs.append(
                {
                    "id": conv_id,
                    "title": _normalize_title(conv.get("title") or DEFAULT_CONVERSATION_TITLE),
                    "createdAt": created_at,
                    "updatedAt": updated_at,
                    "difyConversationId": conv.get("difyConversationId") or conv.get("dify_conversation_id"),
                    "messages": _safe_list(conv.get("messages")),
                }
            )

        normalized_convs.sort(key=lambda c: str(c.get("updatedAt") or ""), reverse=True)
        normalized_convs = normalized_convs[:MAX_CONVERSATIONS]

        current_id = str(state.get("current_conversation_id") or state.get("currentConversationId") or "").strip()
        if current_id and not any(c["id"] == current_id for c in normalized_convs):
            current_id = ""

        if not current_id and normalized_convs:
            current_id = normalized_convs[0]["id"]

        return {
            "user_id": state["user_id"],
            "current_conversation_id": current_id or None,
            "conversations": normalized_convs,
            "storage_path": str(self.path),
        }

    def get_state(self) -> dict[str, Any]:
        with self._lock:
            raw = self._read()
            normalized = self._normalize_state(raw)
            # Auto-heal broken/legacy files (do not persist storage_path).
            data_to_write = dict(normalized)
            data_to_write.pop("storage_path", None)
            if raw != data_to_write:
                try:
                    self._write(data_to_write)
                except Exception:
                    pass
            return normalized

    def replace_state(self, patch: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            normalized = self._normalize_state(patch or {})
            data_to_write = dict(normalized)
            data_to_write.pop("storage_path", None)
            self._write(data_to_write)
            return normalized

    def set_current_conversation(self, conversation_id: str | None) -> dict[str, Any]:
        with self._lock:
            state = self._normalize_state(self._read())
            cid = str(conversation_id or "").strip()
            if cid and any(c["id"] == cid for c in state.get("conversations", [])):
                state["current_conversation_id"] = cid
            elif state.get("conversations"):
                state["current_conversation_id"] = state["conversations"][0]["id"]
            else:
                state["current_conversation_id"] = None
            data_to_write = dict(state)
            data_to_write.pop("storage_path", None)
            self._write(data_to_write)
            return state

    def upsert_conversation(self, conversation_id: str, patch: dict[str, Any] | None = None) -> dict[str, Any]:
        with self._lock:
            state = self._normalize_state(self._read())
            cid = str(conversation_id or "").strip()
            if not cid:
                raise ValueError("conversation_id is required")

            patch = patch or {}
            now = _now_iso()
            conversations = list(state.get("conversations") or [])
            existing = next((c for c in conversations if c.get("id") == cid), None)
            if existing is None:
                conv = {
                    "id": cid,
                    "title": _normalize_title(patch.get("title") or DEFAULT_CONVERSATION_TITLE),
                    "createdAt": now,
                    "updatedAt": now,
                    "difyConversationId": patch.get("difyConversationId") or patch.get("dify_conversation_id"),
                    "messages": [],
                }
                conversations.insert(0, conv)
                existing = conv
            else:
                if "title" in patch and patch.get("title") is not None:
                    existing["title"] = _normalize_title(patch.get("title"))
                if "difyConversationId" in patch and patch.get("difyConversationId") is not None:
                    existing["difyConversationId"] = patch.get("difyConversationId")
                if "dify_conversation_id" in patch and patch.get("dify_conversation_id") is not None:
                    existing["difyConversationId"] = patch.get("dify_conversation_id")
                existing["updatedAt"] = now

            conversations.sort(key=lambda c: str(c.get("updatedAt") or ""), reverse=True)
            conversations = conversations[:MAX_CONVERSATIONS]
            state["conversations"] = conversations
            state["current_conversation_id"] = state.get("current_conversation_id") or cid
            if not any(c["id"] == state["current_conversation_id"] for c in conversations):
                state["current_conversation_id"] = conversations[0]["id"] if conversations else None

            data_to_write = dict(state)
            data_to_write.pop("storage_path", None)
            self._write(data_to_write)
            return existing

    def append_message(self, conversation_id: str, msg: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            cid = str(conversation_id or "").strip()
            if not cid:
                raise ValueError("conversation_id is required")

            state = self._normalize_state(self._read())
            now = _now_iso()
            conversations = list(state.get("conversations") or [])
            existing = next((c for c in conversations if c.get("id") == cid), None)
            if existing is None:
                existing = {
                    "id": cid,
                    "title": DEFAULT_CONVERSATION_TITLE,
                    "createdAt": now,
                    "updatedAt": now,
                    "difyConversationId": None,
                    "messages": [],
                }
                conversations.insert(0, existing)

            role = str(msg.get("role") or "").strip()
            content = str(msg.get("content") or "")
            message = {
                "id": str(msg.get("id") or uuid4()),
                "role": role,
                "content": content,
                "createdAt": str(msg.get("createdAt") or now),
            }
            resources = msg.get("resources")
            if resources is not None:
                message["resources"] = resources

            messages = _safe_list(existing.get("messages"))
            messages.append(message)
            existing["messages"] = messages[-MAX_MESSAGES_PER_CONVERSATION:]

            if existing.get("title") == DEFAULT_CONVERSATION_TITLE and role == "user":
                existing["title"] = _normalize_title(content)

            existing["updatedAt"] = now

            conversations.sort(key=lambda c: str(c.get("updatedAt") or ""), reverse=True)
            conversations = conversations[:MAX_CONVERSATIONS]
            state["conversations"] = conversations
            state["current_conversation_id"] = state.get("current_conversation_id") or cid
            if not any(c["id"] == state["current_conversation_id"] for c in conversations):
                state["current_conversation_id"] = conversations[0]["id"] if conversations else None

            data_to_write = dict(state)
            data_to_write.pop("storage_path", None)
            self._write(data_to_write)
            return existing

    def delete_conversation(self, conversation_id: str) -> dict[str, Any]:
        with self._lock:
            state = self._normalize_state(self._read())
            cid = str(conversation_id or "").strip()
            conversations = [c for c in (state.get("conversations") or []) if c.get("id") != cid]
            state["conversations"] = conversations

            if state.get("current_conversation_id") == cid:
                state["current_conversation_id"] = conversations[0]["id"] if conversations else None

            data_to_write = dict(state)
            data_to_write.pop("storage_path", None)
            self._write(data_to_write)
            return state

    def clear(self) -> dict[str, Any]:
        with self._lock:
            state = self._normalize_state(self._read())
            state["conversations"] = []
            state["current_conversation_id"] = None
            data_to_write = dict(state)
            data_to_write.pop("storage_path", None)
            self._write(data_to_write)
            return state
