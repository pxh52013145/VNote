from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any


def _default_config_dir() -> Path:
    custom = (os.getenv("RAGVIDEO_CONFIG_DIR") or "").strip()
    if custom:
        return Path(custom)

    if os.name == "nt":
        base = os.getenv("APPDATA")
        if base:
            return Path(base) / "RAGVideo"
        return Path.home() / "AppData" / "Roaming" / "RAGVideo"

    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "RAGVideo"

    return Path(os.getenv("XDG_CONFIG_HOME") or (Path.home() / ".config")) / "RAGVideo"


def _mask_secret(value: str) -> str:
    v = (value or "").strip()
    if not v:
        return ""
    if len(v) <= 8:
        return "*" * len(v)
    return f"{v[:4]}{'*' * (len(v) - 8)}{v[-4:]}"


class DifyConfigManager:
    """
    Persist Dify settings locally so the desktop EXE can be configured via UI
    without editing `.env` in the packaged resources.
    """

    def __init__(self, filepath: str | Path | None = None):
        if filepath is None:
            filepath = _default_config_dir() / "dify.json"
        self.path = Path(filepath)
        self.path.parent.mkdir(parents=True, exist_ok=True)

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

    def get(self) -> dict[str, Any]:
        return self._read()

    def update(self, patch: dict[str, Any]) -> dict[str, Any]:
        data = self._read()
        for k, v in (patch or {}).items():
            if v is None:
                continue
            data[k] = v
        self._write(data)
        return data

    def clear(self) -> None:
        self._write({})

    def get_safe(self) -> dict[str, Any]:
        data = self._read()
        service_key = str(data.get("service_api_key") or "")
        app_key = str(data.get("app_api_key") or "")
        return {
            "base_url": data.get("base_url") or "",
            "dataset_id": data.get("dataset_id") or "",
            "note_dataset_id": data.get("note_dataset_id") or "",
            "transcript_dataset_id": data.get("transcript_dataset_id") or "",
            "indexing_technique": data.get("indexing_technique") or "",
            "app_user": data.get("app_user") or "",
            "timeout_seconds": data.get("timeout_seconds"),
            "service_api_key_set": bool(service_key.strip()),
            "app_api_key_set": bool(app_key.strip()),
            "service_api_key_masked": _mask_secret(service_key),
            "app_api_key_masked": _mask_secret(app_key),
            "config_path": str(self.path),
        }
