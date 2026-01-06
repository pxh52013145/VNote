# app/routers/note.py
import json
import os
import re
import uuid
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

from fastapi import APIRouter, BackgroundTasks, File, HTTPException, Request, UploadFile
from pydantic import BaseModel, validator, field_validator
from dataclasses import asdict

from app.db.video_task_dao import get_task_by_video
from app.enmus.exception import NoteErrorEnum
from app.enmus.note_enums import DownloadQuality
from app.enmus.task_status_enums import TaskStatus
from app.exceptions.note import NoteError
from app.services.dify_client import DifyConfig, DifyError, DifyKnowledgeClient
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
from fastapi.responses import StreamingResponse
import httpx

# from app.services.downloader import download_raw_audio
# from app.services.whisperer import transcribe_audio

router = APIRouter()


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


NOTE_OUTPUT_DIR = os.getenv("NOTE_OUTPUT_DIR", "note_results")
UPLOAD_DIR = "uploads"


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


def save_note_to_file(task_id: str, note, extra: Optional[dict[str, Any]] = None):
    os.makedirs(NOTE_OUTPUT_DIR, exist_ok=True)
    payload = asdict(note)
    if extra:
        payload.update(extra)
    with open(os.path.join(NOTE_OUTPUT_DIR, f"{task_id}.json"), "w", encoding="utf-8") as f:
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
        save_note_to_file(task_id, note)

        # Upload transcript + note to Dify Knowledge Base for RAG (separate datasets).
        dify_cfg = DifyConfig.from_env()
        client = DifyKnowledgeClient(dify_cfg)
        try:
            base_name = build_rag_document_name(note.audio_meta, platform)
            transcript_dataset_id = (dify_cfg.transcript_dataset_id or dify_cfg.dataset_id).strip()
            note_dataset_id = (dify_cfg.note_dataset_id or dify_cfg.dataset_id).strip()

            dify_info: dict[str, Any] = {
                "base_url": dify_cfg.base_url,
                "transcript": None,
                "note": None,
            }
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

        result_path = Path(NOTE_OUTPUT_DIR) / f"{task_id}.json"
        status_path = Path(NOTE_OUTPUT_DIR) / f"{task_id}.status.json"
        _atomic_merge_json_file(result_path, {"dify": dify_info})
        _atomic_merge_json_file(status_path, {"dify": dify_info})
        if dify_errors:
            _atomic_merge_json_file(status_path, {"dify_error": json.dumps(dify_errors, ensure_ascii=False)})
            logger.error(f"Dify upload partially failed (task_id={task_id}): {dify_errors}")
        else:
            logger.info(f"Uploaded to Dify (task_id={task_id})")
    except DifyError as exc:
        status_path = Path(NOTE_OUTPUT_DIR) / f"{task_id}.status.json"
        _atomic_merge_json_file(status_path, {"dify_error": str(exc)})
        logger.error(f"Dify upload failed (task_id={task_id}): {exc}")
    except Exception as exc:
        status_path = Path(NOTE_OUTPUT_DIR) / f"{task_id}.status.json"
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

            status_path = Path(NOTE_OUTPUT_DIR) / f"{task_id}.status.json"
            status = None
            if status_path.exists():
                try:
                    status = json.loads(status_path.read_text(encoding="utf-8")).get("status")
                except Exception:
                    status = None

            # Only override status while still running; keep SUCCESS/FAILED records intact.
            if status not in (TaskStatus.SUCCESS.value, TaskStatus.FAILED.value, TaskStatus.CANCELLED.value):
                NoteGenerator()._update_status(task_id, TaskStatus.CANCELLED, message="任务已取消")

        # Best-effort DB cleanup for completed tasks (video_id may be empty while running).
        if data.video_id and data.platform:
            NoteGenerator().delete_note(video_id=data.video_id, platform=data.platform)

        return R.success(msg='删除成功')
    except Exception as e:
        return R.error(msg=str(e))


@router.post("/upload")
async def upload(file: UploadFile = File(...)):
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    file_location = os.path.join(UPLOAD_DIR, file.filename)

    with open(file_location, "wb+") as f:
        f.write(await file.read())

    # 假设你静态目录挂载了 /uploads
    return R.success({"url": f"/uploads/{file.filename}"})


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

    result_path = Path(NOTE_OUTPUT_DIR) / f"{task_id}.json"
    if not result_path.exists():
        return R.error("Note result file not found", code=404)

    status_path = Path(NOTE_OUTPUT_DIR) / f"{task_id}.status.json"

    try:
        result_content = json.loads(result_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return R.error(f"Failed to read note result: {exc}", code=500)

    status_content: dict[str, Any] = {}
    if status_path.exists():
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
        base_name = build_rag_document_name(audio, platform)
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
    status_path = os.path.join(NOTE_OUTPUT_DIR, f"{task_id}.status.json")
    result_path = os.path.join(NOTE_OUTPUT_DIR, f"{task_id}.json")

    # 优先读状态文件
    if os.path.exists(status_path):
        with open(status_path, "r", encoding="utf-8") as f:
            status_content = json.load(f)

        status = status_content.get("status")
        message = status_content.get("message", "")
        dify_info = status_content.get("dify")
        dify_error = status_content.get("dify_error")
        progress = status_content.get("progress")
        if not isinstance(progress, (int, float)):
            progress = TaskStatus.progress(status)
        progress = max(0, min(100, int(progress)))

        if status == TaskStatus.SUCCESS.value:
            # 成功状态的话，继续读取最终笔记内容
            if os.path.exists(result_path):
                with open(result_path, "r", encoding="utf-8") as rf:
                    result_content = json.load(rf)

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
                    "task_id": task_id
                })
            else:
                # 理论上不会出现，保险处理
                return R.success({
                    "status": TaskStatus.PENDING.value,
                    "progress": progress,
                    "message": "任务完成，但结果文件未找到",
                    "task_id": task_id
                })

        if status == TaskStatus.FAILED.value:
            return R.error(message or "任务失败", code=500)

        # 处理中状态
        return R.success({
            "status": status,
            "progress": progress,
            "message": message,
            "dify": dify_info,
            "dify_error": dify_error,
            "task_id": task_id
        })

    # 没有状态文件，但有结果
    if os.path.exists(result_path):
        with open(result_path, "r", encoding="utf-8") as f:
            result_content = json.load(f)
        return R.success({
            "status": TaskStatus.SUCCESS.value,
            "progress": 100,
            "result": result_content,
            "dify": result_content.get("dify"),
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
    headers = {
        "Referer": "https://www.bilibili.com/",
        "User-Agent": request.headers.get("User-Agent", ""),
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, headers=headers)

            if resp.status_code != 200:
                raise HTTPException(status_code=resp.status_code, detail="图片获取失败")

            content_type = resp.headers.get("Content-Type", "image/jpeg")
            return StreamingResponse(
                resp.aiter_bytes(),
                media_type=content_type,
                headers={
                    "Cache-Control": "public, max-age=86400",  #  缓存一天
                    "Content-Type": content_type,
                }
            )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
