from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


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

    _DEFAULT_APP_SCHEME = "default"

    @staticmethod
    def _pick_unique_name(existing: set[str], base: str) -> str:
        b = (base or "").strip() or "main"
        if b not in existing:
            return b
        i = 2
        while f"{b}-{i}" in existing:
            i += 1
        return f"{b}-{i}"

    def _normalize_profile_cfg(self, cfg: dict[str, Any]) -> dict[str, Any]:
        """
        Ensure per-profile structure exists for RAG App schemes.

        Backward compatible:
        - legacy: profile has a flat `app_api_key`
        - new: profile may have `app_schemes` + `active_app_scheme`
        """
        data: dict[str, Any] = dict(cfg or {})

        schemes_raw = data.get("app_schemes")
        schemes: dict[str, dict[str, Any]] = {}
        if isinstance(schemes_raw, dict):
            for name, scfg in schemes_raw.items():
                n = (str(name) if name is not None else "").strip()
                if not n:
                    continue
                schemes[n] = scfg if isinstance(scfg, dict) else {}

        legacy_app_key = str(data.get("app_api_key") or "").strip()

        # Always ensure a "default" scheme exists and stays empty by default.
        if not schemes:
            schemes[self._DEFAULT_APP_SCHEME] = {}
            if legacy_app_key:
                migrated_name = self._pick_unique_name(set(schemes.keys()), "main")
                schemes[migrated_name] = {"app_api_key": legacy_app_key}
                data["active_app_scheme"] = migrated_name
        elif self._DEFAULT_APP_SCHEME not in schemes:
            schemes[self._DEFAULT_APP_SCHEME] = {}

        # If someone previously stored a key under the "default" scheme (older versions),
        # migrate it to a non-default scheme so "default" stays empty (as a placeholder).
        default_cfg = schemes.get(self._DEFAULT_APP_SCHEME) or {}
        default_key = str((default_cfg.get("app_api_key") if isinstance(default_cfg, dict) else "") or "").strip()
        if default_key:
            # Find an existing non-default scheme with the same key; otherwise create a new one.
            target = None
            for name, scfg in schemes.items():
                if name == self._DEFAULT_APP_SCHEME or not isinstance(scfg, dict):
                    continue
                if str(scfg.get("app_api_key") or "").strip() == default_key:
                    target = name
                    break
            if not target:
                target = self._pick_unique_name(set(schemes.keys()), "main")
                schemes[target] = {"app_api_key": default_key}

            # Clear default scheme key (keep the scheme but remove secret).
            if isinstance(default_cfg, dict) and "app_api_key" in default_cfg:
                default_cfg = dict(default_cfg)
                default_cfg.pop("app_api_key", None)
                schemes[self._DEFAULT_APP_SCHEME] = default_cfg

            # If active scheme was default (or unset), keep previous behavior by activating the migrated scheme.
            raw_active = str(data.get("active_app_scheme") or "").strip()
            if raw_active in {"", self._DEFAULT_APP_SCHEME}:
                data["active_app_scheme"] = target

        active_scheme = str(data.get("active_app_scheme") or "").strip()
        if not active_scheme or active_scheme not in schemes:
            if self._DEFAULT_APP_SCHEME in schemes:
                active_scheme = self._DEFAULT_APP_SCHEME
            else:
                active_scheme = next(iter(schemes.keys()))

        # If we have a legacy mirror key but the active scheme has no key, hydrate it.
        if legacy_app_key and active_scheme in schemes and active_scheme != self._DEFAULT_APP_SCHEME:
            scfg = schemes.get(active_scheme) or {}
            if isinstance(scfg, dict) and not str(scfg.get("app_api_key") or "").strip():
                scfg = dict(scfg)
                scfg["app_api_key"] = legacy_app_key
                schemes[active_scheme] = scfg

        active_key = str((schemes.get(active_scheme) or {}).get("app_api_key") or "").strip()

        data["app_schemes"] = schemes
        data["active_app_scheme"] = active_scheme
        # Keep legacy `app_api_key` in sync for older clients.
        data["app_api_key"] = active_key

        return data

    def _read_state_normalized(self) -> tuple[str, dict[str, dict[str, Any]]]:
        existed = self.path.exists()
        active, profiles = self._read_state()
        changed = False
        normalized: dict[str, dict[str, Any]] = {}
        for name, cfg in (profiles or {}).items():
            ncfg = self._normalize_profile_cfg(cfg if isinstance(cfg, dict) else {})
            normalized[name] = ncfg
            if cfg != ncfg:
                changed = True

        template_profile = self._normalize_profile_cfg({})
        if "default" not in normalized:
            normalized["default"] = template_profile
            changed = True

        # Keep "default" as an empty template profile. If legacy data exists under default,
        # migrate it into a new profile and clear default.
        default_cfg = normalized.get("default") or {}
        if isinstance(default_cfg, dict):
            def _has_data(cfg: dict[str, Any]) -> bool:
                for k in (
                    "base_url",
                    "dataset_id",
                    "note_dataset_id",
                    "transcript_dataset_id",
                    "service_api_key",
                    "indexing_technique",
                    "app_user",
                ):
                    if str(cfg.get(k) or "").strip():
                        return True
                if cfg.get("timeout_seconds") not in (None, ""):
                    return True

                schemes = cfg.get("app_schemes")
                if isinstance(schemes, dict):
                    for name, scfg in schemes.items():
                        if name != self._DEFAULT_APP_SCHEME:
                            return True
                        if isinstance(scfg, dict) and str(scfg.get("app_api_key") or "").strip():
                            return True

                active_scheme = str(cfg.get("active_app_scheme") or "").strip()
                if active_scheme and active_scheme != self._DEFAULT_APP_SCHEME:
                    return True

                allowed = {
                    "app_schemes",
                    "active_app_scheme",
                    "app_api_key",
                    "base_url",
                    "dataset_id",
                    "note_dataset_id",
                    "transcript_dataset_id",
                    "service_api_key",
                    "indexing_technique",
                    "app_user",
                    "timeout_seconds",
                }
                for k in cfg.keys():
                    if k not in allowed:
                        return True
                return False

            if _has_data(default_cfg):
                # Suggest a readable profile name.
                base = "main"
                base_url = str(default_cfg.get("base_url") or "").strip()
                if base_url:
                    try:
                        parts = urlparse(base_url)
                        host = parts.hostname or parts.netloc
                        if host:
                            base = host.replace(":", "-")
                            if parts.port:
                                base = f"{base}-{parts.port}"
                    except Exception:
                        base = "main"

                dataset_id = str(default_cfg.get("dataset_id") or "").strip()
                if dataset_id:
                    base = f"{base}-{dataset_id[:8]}"

                new_name = self._pick_unique_name(set(normalized.keys()), base)
                normalized[new_name] = default_cfg
                normalized["default"] = template_profile
                if active == "default":
                    active = new_name
                changed = True

        if changed and existed:
            try:
                self._write_state(active_profile=active, profiles=normalized)
            except Exception:
                pass
            return active, normalized

        return active, normalized if changed else profiles

    def _read_raw(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _write_raw(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def _read_state(self) -> tuple[str, dict[str, dict[str, Any]]]:
        """
        Returns (active_profile, profiles).

        Storage format (v2):
        {
          "version": 2,
          "active_profile": "default",
          "profiles": { "default": { ... }, "server": { ... } }
        }

        Backward compatible with legacy flat dict stored in `dify.json`.
        """
        raw = self._read_raw()
        if isinstance(raw, dict) and isinstance(raw.get("profiles"), dict):
            profiles_raw = raw.get("profiles") or {}
            profiles: dict[str, dict[str, Any]] = {}
            for name, cfg in profiles_raw.items():
                if not isinstance(name, str) or not name.strip():
                    continue
                profiles[name.strip()] = cfg if isinstance(cfg, dict) else {}

            active = str(raw.get("active_profile") or "").strip() or "default"
            if profiles:
                if active not in profiles:
                    active = next(iter(profiles.keys()))
            else:
                profiles = {"default": {}}
                active = "default"

            return active, profiles

        # Legacy format: a single config dict.
        legacy_cfg = raw if isinstance(raw, dict) else {}
        return "default", {"default": dict(legacy_cfg)}

    def _write_state(self, *, active_profile: str, profiles: dict[str, dict[str, Any]]) -> None:
        active = (active_profile or "").strip() or "default"
        safe_profiles: dict[str, dict[str, Any]] = {}
        for name, cfg in (profiles or {}).items():
            n = (name or "").strip()
            if not n:
                continue
            safe_profiles[n] = cfg if isinstance(cfg, dict) else {}

        if not safe_profiles:
            safe_profiles = {"default": {}}
            active = "default"
        elif active not in safe_profiles:
            active = next(iter(safe_profiles.keys()))

        self._write_raw(
            {
                "version": 2,
                "active_profile": active,
                "profiles": safe_profiles,
            }
        )

    def get_active_profile(self) -> str:
        active, _ = self._read_state()
        return active

    def get(self) -> dict[str, Any]:
        active, profiles = self._read_state_normalized()
        return profiles.get(active, {})

    def get_active_app_scheme(self) -> str:
        active, profiles = self._read_state_normalized()
        cfg = profiles.get(active) or {}
        scheme = str(cfg.get("active_app_scheme") or "").strip()
        return scheme or self._DEFAULT_APP_SCHEME

    def get_app_schemes_safe(self) -> dict[str, Any]:
        active, profiles = self._read_state_normalized()
        cfg = profiles.get(active) or {}
        schemes = cfg.get("app_schemes") if isinstance(cfg, dict) else None
        active_scheme = str(cfg.get("active_app_scheme") or "").strip() or self._DEFAULT_APP_SCHEME

        items: list[dict[str, Any]] = []
        if isinstance(schemes, dict):
            for name, scfg in sorted(schemes.items(), key=lambda kv: kv[0].lower()):
                key = ""
                if isinstance(scfg, dict):
                    key = str(scfg.get("app_api_key") or "")
                items.append(
                    {
                        "name": name,
                        "app_api_key_set": bool(key.strip()),
                        "app_api_key_masked": _mask_secret(key),
                    }
                )

        return {
            "active_profile": active,
            "active_app_scheme": active_scheme,
            "schemes": items,
            "config_path": str(self.path),
        }

    def set_active_app_scheme(self, name: str) -> None:
        target = (name or "").strip()
        if not target:
            raise ValueError("Scheme name cannot be empty")

        active, profiles = self._read_state_normalized()
        cfg = dict(profiles.get(active) or {})
        cfg = self._normalize_profile_cfg(cfg)
        schemes = cfg.get("app_schemes") if isinstance(cfg, dict) else None
        if not isinstance(schemes, dict) or target not in schemes:
            raise KeyError(f"Scheme not found: {target}")

        cfg["active_app_scheme"] = target
        cfg = self._normalize_profile_cfg(cfg)
        profiles[active] = cfg
        self._write_state(active_profile=active, profiles=profiles)

    def upsert_app_scheme(self, name: str, patch: dict[str, Any] | None = None, *, activate: bool = False) -> dict[str, Any]:
        scheme_name = (name or "").strip()
        if not scheme_name:
            raise ValueError("Scheme name cannot be empty")

        active, profiles = self._read_state_normalized()
        cfg = dict(profiles.get(active) or {})
        cfg = self._normalize_profile_cfg(cfg)
        schemes = dict(cfg.get("app_schemes") or {})

        scheme_cfg = dict(schemes.get(scheme_name) or {})
        for k, v in (patch or {}).items():
            if v is None:
                continue
            scheme_cfg[k] = v
        schemes[scheme_name] = scheme_cfg

        cfg["app_schemes"] = schemes
        if activate:
            cfg["active_app_scheme"] = scheme_name

        cfg = self._normalize_profile_cfg(cfg)
        profiles[active] = cfg
        self._write_state(active_profile=active, profiles=profiles)
        return scheme_cfg

    def delete_app_scheme(self, name: str) -> None:
        target = (name or "").strip()
        if not target:
            raise ValueError("Scheme name cannot be empty")

        active, profiles = self._read_state_normalized()
        cfg = dict(profiles.get(active) or {})
        cfg = self._normalize_profile_cfg(cfg)
        schemes = dict(cfg.get("app_schemes") or {})
        if target not in schemes:
            return
        if len(schemes) <= 1:
            raise ValueError("Cannot delete the last scheme")

        del schemes[target]
        cfg["app_schemes"] = schemes
        if str(cfg.get("active_app_scheme") or "") == target:
            cfg["active_app_scheme"] = next(iter(schemes.keys()))

        cfg = self._normalize_profile_cfg(cfg)
        profiles[active] = cfg
        self._write_state(active_profile=active, profiles=profiles)

    def update(self, patch: dict[str, Any]) -> dict[str, Any]:
        active, profiles = self._read_state_normalized()
        cfg = dict(profiles.get(active) or {})
        cfg = self._normalize_profile_cfg(cfg)

        # Special: app_api_key belongs to the active app scheme.
        schemes = dict(cfg.get("app_schemes") or {})
        active_scheme = str(cfg.get("active_app_scheme") or "").strip() or self._DEFAULT_APP_SCHEME
        active_scheme_cfg = dict(schemes.get(active_scheme) or {})

        for k, v in (patch or {}).items():
            if v is None:
                continue
            if k == "app_api_key":
                active_scheme_cfg["app_api_key"] = v
                continue
            cfg[k] = v

        schemes[active_scheme] = active_scheme_cfg
        cfg["app_schemes"] = schemes
        cfg = self._normalize_profile_cfg(cfg)
        profiles[active] = cfg
        self._write_state(active_profile=active, profiles=profiles)
        return cfg

    def list_profiles(self) -> dict[str, dict[str, Any]]:
        _, profiles = self._read_state_normalized()
        return profiles

    def set_active_profile(self, name: str) -> None:
        target = (name or "").strip()
        if not target:
            raise ValueError("Profile name cannot be empty")
        active, profiles = self._read_state()
        if target not in profiles:
            raise KeyError(f"Profile not found: {target}")
        if target == active:
            return
        self._write_state(active_profile=target, profiles=profiles)

    def upsert_profile(
        self,
        name: str,
        patch: dict[str, Any] | None = None,
        *,
        clone_from: str | None = None,
        activate: bool = False,
    ) -> dict[str, Any]:
        profile_name = (name or "").strip()
        if not profile_name:
            raise ValueError("Profile name cannot be empty")

        active, profiles = self._read_state_normalized()
        base: dict[str, Any] = {}
        if clone_from is not None:
            source = (clone_from or "").strip()
            if not source:
                raise ValueError("clone_from cannot be empty")
            if source not in profiles:
                raise KeyError(f"Profile not found: {source}")
            base = dict(profiles[source] or {})
        else:
            base = dict(profiles.get(profile_name) or {})

        base = self._normalize_profile_cfg(base)

        for k, v in (patch or {}).items():
            if v is None:
                continue
            if k == "app_api_key":
                schemes = dict(base.get("app_schemes") or {})
                active_scheme = str(base.get("active_app_scheme") or "").strip() or self._DEFAULT_APP_SCHEME
                scheme_cfg = dict(schemes.get(active_scheme) or {})
                scheme_cfg["app_api_key"] = v
                schemes[active_scheme] = scheme_cfg
                base["app_schemes"] = schemes
                continue
            base[k] = v

        base = self._normalize_profile_cfg(base)
        profiles[profile_name] = base
        new_active = profile_name if activate else active
        self._write_state(active_profile=new_active, profiles=profiles)
        return base

    def delete_profile(self, name: str) -> None:
        target = (name or "").strip()
        if not target:
            raise ValueError("Profile name cannot be empty")
        if target == "default":
            raise ValueError("Cannot delete the default template profile")
        active, profiles = self._read_state()
        if target not in profiles:
            return
        if len(profiles) <= 1:
            raise ValueError("Cannot delete the last profile")
        del profiles[target]
        new_active = active
        if active == target:
            new_active = next(iter(profiles.keys()))
        self._write_state(active_profile=new_active, profiles=profiles)

    def clear(self) -> None:
        self._write_state(active_profile="default", profiles={"default": {}})

    def get_safe(self) -> dict[str, Any]:
        active, profiles = self._read_state_normalized()
        data = profiles.get(active) or {}
        service_key = str(data.get("service_api_key") or "")
        app_key = str(data.get("app_api_key") or "")
        active_app_scheme = str(data.get("active_app_scheme") or "").strip() or self._DEFAULT_APP_SCHEME
        return {
            "active_profile": active,
            "active_app_scheme": active_app_scheme,
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

    def get_profiles_safe(self) -> dict[str, Any]:
        active, profiles = self._read_state_normalized()
        safe_profiles: list[dict[str, Any]] = []
        for name, cfg in sorted(profiles.items(), key=lambda kv: kv[0].lower()):
            service_key = str((cfg or {}).get("service_api_key") or "")
            app_key = str((cfg or {}).get("app_api_key") or "")
            active_app_scheme = str((cfg or {}).get("active_app_scheme") or "").strip() or self._DEFAULT_APP_SCHEME
            safe_profiles.append(
                {
                    "name": name,
                    "base_url": (cfg or {}).get("base_url") or "",
                    "dataset_id": (cfg or {}).get("dataset_id") or "",
                    "note_dataset_id": (cfg or {}).get("note_dataset_id") or "",
                    "transcript_dataset_id": (cfg or {}).get("transcript_dataset_id") or "",
                    "indexing_technique": (cfg or {}).get("indexing_technique") or "",
                    "app_user": (cfg or {}).get("app_user") or "",
                    "timeout_seconds": (cfg or {}).get("timeout_seconds"),
                    "active_app_scheme": active_app_scheme,
                    "service_api_key_set": bool(service_key.strip()),
                    "app_api_key_set": bool(app_key.strip()),
                    "service_api_key_masked": _mask_secret(service_key),
                    "app_api_key_masked": _mask_secret(app_key),
                }
            )

        return {
            "active_profile": active,
            "profiles": safe_profiles,
            "config_path": str(self.path),
        }
