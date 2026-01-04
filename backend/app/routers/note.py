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
from app.services.rag_service import (
    build_rag_document_name,
    build_rag_document_text,
    build_rag_note_document_text,
)
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

    note = NoteGenerator().generate(
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
        screenshot=screenshot
        , video_understanding=video_understanding,
        video_interval=video_interval,
        grid_size=grid_size
    )
    logger.info(f"Note generated: {task_id}")
    if not note or not note.markdown:
        logger.warning(f"任务 {task_id} 执行失败，跳过保存")
        return

    # Always save note results locally first.
    save_note_to_file(task_id, note)

    # Upload transcript + note to Dify Knowledge Base for RAG (separate datasets).
    try:
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



@router.post('/delete_task')
def delete_task(data: RecordRequest):
    try:
        # TODO: 待持久化完成
        # NoteGenerator().delete_note(video_id=data.video_id, platform=data.platform)
        return R.success(msg='删除成功')
    except Exception as e:
        return R.error(msg=e)


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
            NoteGenerator()._update_status(task_id, TaskStatus.PENDING)
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
