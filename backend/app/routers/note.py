# app/routers/note.py
import json
import os
import re
import shutil
import socket
import stat
import time
import uuid
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

from fastapi import APIRouter, BackgroundTasks, File, HTTPException, Request, UploadFile
from pydantic import BaseModel, validator, field_validator
from dataclasses import asdict
from ipaddress import ip_address

from app.db.video_task_dao import delete_task_by_task_id, get_task_by_video
from app.enmus.exception import NoteErrorEnum
from app.enmus.note_enums import DownloadQuality
from app.enmus.task_status_enums import TaskStatus
from app.exceptions.note import NoteError
from app.services.dify_client import DifyConfig, DifyError, DifyKnowledgeClient
from app.services.dify_config_manager import DifyConfigManager
from app.services.library_sync import build_bundle_zip, compute_sync_id, ensure_local_sync_meta, make_source_key
from app.services.minio_storage import MinioConfig, MinioConfigError, MinioStorage, bucket_name_for_profile
from app.services.note import NoteGenerator, logger
from app.services.task_manager import task_manager
from app.services.rag_service import (
    build_rag_document_name,
    build_rag_document_text,
    build_rag_note_document_text,
)
from app.models.audio_model import AudioDownloadResult
from app.models.transcriber_model import TranscriptResult, TranscriptSegment
from app.utils.response import ResponseWrapper as R
from app.utils.url_parser import extract_video_id
from app.validators.video_url_validator import is_supported_video_url
from fastapi.responses import Response
import httpx
from app.utils.paths import note_output_dir, uploads_dir as get_uploads_dir

# from app.services.downloader import download_raw_audio
# from app.services.whisperer import transcribe_audio

router = APIRouter()

_TRUE_VALUES = {"1", "true", "yes", "y", "on"}
_FALSE_VALUES = {"0", "false", "no", "n", "off"}


def _env_bool_or_auto(name: str, default: bool | None = None) -> bool | None:
    raw = os.getenv(name)
    if raw is None:
        return default
    raw_l = str(raw).strip().lower()
    if raw_l == "auto":
        return None
    if raw_l in _TRUE_VALUES:
        return True
    if raw_l in _FALSE_VALUES:
        return False
    return default


class RecordRequest(BaseModel):
    video_id: str
    platform: str
    task_id: Optional[str] = None


class VideoRequest(BaseModel):
    video_url: str
    platform: str
    quality: DownloadQuality
    screenshot: Optional[bool] = False
    link: Optional[bool] = False
    model_name: str
    provider_id: str
    task_id: Optional[str] = None
    format: Optional[list] = []
    style: str = None
    extras: Optional[str]=None
    video_understanding: Optional[bool] = False
    video_interval: Optional[int] = 0
    grid_size: Optional[list] = []

    @field_validator("video_url")
    def validate_supported_url(cls, v):
        url = str(v).strip()
        # Allow users to paste "【title】 https://..." and similar formats.
        m = re.search(r"https?://\\S+", url, flags=re.IGNORECASE)
        if m:
            url = m.group(0)
            url = re.sub(r"[\\]\\)）】》>，,。\\.！!？\\?\"']+$", "", url)
        parsed = urlparse(url)
        if parsed.scheme in ("http", "https"):
            # 是网络链接，继续用原有平台校验
            if not is_supported_video_url(url):
                raise NoteError(code=NoteErrorEnum.PLATFORM_NOT_SUPPORTED.code,
                                message=NoteErrorEnum.PLATFORM_NOT_SUPPORTED.message)

        return url


class ReingestRequest(BaseModel):
    task_id: str
    video_url: Optional[str] = None
    platform: Optional[str] = None
    include_transcript: bool = True
    include_note: bool = True


NOTE_OUTPUT_DIR = note_output_dir()
UPLOAD_DIR = get_uploads_dir()
NOTE_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


def _task_dir(task_id: str) -> Path:
    return NOTE_OUTPUT_DIR / str(task_id).strip()


def _task_result_path(task_id: str) -> Path:
    task_dir = _task_dir(task_id)
    return task_dir / f"{task_id}.json"


def _task_status_path(task_id: str) -> Path:
    task_dir = _task_dir(task_id)
    return task_dir / f"{task_id}.status.json"


def _legacy_result_path(task_id: str) -> Path:
    return NOTE_OUTPUT_DIR / f"{task_id}.json"


def _legacy_status_path(task_id: str) -> Path:
    return NOTE_OUTPUT_DIR / f"{task_id}.status.json"


def _pick_existing_path(*candidates: Path) -> Path | None:
    for p in candidates:
        try:
            if p.exists():
                return p
        except Exception:
            continue
    return None


def _atomic_merge_json_file(path: Path, patch: dict[str, Any]) -> None:
    existing: dict[str, Any] = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            existing = {}

    existing.update(patch)

    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _delete_task_files(task_id: str) -> None:
    tid = (task_id or "").strip()
    if not tid:
        return

    # New format: NOTE_OUTPUT_DIR/<task_id>/...
    task_dir = _task_dir(tid)
    if task_dir.exists() and task_dir.is_dir():
        def _on_rm_error(func, path, exc_info):  # noqa: ANN001
            try:
                os.chmod(path, stat.S_IWRITE)
                func(path)
            except Exception:
                pass

        try:
            shutil.rmtree(task_dir, onerror=_on_rm_error)
        except Exception:
            # Fall back to manual cleanup (best-effort).
            try:
                for p in sorted(task_dir.rglob("*"), key=lambda x: len(str(x)), reverse=True):
                    try:
                        if p.is_file() or p.is_symlink():
                            os.chmod(p, stat.S_IWRITE)
                            p.unlink(missing_ok=True)
                        elif p.is_dir():
                            p.rmdir()
                    except Exception:
                        pass
                try:
                    task_dir.rmdir()
                except Exception:
                    pass
            except Exception:
                pass

        if task_dir.exists():
            raise RuntimeError(f"Failed to delete local task dir: {task_dir}")

    # Legacy flat files: NOTE_OUTPUT_DIR/<task_id>*
    legacy_candidates = [
        NOTE_OUTPUT_DIR / f"{tid}.json",
        NOTE_OUTPUT_DIR / f"{tid}.status.json",
        NOTE_OUTPUT_DIR / f"{tid}.sync.json",
        NOTE_OUTPUT_DIR / f"{tid}_audio.json",
        NOTE_OUTPUT_DIR / f"{tid}_transcript.json",
        NOTE_OUTPUT_DIR / f"{tid}_markdown.md",
        NOTE_OUTPUT_DIR / f"{tid}_markdown.status.json",
    ]
    leftover: list[str] = []
    for p in legacy_candidates:
        try:
            if p.exists() and p.is_file():
                try:
                    os.chmod(p, stat.S_IWRITE)
                except Exception:
                    pass
                p.unlink()
        except Exception:
            try:
                if p.exists():
                    leftover.append(str(p))
            except Exception:
                pass

    if leftover:
        raise RuntimeError(f"Failed to delete local task files: {', '.join(leftover[:3])}")


def _extract_dify_indexing_error(payload: Any) -> Optional[str]:
    if not isinstance(payload, dict):
        return None

    items = payload.get("data")
    if not isinstance(items, list) or not items:
        return None

    errors: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        status = str(item.get("indexing_status") or "").strip().lower()
        if status not in ("error", "failed"):
            continue

        doc_id = str(item.get("id") or item.get("document_id") or "").strip()
        err = str(item.get("error") or item.get("message") or "").strip()
        if not err:
            err = f"indexing_status={status}"
        errors.append(f"{doc_id}: {err}" if doc_id else err)

    if not errors:
        return None

    preview = " | ".join(errors[:3])
    if len(errors) > 3:
        preview = f"{preview} (+{len(errors) - 3} more)"
    return preview


def _is_public_ip(ip_value: str) -> bool:
    try:
        ip_obj = ip_address(ip_value)
    except ValueError:
        return False

    return not (
        ip_obj.is_private
        or ip_obj.is_loopback
        or ip_obj.is_link_local
        or ip_obj.is_reserved
        or ip_obj.is_multicast
        or ip_obj.is_unspecified
    )


def _is_public_host(host: str) -> bool:
    hostname = (host or "").strip()
    if not hostname:
        return False
    if hostname.lower() in {"localhost"}:
        return False

    # IP literal
    if _is_public_ip(hostname):
        return True
    try:
        ip_address(hostname)
        return False
    except ValueError:
        pass

    try:
        infos = socket.getaddrinfo(hostname, None)
    except OSError:
        return False

    addrs: set[str] = set()
    for info in infos:
        sockaddr = info[4]
        if not sockaddr:
            continue
        ip_str = sockaddr[0]
        if ip_str:
            addrs.add(ip_str)

    if not addrs:
        return False

    return all(_is_public_ip(ip_str) for ip_str in addrs)


def _host_matches_allowlist(host: str, patterns: list[str]) -> bool:
    hostname = (host or "").strip().lower().strip(".")
    if not hostname:
        return False

    for raw in patterns:
        p = (raw or "").strip().lower()
        if not p:
            continue
        if p.startswith("*."):
            p = p[1:]
        if p.startswith("."):
            suffix = p.lstrip(".")
            if suffix and (hostname == suffix or hostname.endswith(f".{suffix}")):
                return True
            continue
        if hostname == p:
            return True

    return False


def save_note_to_file(task_id: str, note, extra: Optional[dict[str, Any]] = None):
    task_dir = _task_dir(task_id)
    task_dir.mkdir(parents=True, exist_ok=True)
    payload = asdict(note)
    if extra:
        payload.update(extra)
    with open(_task_result_path(task_id), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def run_note_task(task_id: str, video_url: str, platform: str, quality: DownloadQuality,
                  link: bool = False, screenshot: bool = False, model_name: str = None, provider_id: str = None,
                  _format: list = None, style: str = None, extras: str = None, video_understanding: bool = False,
                  video_interval=0, grid_size=[]
                  ):

    if not model_name or not provider_id:
        raise HTTPException(status_code=400, detail="请选择模型和提供者")

    try:
        task_manager.ensure(task_id)
        created_at_ms = int(time.time() * 1000)

        request_meta = {
            "video_url": str(video_url or ""),
            "platform": str(platform or ""),
            "quality": getattr(quality, "value", None) or str(quality or ""),
            "link": bool(link),
            "screenshot": bool(screenshot),
            "model_name": str(model_name or ""),
            "provider_id": str(provider_id or ""),
            "format": list(_format or []),
            "style": str(style or ""),
            "extras": str(extras or ""),
            "video_understanding": bool(video_understanding),
            "video_interval": int(video_interval or 0),
            "grid_size": list(grid_size or []),
        }

        # Persist request meta early so UI can show model/style even after restart (or while still running).
        try:
            _task_dir(task_id).mkdir(parents=True, exist_ok=True)
            _atomic_merge_json_file(_task_status_path(task_id), {"request": request_meta})
        except Exception:
            pass

        generator = NoteGenerator()
        note = generator.generate(
            video_url=video_url,
            platform=platform,
            quality=quality,
            task_id=task_id,
            model_name=model_name,
            provider_id=provider_id,
            link=link,
            _format=_format,
            style=style,
            extras=extras,
            screenshot=screenshot,
            video_understanding=video_understanding,
            video_interval=video_interval,
            grid_size=grid_size,
        )
        logger.info(f"Note generated: {task_id}")
        if not note or not note.markdown:
            logger.warning(f"任务 {task_id} 未生成结果，跳过保存/上传")
            return

        # Always save note results locally first.
        source_key = make_source_key(
            platform=platform,
            video_id=str(getattr(note.audio_meta, "video_id", "") or ""),
            created_at_ms=created_at_ms,
        )
        sync_id = compute_sync_id(source_key)
        save_note_to_file(
            task_id,
            note,
            extra={
                "sync": {
                    "created_at_ms": created_at_ms,
                    "source_key": source_key,
                    "sync_id": sync_id,
                },
                "request": request_meta,
            },
        )
        try:
            ensure_local_sync_meta(
                note_dir=NOTE_OUTPUT_DIR,
                task_id=task_id,
                platform=platform,
                video_id=str(getattr(note.audio_meta, "video_id", "") or ""),
                title=str(getattr(note.audio_meta, "title", "") or ""),
                prefer_created_at_ms=created_at_ms,
            )
        except Exception:
            pass

        auto_minio = str(os.getenv("AUTO_MINIO_BUNDLE_ON_GENERATE", "false") or "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        auto_dify_raw = _env_bool_or_auto("AUTO_DIFY_INGEST_ON_GENERATE", None)
        dify_cfg: DifyConfig | None = None
        if auto_dify_raw is None:
            # Auto mode (default): enable when Dify service key + any dataset id is configured.
            try:
                dify_cfg = DifyConfig.from_env()
                transcript_dataset_id = (dify_cfg.transcript_dataset_id or dify_cfg.dataset_id).strip()
                note_dataset_id = (dify_cfg.note_dataset_id or dify_cfg.dataset_id).strip()
                auto_dify = bool((dify_cfg.service_api_key or "").strip()) and bool(transcript_dataset_id or note_dataset_id)
            except Exception:
                auto_dify = False
        else:
            auto_dify = bool(auto_dify_raw)

        # Optional: upload bundle to MinIO (source-of-truth for multi-device sync).
        if auto_minio:
            try:
                minio_cfg = MinioConfig.from_env()
                storage = MinioStorage(minio_cfg)
                profile = DifyConfigManager().get_active_profile()
                bucket = bucket_name_for_profile(profile, prefix=minio_cfg.bucket_prefix)
                object_key = f"{minio_cfg.object_prefix}{sync_id}.zip"
                bundle = build_bundle_zip(
                    source_key=source_key,
                    sync_id=sync_id,
                    audio=asdict(note.audio_meta),
                    note_markdown=note.markdown,
                    transcript=asdict(note.transcript),
                    extra_meta={"request": request_meta},
                )
                storage.put_bytes(bucket=bucket, object_key=object_key, data=bundle, content_type="application/zip")
            except MinioConfigError:
                pass
            except Exception as exc:
                logger.warning("MinIO bundle upload failed: %s", exc)

        # Optional: upload transcript + note to Dify Knowledge Base for RAG (separate datasets).
        if auto_dify:
            dify_cfg = dify_cfg or DifyConfig.from_env()
            dify_info: dict[str, Any] = {
                "base_url": dify_cfg.base_url,
                "transcript": None,
                "note": None,
            }
            # Persist early so UI can show "uploading" even if Dify calls take a while.
            try:
                _task_dir(task_id).mkdir(parents=True, exist_ok=True)
                _atomic_merge_json_file(_task_result_path(task_id), {"dify": dify_info})
                _atomic_merge_json_file(_task_status_path(task_id), {"dify": dify_info})
            except Exception:
                pass

            client = DifyKnowledgeClient(dify_cfg)
            try:
                base_name = build_rag_document_name(note.audio_meta, platform, created_at_ms=created_at_ms)
                transcript_dataset_id = (dify_cfg.transcript_dataset_id or dify_cfg.dataset_id).strip()
                note_dataset_id = (dify_cfg.note_dataset_id or dify_cfg.dataset_id).strip()

                dify_errors: dict[str, str] = {}

                if transcript_dataset_id:
                    try:
                        transcript_name = f"{base_name} (transcript)"
                        transcript_text = build_rag_document_text(
                            audio=note.audio_meta,
                            transcript=note.transcript,
                            platform=platform,
                            source_url=video_url,
                        )
                        resp_transcript = client.create_document_by_text(
                            dataset_id=transcript_dataset_id,
                            name=transcript_name,
                            text=transcript_text,
                            doc_language="Chinese Simplified",
                        )
                        doc_transcript = resp_transcript.get("document") or {}
                        dify_info["transcript"] = {
                            "dataset_id": transcript_dataset_id,
                            "document_id": doc_transcript.get("id"),
                            "batch": resp_transcript.get("batch"),
                        }
                        # Backward-compatible primary fields (use transcript).
                        dify_info["dataset_id"] = transcript_dataset_id
                        dify_info["document_id"] = doc_transcript.get("id")
                        dify_info["batch"] = resp_transcript.get("batch")
                    except DifyError as exc:
                        dify_errors["transcript"] = str(exc)
                else:
                    dify_errors["transcript"] = "Missing transcript dataset id"

                if note_dataset_id:
                    try:
                        note_name = f"{base_name} (note)"
                        note_text = build_rag_note_document_text(
                            audio=note.audio_meta,
                            platform=platform,
                            source_url=video_url,
                            note_markdown=note.markdown,
                        )
                        resp_note = client.create_document_by_text(
                            dataset_id=note_dataset_id,
                            name=note_name,
                            text=note_text,
                            doc_language="Chinese Simplified",
                        )
                        doc_note = resp_note.get("document") or {}
                        dify_info["note"] = {
                            "dataset_id": note_dataset_id,
                            "document_id": doc_note.get("id"),
                            "batch": resp_note.get("batch"),
                        }
                    except DifyError as exc:
                        dify_errors["note"] = str(exc)
                else:
                    dify_errors["note"] = "Missing note dataset id"
            finally:
                client.close()

            _task_dir(task_id).mkdir(parents=True, exist_ok=True)
            result_path = _task_result_path(task_id)
            status_path = _task_status_path(task_id)
            _atomic_merge_json_file(result_path, {"dify": dify_info})
            _atomic_merge_json_file(status_path, {"dify": dify_info})
            if dify_errors:
                _atomic_merge_json_file(status_path, {"dify_error": json.dumps(dify_errors, ensure_ascii=False)})
                logger.error(f"Dify upload partially failed (task_id={task_id}): {dify_errors}")
            else:
                logger.info(f"Uploaded to Dify (task_id={task_id})")
    except DifyError as exc:
        status_path = _task_status_path(task_id)
        _atomic_merge_json_file(status_path, {"dify_error": str(exc)})
        logger.error(f"Dify upload failed (task_id={task_id}): {exc}")
    except Exception as exc:
        status_path = _task_status_path(task_id)
        _atomic_merge_json_file(status_path, {"dify_error": str(exc)})
        logger.error(f"Dify upload failed (task_id={task_id}): {exc}", exc_info=True)
    finally:
        task_manager.cleanup(task_id)



@router.post('/delete_task')
def delete_task(data: RecordRequest):
    try:
        task_id = (data.task_id or "").strip()
        if not task_id and data.video_id and data.platform:
            task_id = get_task_by_video(data.video_id, data.platform) or ""

        if task_id:
            # Cooperative cancellation (background task checks this flag).
            task_manager.cancel(task_id)

            status_path = _pick_existing_path(_task_status_path(task_id), _legacy_status_path(task_id))
            status = None
            if status_path and status_path.exists():
                try:
                    status = json.loads(status_path.read_text(encoding="utf-8")).get("status")
                except Exception:
                    status = None

            # Only override status while still running; keep SUCCESS/FAILED records intact.
            if status not in (TaskStatus.SUCCESS.value, TaskStatus.FAILED.value, TaskStatus.CANCELLED.value):
                NoteGenerator()._update_status(task_id, TaskStatus.CANCELLED, message="任务已取消")

            # Best-effort local cleanup (files on disk).
            _delete_task_files(task_id)

        # Best-effort DB cleanup for completed tasks (video_id may be empty while running).
        if data.video_id and data.platform:
            NoteGenerator().delete_note(video_id=data.video_id, platform=data.platform)
        elif task_id:
            delete_task_by_task_id(task_id)

        return R.success(msg='删除成功')
    except Exception as e:
        return R.error(msg=str(e))


@router.post("/upload")
async def upload(file: UploadFile = File(...)):
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    original_name = os.path.basename(file.filename or "")
    if not original_name:
        original_name = f"{uuid.uuid4().hex}.mp4"

    name, ext = os.path.splitext(original_name)
    # Sanitize for cross-platform filesystem safety.
    name = re.sub(r'[<>:"/\\\\|?*\\x00-\\x1f]+', "_", name).strip() or uuid.uuid4().hex
    ext = ext or ".mp4"
    candidate = f"{name}{ext}"
    counter = 1
    while (UPLOAD_DIR / candidate).exists():
        candidate = f"{name}_{counter}{ext}"
        counter += 1

    file_location = UPLOAD_DIR / candidate

    try:
        with open(file_location, "wb") as f:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                f.write(chunk)
    except Exception:
        try:
            if file_location.exists():
                file_location.unlink()
        except Exception:
            pass
        raise

    # 假设你静态目录挂载了 /uploads
    return R.success({"url": f"/uploads/{candidate}"})


@router.post("/generate_note")
def generate_note(data: VideoRequest, background_tasks: BackgroundTasks):
    try:

        video_id = extract_video_id(data.video_url, data.platform)
        # if not video_id:
        #     raise HTTPException(status_code=400, detail="无法提取视频 ID")
        # existing = get_task_by_video(video_id, data.platform)
        # if existing:
        #     return R.error(
        #         msg='笔记已生成，请勿重复发起',
        #
        #     )
        if data.task_id:
            # 如果传了task_id，说明是重试！
            task_id = data.task_id
            # 更新之前的状态
            NoteGenerator()._update_status(
                task_id,
                TaskStatus.PENDING,
                extra={
                    "dify": None,
                    "dify_error": None,
                    "dify_indexing": None,
                    "transcribed_seconds": None,
                    "total_seconds": None,
                },
            )
            logger.info(f"重试模式，复用已有 task_id={task_id}")
        else:
            # 正常新建任务
            task_id = str(uuid.uuid4())

        background_tasks.add_task(run_note_task, task_id, data.video_url, data.platform, data.quality, data.link,
                                  data.screenshot, data.model_name, data.provider_id, data.format, data.style,
                                  data.extras, data.video_understanding, data.video_interval, data.grid_size)
        return R.success({"task_id": task_id})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def _parse_audio_meta(payload: Any) -> AudioDownloadResult:
    if not isinstance(payload, dict):
        raise ValueError("note result is not a JSON object")

    audio_meta = payload.get("audio_meta")
    if not isinstance(audio_meta, dict):
        raise ValueError("missing audio_meta in note result")

    return AudioDownloadResult(
        file_path=str(audio_meta.get("file_path") or ""),
        title=str(audio_meta.get("title") or ""),
        duration=float(audio_meta.get("duration") or 0),
        cover_url=audio_meta.get("cover_url"),
        platform=str(audio_meta.get("platform") or ""),
        video_id=str(audio_meta.get("video_id") or ""),
        raw_info=audio_meta.get("raw_info") if isinstance(audio_meta.get("raw_info"), dict) else {},
        video_path=audio_meta.get("video_path"),
    )


def _parse_transcript(payload: Any) -> TranscriptResult:
    if not isinstance(payload, dict):
        raise ValueError("note result is not a JSON object")

    transcript = payload.get("transcript")
    if not isinstance(transcript, dict):
        return TranscriptResult(language=None, full_text="", segments=[], raw=None)

    segments: list[TranscriptSegment] = []
    raw_segments = transcript.get("segments")
    if isinstance(raw_segments, list):
        for seg in raw_segments:
            if not isinstance(seg, dict):
                continue
            text = str(seg.get("text") or "").strip()
            if not text:
                continue
            try:
                start = float(seg.get("start") or 0)
                end = float(seg.get("end") or start)
            except (TypeError, ValueError):
                start = 0.0
                end = 0.0
            segments.append(TranscriptSegment(start=start, end=end, text=text))

    return TranscriptResult(
        language=str(transcript.get("language") or "") or None,
        full_text=str(transcript.get("full_text") or ""),
        segments=segments,
        raw=transcript.get("raw"),
    )


def _extract_markdown(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    md = payload.get("markdown")
    if isinstance(md, str):
        return md
    # Backward-compatible: some old results may store markdown versions as a list.
    if isinstance(md, list) and md:
        first = md[0]
        if isinstance(first, dict) and isinstance(first.get("content"), str):
            return str(first.get("content") or "")
        if isinstance(first, str):
            return first
    return ""


def _get_existing_dify_doc(dify: Any, kind: str) -> tuple[Optional[str], Optional[str]]:
    """
    Returns (dataset_id, document_id) for a given kind ("transcript" or "note").
    Backward-compatible: legacy `dify.dataset_id/document_id` maps to transcript.
    """
    if not isinstance(dify, dict):
        return None, None

    if kind in dify and isinstance(dify.get(kind), dict):
        info = dify.get(kind) or {}
        return (
            str(info.get("dataset_id") or "").strip() or None,
            str(info.get("document_id") or "").strip() or None,
        )

    if kind == "transcript":
        return (
            str(dify.get("dataset_id") or "").strip() or None,
            str(dify.get("document_id") or "").strip() or None,
        )

    return None, None


@router.post("/reingest_dify")
def reingest_dify(data: ReingestRequest):
    task_id = str(data.task_id or "").strip()
    if not task_id:
        return R.error("Missing task_id", code=400)

    result_path = _pick_existing_path(_task_result_path(task_id), _legacy_result_path(task_id))
    if not result_path:
        return R.error("Note result file not found", code=404)

    status_path = _pick_existing_path(_task_status_path(task_id), _legacy_status_path(task_id)) or _task_status_path(task_id)
    _task_dir(task_id).mkdir(parents=True, exist_ok=True)

    try:
        result_content = json.loads(result_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return R.error(f"Failed to read note result: {exc}", code=500)

    status_content: dict[str, Any] = {}
    if status_path and status_path.exists():
        try:
            status_content = json.loads(status_path.read_text(encoding="utf-8"))
        except Exception:
            status_content = {}

    try:
        audio = _parse_audio_meta(result_content)
        transcript = _parse_transcript(result_content)
        markdown = _extract_markdown(result_content)
    except Exception as exc:
        return R.error(f"Invalid note result: {exc}", code=500)

    platform = str(data.platform or "").strip() or str(audio.platform or "").strip() or "unknown"
    source_url = str(data.video_url or "").strip()
    if not markdown:
        return R.error("Note markdown is empty; cannot ingest to Dify", code=400)

    dify_cfg = DifyConfig.from_env()
    client = DifyKnowledgeClient(dify_cfg)
    try:
        created_at_ms: int | None = None
        sync_info = result_content.get("sync") if isinstance(result_content.get("sync"), dict) else {}
        raw_created_at = sync_info.get("created_at_ms")
        if raw_created_at is not None:
            try:
                created_at_ms = int(raw_created_at)
            except (TypeError, ValueError):
                created_at_ms = None

        try:
            meta = ensure_local_sync_meta(
                note_dir=NOTE_OUTPUT_DIR,
                task_id=task_id,
                platform=platform,
                video_id=str(audio.video_id or ""),
                title=str(audio.title or ""),
                prefer_created_at_ms=created_at_ms,
            )
            v = meta.get("created_at_ms")
            if isinstance(v, int) and v > 0:
                created_at_ms = v
        except Exception:
            pass

        base_name = build_rag_document_name(audio, platform, created_at_ms=created_at_ms)
        transcript_dataset_id = (dify_cfg.transcript_dataset_id or dify_cfg.dataset_id).strip()
        note_dataset_id = (dify_cfg.note_dataset_id or dify_cfg.dataset_id).strip()

        # Prefer the latest dify info in status file, fallback to result file.
        prev_dify = status_content.get("dify")
        if not isinstance(prev_dify, dict):
            prev_dify = result_content.get("dify")

        dify_info: dict[str, Any] = {
            "base_url": dify_cfg.base_url,
            "transcript": None,
            "note": None,
        }
        dify_errors: dict[str, str] = {}

        if data.include_transcript:
            if transcript_dataset_id:
                transcript_name = f"{base_name} (transcript)"
                transcript_text = build_rag_document_text(
                    audio=audio,
                    transcript=transcript,
                    platform=platform,
                    source_url=source_url,
                )
                prev_dataset_id, prev_document_id = _get_existing_dify_doc(prev_dify, "transcript")
                try:
                    if prev_document_id and prev_dataset_id == transcript_dataset_id:
                        resp_transcript = client.update_document_by_text(
                            dataset_id=transcript_dataset_id,
                            document_id=prev_document_id,
                            name=transcript_name,
                            text=transcript_text,
                            doc_language="Chinese Simplified",
                        )
                    else:
                        resp_transcript = client.create_document_by_text(
                            dataset_id=transcript_dataset_id,
                            name=transcript_name,
                            text=transcript_text,
                            doc_language="Chinese Simplified",
                        )
                    doc_transcript = resp_transcript.get("document") or {}
                    dify_info["transcript"] = {
                        "dataset_id": transcript_dataset_id,
                        "document_id": doc_transcript.get("id"),
                        "batch": resp_transcript.get("batch"),
                    }
                    # Backward-compatible primary fields (use transcript).
                    dify_info["dataset_id"] = transcript_dataset_id
                    dify_info["document_id"] = doc_transcript.get("id")
                    dify_info["batch"] = resp_transcript.get("batch")
                except DifyError as exc:
                    dify_errors["transcript"] = str(exc)
            else:
                dify_errors["transcript"] = "Missing transcript dataset id"

        if data.include_note:
            if note_dataset_id:
                note_name = f"{base_name} (note)"
                note_text = build_rag_note_document_text(
                    audio=audio,
                    platform=platform,
                    source_url=source_url,
                    note_markdown=markdown,
                )
                prev_note_dataset_id, prev_note_document_id = _get_existing_dify_doc(prev_dify, "note")
                try:
                    if prev_note_document_id and prev_note_dataset_id == note_dataset_id:
                        resp_note = client.update_document_by_text(
                            dataset_id=note_dataset_id,
                            document_id=prev_note_document_id,
                            name=note_name,
                            text=note_text,
                            doc_language="Chinese Simplified",
                        )
                    else:
                        resp_note = client.create_document_by_text(
                            dataset_id=note_dataset_id,
                            name=note_name,
                            text=note_text,
                            doc_language="Chinese Simplified",
                        )
                    doc_note = resp_note.get("document") or {}
                    dify_info["note"] = {
                        "dataset_id": note_dataset_id,
                        "document_id": doc_note.get("id"),
                        "batch": resp_note.get("batch"),
                    }
                except DifyError as exc:
                    dify_errors["note"] = str(exc)
            else:
                dify_errors["note"] = "Missing note dataset id"
    finally:
        client.close()

    _atomic_merge_json_file(result_path, {"dify": dify_info})
    _atomic_merge_json_file(
        status_path,
        {
            "status": TaskStatus.SUCCESS.value,
            "progress": 100,
            "dify": dify_info,
            "dify_error": None,
            "dify_indexing": None,
        },
    )

    dify_error: Optional[str] = None
    if dify_errors:
        dify_error = json.dumps(dify_errors, ensure_ascii=False)
        _atomic_merge_json_file(status_path, {"dify_error": dify_error})

    return R.success({"task_id": task_id, "dify": dify_info, "dify_error": dify_error})


@router.get("/task_status/{task_id}")
def get_task_status(task_id: str):
    status_path = _pick_existing_path(_task_status_path(task_id), _legacy_status_path(task_id))
    result_path = _pick_existing_path(_task_result_path(task_id), _legacy_result_path(task_id))

    # 优先读状态文件
    if status_path and status_path.exists():
        status_content = json.loads(status_path.read_text(encoding="utf-8"))

        status = status_content.get("status")
        message = status_content.get("message", "")
        dify_info = status_content.get("dify")
        dify_error = status_content.get("dify_error")
        request_meta = status_content.get("request") if isinstance(status_content.get("request"), dict) else None
        progress = status_content.get("progress")
        if not isinstance(progress, (int, float)):
            progress = TaskStatus.progress(status)
        progress = max(0, min(100, int(progress)))

        if status == TaskStatus.SUCCESS.value:
            # 成功状态的话，继续读取最终笔记内容
            if result_path and result_path.exists():
                result_content = json.loads(result_path.read_text(encoding="utf-8"))
                request_effective = request_meta
                if request_effective is None and isinstance(result_content, dict) and isinstance(result_content.get("request"), dict):
                    request_effective = result_content.get("request")

                # If we have a Dify batch id, attach real-time indexing status.
                dify_info = dify_info or result_content.get("dify")
                dify_indexing = None
                if isinstance(dify_info, dict):
                    try:
                        dify_cfg = DifyConfig.from_env()
                        dify_client = DifyKnowledgeClient(dify_cfg)
                        try:
                            merged_data: list[Any] = []
                            per_dataset: dict[str, Any] = {}

                            for key in ("transcript", "note"):
                                info = dify_info.get(key)
                                if not isinstance(info, dict):
                                    continue
                                batch = info.get("batch")
                                dataset_id = info.get("dataset_id")
                                if not batch or not dataset_id:
                                    continue
                                payload = dify_client.get_batch_indexing_status(
                                    batch=str(batch),
                                    dataset_id=str(dataset_id),
                                )
                                per_dataset[key] = payload
                                data_list = payload.get("data")
                                if isinstance(data_list, list):
                                    merged_data.extend(data_list)

                            # Backward-compatible: if no per-dataset info, fall back to legacy fields.
                            if not per_dataset and dify_info.get("batch"):
                                payload = dify_client.get_batch_indexing_status(
                                    batch=str(dify_info["batch"]),
                                    dataset_id=str(dify_info.get("dataset_id") or ""),
                                )
                                per_dataset["primary"] = payload
                                data_list = payload.get("data")
                                if isinstance(data_list, list):
                                    merged_data.extend(data_list)

                            if per_dataset:
                                dify_indexing = {**per_dataset, "data": merged_data}
                                indexing_error = _extract_dify_indexing_error(dify_indexing)
                                if indexing_error and not dify_error:
                                    dify_error = indexing_error
                        finally:
                            dify_client.close()
                    except Exception as exc:
                        dify_error = dify_error or str(exc)
                return R.success({
                    "status": status,
                    "progress": progress,
                    "result": result_content,
                    "message": message,
                    "dify": dify_info,
                    "dify_indexing": dify_indexing,
                    "dify_error": dify_error,
                    "request": request_effective,
                    "task_id": task_id
                })
            else:
                # 理论上不会出现，保险处理
                return R.success({
                    "status": TaskStatus.PENDING.value,
                    "progress": progress,
                    "request": request_meta,
                    "message": "任务完成，但结果文件未找到",
                    "task_id": task_id
                })

        if status == TaskStatus.FAILED.value:
            return R.error(message or "任务失败", code=500)

        # 处理中状态
        return R.success({
            "status": status,
            "progress": progress,
            "request": request_meta,
            "message": message,
            "dify": dify_info,
            "dify_error": dify_error,
            "task_id": task_id
        })

    # 没有状态文件，但有结果
    if result_path and result_path.exists():
        result_content = json.loads(result_path.read_text(encoding="utf-8"))
        return R.success({
            "status": TaskStatus.SUCCESS.value,
            "progress": 100,
            "result": result_content,
            "dify": result_content.get("dify"),
            "request": result_content.get("request") if isinstance(result_content.get("request"), dict) else None,
            "task_id": task_id
        })

    # 什么都没有，默认PENDING
    return R.success({
        "status": TaskStatus.PENDING.value,
        "progress": 0,
        "message": "任务排队中",
        "task_id": task_id
    })


@router.get("/image_proxy")
async def image_proxy(request: Request, url: str):
    raw_url = str(url or "").strip()
    if not raw_url:
        raise HTTPException(status_code=400, detail="Missing url")

    parsed = urlparse(raw_url)
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(status_code=400, detail="Only http(s) URLs are allowed")
    if parsed.username or parsed.password:
        raise HTTPException(status_code=400, detail="URL userinfo is not allowed")

    host = parsed.hostname
    if not host:
        raise HTTPException(status_code=400, detail="Invalid url host")

    allowlist_raw = (os.getenv("IMAGE_PROXY_ALLOWED_HOSTS") or "").strip()
    if allowlist_raw:
        patterns = [p.strip() for p in allowlist_raw.split(",")]
        if not _host_matches_allowlist(host, patterns):
            raise HTTPException(status_code=403, detail="Forbidden host")

    if not _is_public_host(host):
        raise HTTPException(status_code=403, detail="Forbidden host")

    headers = {
        "Referer": "https://www.bilibili.com/",
        "User-Agent": request.headers.get("User-Agent", ""),
    }

    try:
        max_bytes = int(os.getenv("IMAGE_PROXY_MAX_BYTES", "10485760") or "10485760")
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(raw_url, headers=headers)

            if resp.status_code != 200:
                raise HTTPException(status_code=resp.status_code, detail="图片获取失败")

            content_type = resp.headers.get("Content-Type", "image/jpeg")
            content = resp.content
            if max_bytes > 0 and len(content) > max_bytes:
                raise HTTPException(status_code=413, detail="Image too large")

            return Response(
                content=content,
                media_type=content_type,
                headers={
                    "Cache-Control": "public, max-age=86400",  #  缓存一天
                    "Content-Type": content_type,
                },
            )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
