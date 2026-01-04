from fastapi import APIRouter, HTTPException
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

    dify_config_manager.update(patch)
    return get_dify_config()
