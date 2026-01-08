from fastapi import APIRouter, HTTPException
from urllib.parse import urlparse
from pydantic import BaseModel, field_validator
from typing import Optional
from app.utils.response import ResponseWrapper as R

from app.services.cookie_manager import CookieConfigManager
from app.services.dify_config_manager import DifyConfigManager
from app.services.dify_client import DifyConfig
from ffmpeg_helper import ensure_ffmpeg_or_raise

router = APIRouter()
cookie_manager = CookieConfigManager()
dify_config_manager = DifyConfigManager()

def _mask_secret(value: str | None) -> str:
    v = (value or "").strip()
    if not v:
        return ""
    if len(v) <= 8:
        return "*" * len(v)
    return f"{v[:4]}{'*' * (len(v) - 8)}{v[-4:]}"


class CookieUpdateRequest(BaseModel):
    platform: str
    cookie: str


class DifyConfigUpdateRequest(BaseModel):
    base_url: Optional[str] = None
    dataset_id: Optional[str] = None
    note_dataset_id: Optional[str] = None
    transcript_dataset_id: Optional[str] = None
    service_api_key: Optional[str] = None
    app_api_key: Optional[str] = None
    app_user: Optional[str] = None
    indexing_technique: Optional[str] = None
    timeout_seconds: Optional[float] = None

    @field_validator(
        "base_url",
        "dataset_id",
        "note_dataset_id",
        "transcript_dataset_id",
        "service_api_key",
        "app_api_key",
        "app_user",
        "indexing_technique",
        mode="before",
    )
    @classmethod
    def _strip_strings(cls, v):
        if v is None:
            return None
        return str(v).strip()


class DifyProfileActivateRequest(BaseModel):
    name: str

    @field_validator("name", mode="before")
    @classmethod
    def _strip_name(cls, v):
        return str(v).strip() if v is not None else ""


class DifyProfileUpsertRequest(BaseModel):
    name: str
    clone_from: Optional[str] = None
    activate: bool = True

    # Same config fields as DifyConfigUpdateRequest (None means "don't touch").
    base_url: Optional[str] = None
    dataset_id: Optional[str] = None
    note_dataset_id: Optional[str] = None
    transcript_dataset_id: Optional[str] = None
    service_api_key: Optional[str] = None
    app_api_key: Optional[str] = None
    app_user: Optional[str] = None
    indexing_technique: Optional[str] = None
    timeout_seconds: Optional[float] = None

    @field_validator(
        "name",
        "clone_from",
        "base_url",
        "dataset_id",
        "note_dataset_id",
        "transcript_dataset_id",
        "service_api_key",
        "app_api_key",
        "app_user",
        "indexing_technique",
        mode="before",
    )
    @classmethod
    def _strip_strings(cls, v):
        if v is None:
            return None
        return str(v).strip()


class DifyAppSchemeActivateRequest(BaseModel):
    name: str

    @field_validator("name", mode="before")
    @classmethod
    def _strip_name(cls, v):
        return str(v).strip() if v is not None else ""


class DifyAppSchemeUpsertRequest(BaseModel):
    name: str
    app_api_key: Optional[str] = None
    activate: bool = True

    @field_validator("name", "app_api_key", mode="before")
    @classmethod
    def _strip_strings(cls, v):
        if v is None:
            return None
        return str(v).strip()


@router.get("/get_downloader_cookie/{platform}")
def get_cookie(platform: str):
    cookie = cookie_manager.get(platform)
    if not cookie:
        return R.success(msg='未找到Cookies')
    return R.success(
        data={"platform": platform, "cookie": cookie}
    )


@router.post("/update_downloader_cookie")
def update_cookie(data: CookieUpdateRequest):
    cookie_manager.set(data.platform, data.cookie)
    return R.success(

    )

@router.get("/sys_health")
async def sys_health():
    try:
        ensure_ffmpeg_or_raise()
        return R.success()
    except EnvironmentError:
        return R.error(msg="系统未安装 ffmpeg 请先进行安装")

@router.get("/sys_check")
async def sys_check():
    return R.success()


@router.get("/dify_config")
def get_dify_config():
    """
    Return effective Dify config (env + persisted overrides).
    Secrets are masked and never returned in full.
    """
    cfg = DifyConfig.from_env()
    persisted_safe = dify_config_manager.get_safe()

    return R.success(
        data={
            "active_profile": persisted_safe.get("active_profile"),
            "active_app_scheme": persisted_safe.get("active_app_scheme"),
            "base_url": cfg.base_url,
            "dataset_id": cfg.dataset_id,
            "note_dataset_id": cfg.note_dataset_id,
            "transcript_dataset_id": cfg.transcript_dataset_id,
            "indexing_technique": cfg.indexing_technique,
            "app_user": cfg.app_user,
            "timeout_seconds": cfg.timeout_seconds,
            "service_api_key_set": bool((cfg.service_api_key or "").strip()),
            "app_api_key_set": bool((cfg.app_api_key or "").strip()),
            "service_api_key_masked": _mask_secret(cfg.service_api_key),
            "app_api_key_masked": _mask_secret(cfg.app_api_key),
            "config_path": persisted_safe.get("config_path"),
        }
    )


@router.get("/dify_app_schemes")
def get_dify_app_schemes():
    """
    List saved RAG App schemes under the active Dify profile.
    Secrets are masked and never returned in full.
    """
    return R.success(data=dify_config_manager.get_app_schemes_safe())


@router.post("/dify_app_schemes/activate")
def activate_dify_app_scheme(data: DifyAppSchemeActivateRequest):
    try:
        dify_config_manager.set_active_app_scheme(data.name)
    except ValueError as exc:
        return R.error(msg=str(exc))
    except KeyError as exc:
        return R.error(msg=str(exc))
    return R.success(data=dify_config_manager.get_app_schemes_safe())


@router.post("/dify_app_schemes")
def upsert_dify_app_scheme(data: DifyAppSchemeUpsertRequest):
    if not data.name:
        return R.error(msg="Scheme name cannot be empty")

    patch = {}
    if data.app_api_key is not None:
        patch["app_api_key"] = data.app_api_key

    try:
        dify_config_manager.upsert_app_scheme(data.name, patch, activate=bool(data.activate))
    except ValueError as exc:
        return R.error(msg=str(exc))
    except KeyError as exc:
        return R.error(msg=str(exc))

    return R.success(data=dify_config_manager.get_app_schemes_safe())


@router.delete("/dify_app_schemes/{name}")
def delete_dify_app_scheme(name: str):
    try:
        dify_config_manager.delete_app_scheme(name)
    except ValueError as exc:
        return R.error(msg=str(exc))
    return R.success(data=dify_config_manager.get_app_schemes_safe())


@router.post("/dify_config")
def update_dify_config(data: DifyConfigUpdateRequest):
    patch = {}
    if data.base_url is not None:
        patch["base_url"] = data.base_url
    if data.dataset_id is not None:
        patch["dataset_id"] = data.dataset_id
    if data.note_dataset_id is not None:
        patch["note_dataset_id"] = data.note_dataset_id
    if data.transcript_dataset_id is not None:
        patch["transcript_dataset_id"] = data.transcript_dataset_id
    if data.service_api_key is not None:
        patch["service_api_key"] = data.service_api_key
    if data.app_api_key is not None:
        patch["app_api_key"] = data.app_api_key
    if data.app_user is not None:
        patch["app_user"] = data.app_user
    if data.indexing_technique is not None:
        patch["indexing_technique"] = data.indexing_technique
    if data.timeout_seconds is not None:
        patch["timeout_seconds"] = data.timeout_seconds

    # Normalize/migrate first (keeps default as template if legacy data existed).
    existing_profiles = dify_config_manager.list_profiles()
    active = dify_config_manager.get_active_profile()

    # Keep "default" as an empty template: saving under default auto-creates a new profile.
    if active == "default":
        base = "profile"
        base_url = str(patch.get("base_url") or "").strip()
        if base_url:
            try:
                parts = urlparse(base_url)
                host = parts.hostname or parts.netloc
                if host:
                    base = host.replace(":", "-")
                    if parts.port:
                        base = f"{base}-{parts.port}"
            except Exception:
                base = "profile"

        dataset_id = str(patch.get("dataset_id") or "").strip()
        if dataset_id:
            base = f"{base}-{dataset_id[:8]}"

        existing = set(existing_profiles.keys())
        name = base.strip() or "profile"
        if name in existing:
            i = 2
            while f"{name}-{i}" in existing:
                i += 1
            name = f"{name}-{i}"

        dify_config_manager.upsert_profile(name, patch, clone_from=None, activate=True)
    else:
        dify_config_manager.update(patch)

    return get_dify_config()


@router.get("/dify_profiles")
def get_dify_profiles():
    """
    List saved Dify config profiles. Secrets are masked.
    """
    return R.success(data=dify_config_manager.get_profiles_safe())


@router.post("/dify_profiles/activate")
def activate_dify_profile(data: DifyProfileActivateRequest):
    try:
        dify_config_manager.set_active_profile(data.name)
    except ValueError as exc:
        return R.error(msg=str(exc))
    except KeyError as exc:
        return R.error(msg=str(exc))
    return R.success(data=dify_config_manager.get_profiles_safe())


@router.post("/dify_profiles")
def upsert_dify_profile(data: DifyProfileUpsertRequest):
    if not data.name:
        return R.error(msg="Profile name cannot be empty")

    patch = {}
    for k in (
        "base_url",
        "dataset_id",
        "note_dataset_id",
        "transcript_dataset_id",
        "service_api_key",
        "app_api_key",
        "app_user",
        "indexing_technique",
        "timeout_seconds",
    ):
        v = getattr(data, k)
        if v is None:
            continue
        patch[k] = v

    clone_from = data.clone_from
    if clone_from is None:
        clone_from = dify_config_manager.get_active_profile()

    try:
        dify_config_manager.upsert_profile(
            data.name,
            patch,
            clone_from=clone_from,
            activate=bool(data.activate),
        )
    except ValueError as exc:
        return R.error(msg=str(exc))
    except KeyError as exc:
        return R.error(msg=str(exc))

    return R.success(data=dify_config_manager.get_profiles_safe())


@router.delete("/dify_profiles/{name}")
def delete_dify_profile(name: str):
    try:
        dify_config_manager.delete_profile(name)
    except ValueError as exc:
        return R.error(msg=str(exc))
    return R.success(data=dify_config_manager.get_profiles_safe())
