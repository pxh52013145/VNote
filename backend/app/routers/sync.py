from __future__ import annotations

import io
import hashlib
import json
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter
from pydantic import BaseModel, field_validator

from app.services.dify_client import DifyConfig, DifyError, DifyKnowledgeClient
from app.services.dify_config_manager import DifyConfigManager
from app.services.library_sync import (
    audio_from_json,
    build_bundle_zip,
    compute_sync_id,
    ensure_local_sync_meta,
    load_local_note_item,
    make_source_key,
    parse_dify_sync_tag,
    scan_local_notes,
    transcript_from_json,
)
from app.services.minio_storage import MinioConfig, MinioConfigError, MinioStorage, bucket_name_for_profile
from app.services.rag_service import build_rag_document_name, build_rag_document_text, build_rag_note_document_text
from app.db.engine import get_db
from app.db.models.sync_items import SyncItem
from app.utils.paths import note_output_dir
from app.utils.response import ResponseWrapper as R

router = APIRouter()


def _list_all_documents(client: DifyKnowledgeClient, *, dataset_id: str) -> list[dict[str, Any]]:
    docs_all: list[dict[str, Any]] = []
    page = 1
    while True:
        resp = client.list_documents(dataset_id=dataset_id, page=page, limit=100)
        if not isinstance(resp, dict):
            break
        batch = resp.get("data")
        if isinstance(batch, list):
            docs_all.extend([d for d in batch if isinstance(d, dict)])
        if not resp.get("has_more"):
            break
        page += 1
        if page > 200:
            break
    return docs_all


def _find_document_by_name(
    client: DifyKnowledgeClient,
    *,
    dataset_id: str,
    name: str,
) -> Optional[dict[str, Any]]:
    target = (name or "").strip()
    if not target:
        return None
    page = 1
    while True:
        resp = client.list_documents(dataset_id=dataset_id, page=page, limit=100)
        if not isinstance(resp, dict):
            return None
        batch = resp.get("data")
        if isinstance(batch, list):
            for d in batch:
                if not isinstance(d, dict):
                    continue
                if str(d.get("name") or "").strip() == target:
                    return d
        if not resp.get("has_more"):
            return None
        page += 1
        if page > 200:
            return None


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _read_local_payloads(item) -> tuple[dict[str, Any] | None, dict[str, Any] | None, str]:
    audio_json: dict[str, Any] | None = None
    transcript_json: dict[str, Any] | None = None
    note_markdown = ""

    if item.audio_path and item.audio_path.exists():
        audio_json = _read_json(item.audio_path)
    if item.transcript_path and item.transcript_path.exists():
        transcript_json = _read_json(item.transcript_path)
    if item.markdown_path and item.markdown_path.exists():
        try:
            note_markdown = item.markdown_path.read_text(encoding="utf-8")
        except Exception:
            note_markdown = ""

    if (audio_json is None or transcript_json is None or not note_markdown.strip()) and item.result_path and item.result_path.exists():
        res = _read_json(item.result_path) or {}
        if audio_json is None and isinstance(res.get("audio_meta"), dict):
            audio_json = res.get("audio_meta")
        if transcript_json is None and isinstance(res.get("transcript"), dict):
            transcript_json = res.get("transcript")
        if not note_markdown.strip():
            md = res.get("markdown")
            if isinstance(md, str):
                note_markdown = md

    return audio_json, transcript_json, note_markdown


def _read_local_request_meta(item) -> dict[str, Any] | None:
    for p in (getattr(item, "result_path", None), getattr(item, "status_path", None)):
        if not isinstance(p, Path) or not p.exists():
            continue
        payload = _read_json(p)
        if isinstance(payload, dict) and isinstance(payload.get("request"), dict):
            return payload.get("request") or {}
    return None


def _sync_bucket(profile_name: str) -> tuple[MinioStorage, str]:
    cfg = MinioConfig.from_env()
    storage = MinioStorage(cfg)
    bucket = bucket_name_for_profile(profile_name, prefix=cfg.bucket_prefix)
    return storage, bucket


def _bundle_object_key(storage: MinioStorage, *, sync_id: str) -> str:
    return f"{storage.config.object_prefix}{sync_id}.zip"


def _tombstone_object_key(storage: MinioStorage, *, sync_id: str) -> str:
    return f"{storage.config.tombstone_prefix}{sync_id}.json"


class SyncScanItem(BaseModel):
    status: str
    title: str
    platform: str
    video_id: str
    created_at_ms: Optional[int] = None
    source_key: Optional[str] = None
    sync_id: Optional[str] = None

    # Local side
    local_task_id: Optional[str] = None
    local_has_note: Optional[bool] = None
    local_has_transcript: Optional[bool] = None

    # Remote side (optional)
    dify_note_document_id: Optional[str] = None
    dify_note_name: Optional[str] = None
    dify_transcript_document_id: Optional[str] = None
    dify_transcript_name: Optional[str] = None

    remote_has_note: Optional[bool] = None
    remote_has_transcript: Optional[bool] = None

    # MinIO hints (optional)
    minio_bundle_exists: Optional[bool] = None
    minio_tombstone_exists: Optional[bool] = None
    bundle_sha256_local: Optional[str] = None
    bundle_sha256_remote: Optional[str] = None
    note_sha256_local: Optional[str] = None
    note_sha256_remote: Optional[str] = None
    transcript_sha256_local: Optional[str] = None
    transcript_sha256_remote: Optional[str] = None


def _iso_utc(dt: datetime | None) -> str | None:
    if not isinstance(dt, datetime):
        return None
    try:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    except Exception:
        return None


@router.post("/sync/scan")
def sync_scan():
    cfg = DifyConfig.from_env()
    profile = DifyConfigManager().get_active_profile()
    note_dataset_id = (cfg.note_dataset_id or cfg.dataset_id).strip()
    transcript_dataset_id = (cfg.transcript_dataset_id or cfg.dataset_id).strip()

    note_dir = note_output_dir()
    local_items = scan_local_notes(note_dir)
    local_by_source: dict[str, Any] = {i.source_key: i for i in local_items if i.source_key}

    remote_note_by_source: dict[str, dict[str, Any]] = {}
    remote_transcript_by_source: dict[str, dict[str, Any]] = {}
    legacy_remote: list[dict[str, Any]] = []

    if cfg.service_api_key and (note_dataset_id or transcript_dataset_id):
        client = DifyKnowledgeClient(cfg)
        try:
            if note_dataset_id:
                for d in _list_all_documents(client, dataset_id=note_dataset_id):
                    name = str(d.get("name") or "").strip()
                    doc_id = str(d.get("id") or d.get("document_id") or "").strip()
                    if not name or not doc_id:
                        continue
                    name_l = name.lower()
                    # When note/transcript share the same dataset, keep them separate by suffix.
                    if "(transcript)" in name_l:
                        continue
                    parsed = parse_dify_sync_tag(name)
                    if not parsed:
                        continue
                    title, platform, video_id, created_at_ms = parsed
                    if created_at_ms is None:
                        legacy_remote.append(
                            {
                                "kind": "note",
                                "title": title or name,
                                "platform": platform,
                                "video_id": video_id,
                                "document_id": doc_id,
                                "name": name,
                            }
                        )
                        continue
                    source_key = make_source_key(platform=platform, video_id=video_id, created_at_ms=created_at_ms)
                    remote_note_by_source[source_key] = {
                        "title": title or name,
                        "platform": platform,
                        "video_id": video_id,
                        "created_at_ms": created_at_ms,
                        "source_key": source_key,
                        "sync_id": compute_sync_id(source_key),
                        "document_id": doc_id,
                        "name": name,
                    }

            if transcript_dataset_id:
                for d in _list_all_documents(client, dataset_id=transcript_dataset_id):
                    name = str(d.get("name") or "").strip()
                    doc_id = str(d.get("id") or d.get("document_id") or "").strip()
                    if not name or not doc_id:
                        continue
                    name_l = name.lower()
                    if "(note)" in name_l:
                        continue
                    parsed = parse_dify_sync_tag(name)
                    if not parsed:
                        continue
                    title, platform, video_id, created_at_ms = parsed
                    if created_at_ms is None:
                        legacy_remote.append(
                            {
                                "kind": "transcript",
                                "title": title or name,
                                "platform": platform,
                                "video_id": video_id,
                                "document_id": doc_id,
                                "name": name,
                            }
                        )
                        continue
                    source_key = make_source_key(platform=platform, video_id=video_id, created_at_ms=created_at_ms)
                    remote_transcript_by_source[source_key] = {
                        "title": title or name,
                        "platform": platform,
                        "video_id": video_id,
                        "created_at_ms": created_at_ms,
                        "source_key": source_key,
                        "sync_id": compute_sync_id(source_key),
                        "document_id": doc_id,
                        "name": name,
                    }
        except DifyError as exc:
            return R.error(msg=str(exc), code=500)
        finally:
            client.close()

    # Merge by source_key (only items with created_at_ms can be joined reliably).
    all_source_keys = set(local_by_source.keys()) | set(remote_note_by_source.keys()) | set(remote_transcript_by_source.keys())
    merged: list[dict[str, Any]] = []

    storage: MinioStorage | None = None
    bucket: str | None = None
    try:
        storage, bucket = _sync_bucket(profile)
        storage.ensure_bucket(bucket)
    except MinioConfigError:
        storage, bucket = None, None
    except Exception:
        storage = storage
        bucket = bucket

    for source_key in all_source_keys:
        local = local_by_source.get(source_key)
        remote_note = remote_note_by_source.get(source_key)
        remote_transcript = remote_transcript_by_source.get(source_key)

        platform = (local.platform if local else (remote_note or remote_transcript or {}).get("platform")) or ""
        video_id = (local.video_id if local else (remote_note or remote_transcript or {}).get("video_id")) or ""
        title = (local.title if local else (remote_note or remote_transcript or {}).get("title")) or ""
        created_at_ms = (local.created_at_ms if local else (remote_note or remote_transcript or {}).get("created_at_ms")) or None
        sync_id = compute_sync_id(source_key) if source_key else None

        has_local = bool(local)
        local_has_note = False
        local_has_transcript = False
        if local:
            try:
                if local.markdown_path and local.markdown_path.exists() and local.markdown_path.stat().st_size > 0:
                    local_has_note = True
            except Exception:
                local_has_note = False
            try:
                if local.transcript_path and local.transcript_path.exists() and local.transcript_path.stat().st_size > 0:
                    local_has_transcript = True
            except Exception:
                local_has_transcript = False

            # Fall back to result.json for legacy/incomplete layouts.
            if (not local_has_note or not local_has_transcript) and local.result_path and local.result_path.exists():
                res = _read_json(local.result_path) or {}
                if not local_has_note:
                    md = res.get("markdown")
                    local_has_note = isinstance(md, str) and bool(md.strip())
                if not local_has_transcript:
                    local_has_transcript = isinstance(res.get("transcript"), dict)

        remote_has_note = bool(remote_note)
        remote_has_transcript = bool(remote_transcript)
        has_remote = remote_has_note or remote_has_transcript

        note_sha256_local: Optional[str] = None
        transcript_sha256_local: Optional[str] = None
        bundle_sha256_local: Optional[str] = None
        if local and (local_has_note or local_has_transcript):
            audio_json, transcript_json, note_markdown = _read_local_payloads(local)
            if local_has_note and (note_markdown or "").strip():
                try:
                    note_bytes = (note_markdown or "").lstrip("\ufeff").encode("utf-8")
                    note_sha256_local = hashlib.sha256(note_bytes).hexdigest()
                except Exception:
                    note_sha256_local = None
            if local_has_transcript and isinstance(transcript_json, dict):
                try:
                    transcript_bytes = json.dumps(transcript_json, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
                    transcript_sha256_local = hashlib.sha256(transcript_bytes).hexdigest()
                except Exception:
                    transcript_sha256_local = None

            # Deterministic bundle hash (for conflict detection / idempotency hints).
            try:
                request_meta = _read_local_request_meta(local)
                extra_meta = {"request": request_meta} if isinstance(request_meta, dict) and request_meta else None
                bundle = build_bundle_zip(
                    source_key=source_key,
                    sync_id=sync_id or compute_sync_id(source_key),
                    audio=audio_json if isinstance(audio_json, dict) else None,
                    note_markdown=note_markdown if local_has_note else None,
                    transcript=transcript_json if local_has_transcript and isinstance(transcript_json, dict) else None,
                    extra_meta=extra_meta,
                )
                bundle_sha256_local = hashlib.sha256(bundle).hexdigest()
            except Exception:
                bundle_sha256_local = None

        bundle_sha256_remote: Optional[str] = None
        note_sha256_remote: Optional[str] = None
        transcript_sha256_remote: Optional[str] = None

        status = "DIFY_ONLY"
        if has_local and has_remote:
            if local_has_note == remote_has_note and local_has_transcript == remote_has_transcript:
                status = "SYNCED"
            else:
                status = "PARTIAL"
        elif has_local and not has_remote:
            status = "LOCAL_ONLY"
        elif not has_local and has_remote:
            status = "DIFY_ONLY"

        minio_bundle_exists: Optional[bool] = None
        minio_tombstone_exists: Optional[bool] = None
        if storage and bucket and sync_id:
            def _meta_get(meta: dict[str, Any], key: str) -> Optional[str]:
                k = (key or "").strip().lower()
                if not k:
                    return None
                candidates = [k, f"x-amz-meta-{k}"]
                for cand in candidates:
                    for kk, vv in meta.items():
                        if str(kk).strip().lower() == cand:
                            if isinstance(vv, (list, tuple)) and vv:
                                vv = vv[0]
                            v = str(vv).strip()
                            return v if v else None
                return None

            try:
                minio_tombstone_exists = storage.stat(bucket=bucket, object_key=_tombstone_object_key(storage, sync_id=sync_id)) is not None
            except Exception:
                minio_tombstone_exists = None

            try:
                st = storage.stat(bucket=bucket, object_key=_bundle_object_key(storage, sync_id=sync_id))
                minio_bundle_exists = st is not None
                if isinstance(st, dict) and isinstance(st.get("metadata"), dict):
                    meta = st.get("metadata") or {}
                    bundle_sha256_remote = _meta_get(meta, "bundle-sha256")
                    note_sha256_remote = _meta_get(meta, "note-sha256")
                    transcript_sha256_remote = _meta_get(meta, "transcript-sha256")
            except Exception:
                minio_bundle_exists = None

        if minio_tombstone_exists is True:
            # Tombstone means the remote item is deleted. If we still have local files,
            # present it as LOCAL_ONLY (remote already removed) so users can "入库" again.
            if has_local:
                status = "LOCAL_ONLY"
                remote_has_note = False
                remote_has_transcript = False
                remote_note = None
                remote_transcript = None
            else:
                status = "DELETED"
        elif (not has_local) and has_remote and minio_bundle_exists is False:
            status = "DIFY_ONLY_NO_BUNDLE"
        elif status == "SYNCED":
            # Detect real conflicts only when we can compare hashes.
            mismatch = False
            if remote_has_note and local_has_note and note_sha256_local and note_sha256_remote and note_sha256_local != note_sha256_remote:
                mismatch = True
            if (
                remote_has_transcript
                and local_has_transcript
                and transcript_sha256_local
                and transcript_sha256_remote
                and transcript_sha256_local != transcript_sha256_remote
            ):
                mismatch = True
            if mismatch:
                status = "CONFLICT"

        merged.append(
            {
                "status": status,
                "title": title,
                "platform": platform,
                "video_id": video_id,
                "created_at_ms": int(created_at_ms) if isinstance(created_at_ms, int) else None,
                "source_key": source_key,
                "sync_id": sync_id,
                "local_task_id": local.task_id if local else None,
                "local_has_note": local_has_note if local else None,
                "local_has_transcript": local_has_transcript if local else None,
                "dify_note_document_id": (remote_note or {}).get("document_id"),
                "dify_note_name": (remote_note or {}).get("name"),
                "dify_transcript_document_id": (remote_transcript or {}).get("document_id"),
                "dify_transcript_name": (remote_transcript or {}).get("name"),
                "remote_has_note": remote_has_note,
                "remote_has_transcript": remote_has_transcript,
                "minio_bundle_exists": minio_bundle_exists,
                "minio_tombstone_exists": minio_tombstone_exists,
                "bundle_sha256_local": bundle_sha256_local,
                "bundle_sha256_remote": bundle_sha256_remote,
                "note_sha256_local": note_sha256_local,
                "note_sha256_remote": note_sha256_remote,
                "transcript_sha256_local": transcript_sha256_local,
                "transcript_sha256_remote": transcript_sha256_remote,
            }
        )

    # Add legacy remote docs (not fetchable)
    for d in legacy_remote:
        merged.append(
            {
                "status": "DIFY_ONLY_LEGACY",
                "title": d.get("title") or "",
                "platform": d.get("platform") or "",
                "video_id": d.get("video_id") or "",
                "created_at_ms": None,
                "source_key": None,
                "sync_id": None,
                "local_task_id": None,
                "dify_note_document_id": d.get("document_id") if d.get("kind") == "note" else None,
                "dify_note_name": d.get("name") if d.get("kind") == "note" else None,
                "dify_transcript_document_id": d.get("document_id") if d.get("kind") == "transcript" else None,
                "dify_transcript_name": d.get("name") if d.get("kind") == "transcript" else None,
            }
        )

    merged.sort(key=lambda x: int(x.get("created_at_ms") or 0), reverse=True)

    # Persist scan results for the active profile (best-effort).
    try:
        db = next(get_db())
        try:
            db.query(SyncItem).filter(SyncItem.profile == profile).delete()
            rows = []
            for it in merged:
                sk = str(it.get("source_key") or "").strip()
                sid = str(it.get("sync_id") or "").strip()
                if not sk or not sid:
                    continue
                rows.append(
                    SyncItem(
                        profile=profile,
                        source_key=sk,
                        sync_id=sid,
                        status=str(it.get("status") or ""),
                        title=str(it.get("title") or "") or None,
                        platform=str(it.get("platform") or "") or None,
                        video_id=str(it.get("video_id") or "") or None,
                        created_at_ms=it.get("created_at_ms") if isinstance(it.get("created_at_ms"), int) else None,
                        local_task_id=str(it.get("local_task_id") or "") or None,
                        local_has_note=it.get("local_has_note") if isinstance(it.get("local_has_note"), bool) else None,
                        local_has_transcript=it.get("local_has_transcript") if isinstance(it.get("local_has_transcript"), bool) else None,
                        dify_note_document_id=str(it.get("dify_note_document_id") or "") or None,
                        dify_note_name=str(it.get("dify_note_name") or "") or None,
                        dify_transcript_document_id=str(it.get("dify_transcript_document_id") or "") or None,
                        dify_transcript_name=str(it.get("dify_transcript_name") or "") or None,
                        remote_has_note=it.get("remote_has_note") if isinstance(it.get("remote_has_note"), bool) else None,
                        remote_has_transcript=it.get("remote_has_transcript") if isinstance(it.get("remote_has_transcript"), bool) else None,
                        minio_bundle_exists=it.get("minio_bundle_exists") if isinstance(it.get("minio_bundle_exists"), bool) else None,
                        minio_tombstone_exists=it.get("minio_tombstone_exists") if isinstance(it.get("minio_tombstone_exists"), bool) else None,
                        bundle_sha256_local=str(it.get("bundle_sha256_local") or "") or None,
                        bundle_sha256_remote=str(it.get("bundle_sha256_remote") or "") or None,
                        note_sha256_local=str(it.get("note_sha256_local") or "") or None,
                        note_sha256_remote=str(it.get("note_sha256_remote") or "") or None,
                        transcript_sha256_local=str(it.get("transcript_sha256_local") or "") or None,
                        transcript_sha256_remote=str(it.get("transcript_sha256_remote") or "") or None,
                    )
                )
            if rows:
                db.bulk_save_objects(rows)
            db.commit()
        except Exception:
            db.rollback()
        finally:
            db.close()
    except Exception:
        pass

    return R.success(
        data={
            "profile": profile,
            "dify_base_url": cfg.base_url,
            "note_dataset_id": note_dataset_id,
            "transcript_dataset_id": transcript_dataset_id,
            "minio_bucket": bucket,
            "items": [SyncScanItem.model_validate(x).model_dump() for x in merged],
        }
    )


@router.get("/sync/items")
def sync_items_cached():
    """
    Return last scanned items from local SQLite (no Dify/MinIO calls).
    Also merges current local files (NOTE_OUTPUT_DIR) to avoid stale local flags.
    """
    cfg = DifyConfig.from_env()
    profile = DifyConfigManager().get_active_profile()
    note_dataset_id = (cfg.note_dataset_id or cfg.dataset_id).strip()
    transcript_dataset_id = (cfg.transcript_dataset_id or cfg.dataset_id).strip()

    note_dir = note_output_dir()
    local_items = scan_local_notes(note_dir)
    local_by_source: dict[str, Any] = {i.source_key: i for i in local_items if i.source_key}

    bucket: str | None = None
    try:
        minio_cfg = MinioConfig.from_env()
        bucket = bucket_name_for_profile(profile, prefix=minio_cfg.bucket_prefix)
    except Exception:
        bucket = None

    def _local_flags(local) -> tuple[Optional[str], Optional[bool], Optional[bool]]:
        if not local:
            return None, None, None
        local_has_note = False
        local_has_transcript = False
        try:
            if local.markdown_path and local.markdown_path.exists() and local.markdown_path.stat().st_size > 0:
                local_has_note = True
        except Exception:
            local_has_note = False
        try:
            if local.transcript_path and local.transcript_path.exists() and local.transcript_path.stat().st_size > 0:
                local_has_transcript = True
        except Exception:
            local_has_transcript = False
        if (not local_has_note or not local_has_transcript) and local.result_path and local.result_path.exists():
            res = _read_json(local.result_path) or {}
            if not local_has_note:
                md = res.get("markdown")
                local_has_note = isinstance(md, str) and bool(md.strip())
            if not local_has_transcript:
                local_has_transcript = isinstance(res.get("transcript"), dict)
        return local.task_id, local_has_note, local_has_transcript

    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    last_scanned_at: datetime | None = None

    try:
        db = next(get_db())
    except Exception:
        db = None

    try:
        rows = db.query(SyncItem).filter(SyncItem.profile == profile).all() if db is not None else []
        for row in rows:
            sk = str(getattr(row, "source_key", "") or "").strip()
            if not sk:
                continue
            seen.add(sk)
            sid = str(getattr(row, "sync_id", "") or "").strip() or compute_sync_id(sk)
            local = local_by_source.get(sk)
            local_task_id, local_has_note, local_has_transcript = _local_flags(local)

            dt = getattr(row, "updated_at", None)
            if isinstance(dt, datetime):
                last_scanned_at = dt if last_scanned_at is None or dt > last_scanned_at else last_scanned_at

            base_status = str(getattr(row, "status", "") or "").strip()
            remote_has_note = getattr(row, "remote_has_note", None)
            if not isinstance(remote_has_note, bool):
                remote_has_note = bool(str(getattr(row, "dify_note_document_id", "") or "").strip())
            remote_has_transcript = getattr(row, "remote_has_transcript", None)
            if not isinstance(remote_has_transcript, bool):
                remote_has_transcript = bool(str(getattr(row, "dify_transcript_document_id", "") or "").strip())

            status = base_status
            tombstone = getattr(row, "minio_tombstone_exists", None)
            if tombstone is True:
                if local_task_id:
                    status = "LOCAL_ONLY"
                    remote_has_note = False
                    remote_has_transcript = False
                else:
                    status = "DELETED"
            elif str(base_status or "").upper() not in {"CONFLICT", "DELETED"}:
                has_local = bool(local_task_id)
                has_remote = bool(remote_has_note or remote_has_transcript)
                if has_local and has_remote:
                    if bool(local_has_note) == bool(remote_has_note) and bool(local_has_transcript) == bool(remote_has_transcript):
                        status = "SYNCED"
                    else:
                        status = "PARTIAL"
                elif has_local and not has_remote:
                    status = "LOCAL_ONLY"

            dify_note_document_id = getattr(row, "dify_note_document_id", None)
            dify_note_name = getattr(row, "dify_note_name", None)
            dify_transcript_document_id = getattr(row, "dify_transcript_document_id", None)
            dify_transcript_name = getattr(row, "dify_transcript_name", None)
            if tombstone is True and local_task_id:
                dify_note_document_id = None
                dify_note_name = None
                dify_transcript_document_id = None
                dify_transcript_name = None

            merged.append(
                {
                    "status": status,
                    "title": str(getattr(row, "title", "") or "") or (local.title if local else ""),
                    "platform": str(getattr(row, "platform", "") or "") or (local.platform if local else ""),
                    "video_id": str(getattr(row, "video_id", "") or "") or (local.video_id if local else ""),
                    "created_at_ms": getattr(row, "created_at_ms", None) if isinstance(getattr(row, "created_at_ms", None), int) else (local.created_at_ms if local else None),
                    "source_key": sk,
                    "sync_id": sid,
                    "local_task_id": local_task_id,
                    "local_has_note": local_has_note,
                    "local_has_transcript": local_has_transcript,
                    "dify_note_document_id": dify_note_document_id,
                    "dify_note_name": dify_note_name,
                    "dify_transcript_document_id": dify_transcript_document_id,
                    "dify_transcript_name": dify_transcript_name,
                    "remote_has_note": remote_has_note,
                    "remote_has_transcript": remote_has_transcript,
                    "minio_bundle_exists": getattr(row, "minio_bundle_exists", None),
                    "minio_tombstone_exists": getattr(row, "minio_tombstone_exists", None),
                    "bundle_sha256_local": getattr(row, "bundle_sha256_local", None),
                    "bundle_sha256_remote": getattr(row, "bundle_sha256_remote", None),
                    "note_sha256_local": getattr(row, "note_sha256_local", None),
                    "note_sha256_remote": getattr(row, "note_sha256_remote", None),
                    "transcript_sha256_local": getattr(row, "transcript_sha256_local", None),
                    "transcript_sha256_remote": getattr(row, "transcript_sha256_remote", None),
                }
            )
    finally:
        try:
            if db is not None:
                db.close()
        except Exception:
            pass

    # Add new local items not seen in last scan.
    for sk, local in local_by_source.items():
        if sk in seen:
            continue
        local_task_id, local_has_note, local_has_transcript = _local_flags(local)
        merged.append(
            {
                "status": "LOCAL_ONLY",
                "title": local.title,
                "platform": local.platform,
                "video_id": local.video_id,
                "created_at_ms": local.created_at_ms,
                "source_key": sk,
                "sync_id": local.sync_id,
                "local_task_id": local_task_id,
                "local_has_note": local_has_note,
                "local_has_transcript": local_has_transcript,
                "remote_has_note": False,
                "remote_has_transcript": False,
                "minio_bundle_exists": None,
                "minio_tombstone_exists": None,
            }
        )

    merged.sort(key=lambda x: int(x.get("created_at_ms") or 0), reverse=True)

    return R.success(
        data={
            "profile": profile,
            "dify_base_url": cfg.base_url,
            "note_dataset_id": note_dataset_id,
            "transcript_dataset_id": transcript_dataset_id,
            "minio_bucket": bucket,
            "last_scanned_at": _iso_utc(last_scanned_at),
            "items": [SyncScanItem.model_validate(x).model_dump() for x in merged],
        }
    )


class SyncPushRequest(BaseModel):
    item_id: str
    include_transcript: bool = True
    include_note: bool = True
    update_dify: bool = True

    @field_validator("item_id", mode="before")
    @classmethod
    def _strip_item_id(cls, v):
        return str(v).strip() if v is not None else ""


@router.post("/sync/push")
def sync_push(data: SyncPushRequest):
    item_id = (data.item_id or "").strip()
    if not item_id:
        return R.error(msg="Missing item_id", code=400)

    cfg = DifyConfig.from_env()
    profile = DifyConfigManager().get_active_profile()
    note_dataset_id = (cfg.note_dataset_id or cfg.dataset_id).strip()
    transcript_dataset_id = (cfg.transcript_dataset_id or cfg.dataset_id).strip()

    note_dir = note_output_dir()
    local = load_local_note_item(note_dir, item_id)
    if not local:
        return R.error(msg=f"Local item not found: {item_id}", code=404)

    audio_json, transcript_json, note_markdown = _read_local_payloads(local)
    if not isinstance(audio_json, dict):
        return R.error(msg="Missing local audio metadata", code=400)

    if data.include_note and not (note_markdown or "").strip():
        return R.error(msg="Missing local note markdown", code=400)
    if data.include_transcript and not isinstance(transcript_json, dict):
        return R.error(msg="Missing local transcript", code=400)

    source_key = local.source_key
    sync_id = local.sync_id

    # Upload bundle to MinIO (source of truth).
    try:
        storage, bucket = _sync_bucket(profile)
    except MinioConfigError as exc:
        return R.error(msg=str(exc), code=500)

    object_key = _bundle_object_key(storage, sync_id=sync_id)
    request_meta = _read_local_request_meta(local)
    extra_meta = {"request": request_meta} if isinstance(request_meta, dict) and request_meta else None
    bundle = build_bundle_zip(
        source_key=source_key,
        sync_id=sync_id,
        audio=audio_json,
        note_markdown=note_markdown if data.include_note else None,
        transcript=transcript_json if data.include_transcript else None,
        extra_meta=extra_meta,
    )

    bundle_sha256 = hashlib.sha256(bundle).hexdigest()
    note_sha256: Optional[str] = None
    transcript_sha256: Optional[str] = None
    try:
        with zipfile.ZipFile(io.BytesIO(bundle)) as zf:
            meta_raw = zf.read("meta.json").decode("utf-8", errors="replace") if "meta.json" in zf.namelist() else "{}"
        meta_json = json.loads(meta_raw) if meta_raw.strip() else {}
        if isinstance(meta_json, dict) and isinstance(meta_json.get("content_sha256"), dict):
            hashes = meta_json.get("content_sha256") or {}
            note_sha256 = str(hashes.get("note_md") or "").strip() or None
            transcript_sha256 = str(hashes.get("transcript_json") or "").strip() or None
    except Exception:
        note_sha256 = None
        transcript_sha256 = None

    metadata = {
        "bundle-sha256": bundle_sha256,
        "sync-id": sync_id,
        "source-key": source_key,
    }
    if note_sha256:
        metadata["note-sha256"] = note_sha256
    if transcript_sha256:
        metadata["transcript-sha256"] = transcript_sha256

    try:
        # If a tombstone exists, treat this as a restore and remove it.
        tomb_key = _tombstone_object_key(storage, sync_id=sync_id)
        try:
            if storage.stat(bucket=bucket, object_key=tomb_key) is not None:
                storage.remove_object(bucket=bucket, object_key=tomb_key)
        except Exception:
            pass

        # Idempotent upload: skip when hash matches.
        existing = storage.stat(bucket=bucket, object_key=object_key) or {}
        existing_meta = existing.get("metadata") if isinstance(existing, dict) else None
        existing_sha = None
        if isinstance(existing_meta, dict):
            for k, v in existing_meta.items():
                if str(k).strip().lower() in {"x-amz-meta-bundle-sha256", "bundle-sha256"}:
                    if isinstance(v, (list, tuple)) and v:
                        v = v[0]
                    existing_sha = str(v).strip() or None
                    break

        if existing_sha != bundle_sha256:
            storage.put_bytes(bucket=bucket, object_key=object_key, data=bundle, content_type="application/zip", metadata=metadata)
    except Exception as exc:
        return R.error(msg=str(exc), code=500)

    if not bool(data.update_dify):
        return R.success(
            data={
                "source_key": source_key,
                "sync_id": sync_id,
                "minio": {"bucket": bucket, "object_key": object_key, "bundle_sha256": bundle_sha256},
                "dify": {"note": None, "transcript": None},
                "dify_error": None,
            }
        )

    # Upsert Dify documents (for RAG); use doc name matching for idempotency.
    dify_info: dict[str, Any] = {"note": None, "transcript": None}
    dify_errors: dict[str, str] = {}

    if not cfg.service_api_key:
        dify_errors["dify"] = "Missing DIFY_SERVICE_API_KEY"
        return R.success(
            data={
                "source_key": source_key,
                "sync_id": sync_id,
                "minio": {"bucket": bucket, "object_key": object_key, "bundle_sha256": bundle_sha256},
                "dify": dify_info,
                "dify_error": json.dumps(dify_errors, ensure_ascii=False),
            }
        )

    audio_obj = audio_from_json(audio_json)
    base_name = build_rag_document_name(audio_obj, local.platform, created_at_ms=local.created_at_ms)
    client = DifyKnowledgeClient(cfg)
    try:
        if data.include_note and note_dataset_id:
            try:
                doc_name = f"{base_name} (note)"
                text = build_rag_note_document_text(
                    audio=audio_obj,
                    platform=local.platform,
                    source_url="",
                    note_markdown=note_markdown,
                )
                existing = _find_document_by_name(client, dataset_id=note_dataset_id, name=doc_name)
                if existing and (existing.get("id") or existing.get("document_id")):
                    resp = client.update_document_by_text(
                        dataset_id=note_dataset_id,
                        document_id=str(existing.get("id") or existing.get("document_id")),
                        name=doc_name,
                        text=text,
                        doc_language="Chinese Simplified",
                    )
                else:
                    resp = client.create_document_by_text(
                        dataset_id=note_dataset_id,
                        name=doc_name,
                        text=text,
                        doc_language="Chinese Simplified",
                    )
                doc = resp.get("document") or {}
                dify_info["note"] = {
                    "dataset_id": note_dataset_id,
                    "document_id": doc.get("id"),
                    "batch": resp.get("batch"),
                    "name": doc_name,
                }
            except DifyError as exc:
                dify_errors["note"] = str(exc)

        if data.include_transcript and transcript_dataset_id:
            try:
                doc_name = f"{base_name} (transcript)"
                transcript_obj = transcript_from_json(transcript_json)
                text = build_rag_document_text(
                    audio=audio_obj,
                    transcript=transcript_obj,
                    platform=local.platform,
                    source_url="",
                )
                existing = _find_document_by_name(client, dataset_id=transcript_dataset_id, name=doc_name)
                if existing and (existing.get("id") or existing.get("document_id")):
                    resp = client.update_document_by_text(
                        dataset_id=transcript_dataset_id,
                        document_id=str(existing.get("id") or existing.get("document_id")),
                        name=doc_name,
                        text=text,
                        doc_language="Chinese Simplified",
                    )
                else:
                    resp = client.create_document_by_text(
                        dataset_id=transcript_dataset_id,
                        name=doc_name,
                        text=text,
                        doc_language="Chinese Simplified",
                    )
                doc = resp.get("document") or {}
                dify_info["transcript"] = {
                    "dataset_id": transcript_dataset_id,
                    "document_id": doc.get("id"),
                    "batch": resp.get("batch"),
                    "name": doc_name,
                }
            except DifyError as exc:
                dify_errors["transcript"] = str(exc)
    finally:
        client.close()

    dify_error = json.dumps(dify_errors, ensure_ascii=False) if dify_errors else None
    return R.success(
        data={
            "source_key": source_key,
            "sync_id": sync_id,
            "minio": {"bucket": bucket, "object_key": object_key, "bundle_sha256": bundle_sha256},
            "dify": dify_info,
            "dify_error": dify_error,
        }
    )


class SyncPullRequest(BaseModel):
    source_key: str
    overwrite: bool = False

    @field_validator("source_key", mode="before")
    @classmethod
    def _strip_source_key(cls, v):
        return str(v).strip() if v is not None else ""


@router.post("/sync/pull")
def sync_pull(data: SyncPullRequest):
    source_key = (data.source_key or "").strip()
    if not source_key:
        return R.error(msg="Missing source_key", code=400)

    sync_id = compute_sync_id(source_key)
    cfg = DifyConfig.from_env()
    profile = DifyConfigManager().get_active_profile()

    try:
        storage, bucket = _sync_bucket(profile)
    except MinioConfigError as exc:
        return R.error(msg=str(exc), code=500)

    object_key = _bundle_object_key(storage, sync_id=sync_id)
    # If tombstone exists, block pull to avoid resurrecting deleted items by accident.
    try:
        if storage.stat(bucket=bucket, object_key=_tombstone_object_key(storage, sync_id=sync_id)) is not None:
            return R.error(msg="Remote item is deleted (tombstone)", code=410)
    except Exception:
        pass

    remote_bundle_sha256: Optional[str] = None
    try:
        st = storage.stat(bucket=bucket, object_key=object_key) or {}
        meta = st.get("metadata") if isinstance(st, dict) else None
        if isinstance(meta, dict):
            for k, v in meta.items():
                if str(k).strip().lower() in {"x-amz-meta-bundle-sha256", "bundle-sha256"}:
                    if isinstance(v, (list, tuple)) and v:
                        v = v[0]
                    remote_bundle_sha256 = str(v).strip() or None
                    break
    except Exception:
        remote_bundle_sha256 = None

    try:
        bundle_bytes = storage.get_bytes(bucket=bucket, object_key=object_key)
    except Exception as exc:
        return R.error(msg=str(exc), code=500)

    if remote_bundle_sha256:
        actual = hashlib.sha256(bundle_bytes).hexdigest()
        if actual != remote_bundle_sha256:
            return R.error(msg="Bundle sha256 mismatch (download corrupted)", code=500)

    note_dir = note_output_dir()

    # Avoid creating duplicate local items: if we already have this source_key locally (even with a UUID task_id),
    # write into that existing task directory by default.
    existing_local = None
    try:
        for it in scan_local_notes(note_dir):
            if it.source_key == source_key:
                existing_local = it
                break
    except Exception:
        existing_local = None

    task_id = existing_local.task_id if existing_local else sync_id
    task_dir = note_dir / task_id

    try:
        with zipfile.ZipFile(io.BytesIO(bundle_bytes)) as zf:
            meta_raw = zf.read("meta.json").decode("utf-8", errors="replace") if "meta.json" in zf.namelist() else "{}"
            note_md = zf.read("note.md").decode("utf-8", errors="replace") if "note.md" in zf.namelist() else ""
            transcript_raw = zf.read("transcript.json").decode("utf-8", errors="replace") if "transcript.json" in zf.namelist() else "{}"
            audio_raw = zf.read("audio.json").decode("utf-8", errors="replace") if "audio.json" in zf.namelist() else "{}"

        meta_json = json.loads(meta_raw) if meta_raw.strip() else {}
        transcript_json = json.loads(transcript_raw) if transcript_raw.strip() else {}
        audio_json = json.loads(audio_raw) if audio_raw.strip() else {}
    except Exception as exc:
        return R.error(msg=f"Invalid bundle zip: {exc}", code=500)

    request_meta: dict[str, Any] | None = None
    if isinstance(meta_json, dict):
        bundle_source_key = str(meta_json.get("source_key") or "").strip()
        bundle_sync_id = str(meta_json.get("sync_id") or "").strip()
        if bundle_sync_id and bundle_sync_id != sync_id:
            return R.error(msg="Bundle sync_id mismatch", code=500)
        if bundle_source_key and bundle_source_key != source_key:
            return R.error(msg="Bundle source_key mismatch", code=400)
        if isinstance(meta_json.get("request"), dict):
            request_meta = meta_json.get("request") or {}

    # Parse source_key => stable created_at_ms for local meta.
    created_at_ms: Optional[int] = None
    platform = ""
    video_id = ""
    try:
        parts = [p.strip() for p in source_key.split(":")]
        if len(parts) >= 3 and parts[-1].isdigit():
            created_at_ms = int(parts[-1])
            platform = parts[0]
            video_id = parts[1]
    except Exception:
        created_at_ms = None

    if not platform or not video_id:
        # Fall back to audio.json if source_key is unexpected.
        if isinstance(audio_json, dict):
            platform = str(audio_json.get("platform") or "").strip() or platform
            video_id = str(audio_json.get("video_id") or "").strip() or video_id

    if created_at_ms is None or not platform or not video_id:
        return R.error(msg="Invalid source_key (expected platform:video_id:created_at_ms)", code=400)

    if isinstance(audio_json, dict):
        if platform and not str(audio_json.get("platform") or "").strip():
            audio_json["platform"] = platform
        if video_id and not str(audio_json.get("video_id") or "").strip():
            audio_json["video_id"] = video_id

    task_dir.mkdir(parents=True, exist_ok=True)

    md_path = task_dir / f"{task_id}_markdown.md"
    transcript_path = task_dir / f"{task_id}_transcript.json"
    audio_path = task_dir / f"{task_id}_audio.json"
    result_path = task_dir / f"{task_id}.json"
    status_path = task_dir / f"{task_id}.status.json"

    def _should_write(path: Path) -> bool:
        if data.overwrite:
            return True
        try:
            return not (path.exists() and path.stat().st_size > 0)
        except Exception:
            return True

    wrote_any = False

    if note_md.strip() and _should_write(md_path):
        md_path.write_text(note_md, encoding="utf-8")
        wrote_any = True

    if transcript_json and _should_write(transcript_path):
        transcript_path.write_text(json.dumps(transcript_json, ensure_ascii=False, indent=2), encoding="utf-8")
        wrote_any = True

    if audio_json and _should_write(audio_path):
        audio_path.write_text(json.dumps(audio_json, ensure_ascii=False, indent=2), encoding="utf-8")
        wrote_any = True

    try:
        ensure_local_sync_meta(
            note_dir=note_dir,
            task_id=task_id,
            platform=platform,
            video_id=video_id,
            title=str(audio_json.get("title") or "").strip() if isinstance(audio_json, dict) else "",
            prefer_created_at_ms=created_at_ms,
        )
    except Exception:
        pass

    result = {
        "markdown": note_md,
        "transcript": transcript_json,
        "audio_meta": audio_json,
        "request": request_meta if isinstance(request_meta, dict) else None,
        "sync": {"source_key": source_key, "sync_id": sync_id, "created_at_ms": created_at_ms},
        "dify": {"base_url": cfg.base_url},
    }
    if _should_write(result_path):
        result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        wrote_any = True

    status = {
        "status": "SUCCESS",
        "progress": 100,
        "message": "",
        "request": request_meta if isinstance(request_meta, dict) else None,
        "sync": {"source_key": source_key, "sync_id": sync_id, "created_at_ms": created_at_ms},
        "dify": {"base_url": cfg.base_url},
        "dify_error": None,
        "dify_indexing": None,
    }
    if _should_write(status_path):
        status_path.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
        wrote_any = True

    if not wrote_any and not data.overwrite:
        return R.error(msg="Local item already exists (set overwrite=true)", code=409)

    return R.success(
        data={
            "task_id": task_id,
            "source_key": source_key,
            "sync_id": sync_id,
            "minio": {"bucket": bucket, "object_key": object_key},
        }
    )


class SyncCopyRequest(BaseModel):
    source_key: str
    from_side: str = "local"  # "local" | "remote"
    create_dify_docs: bool = True
    include_transcript: bool = True
    include_note: bool = True
    new_created_at_ms: Optional[int] = None

    @field_validator("source_key", mode="before")
    @classmethod
    def _strip_source_key(cls, v):
        return str(v).strip() if v is not None else ""

    @field_validator("from_side", mode="before")
    @classmethod
    def _strip_from_side(cls, v):
        return str(v).strip().lower() if v is not None else "local"


@router.post("/sync/copy")
def sync_copy(data: SyncCopyRequest):
    source_key = (data.source_key or "").strip()
    if not source_key:
        return R.error(msg="Missing source_key", code=400)

    parts = [p.strip() for p in source_key.split(":")]
    if len(parts) < 3 or not parts[-1].isdigit():
        return R.error(msg="Invalid source_key (expected platform:video_id:created_at_ms)", code=400)
    platform = parts[0]
    video_id = parts[1]
    if not platform or not video_id:
        return R.error(msg="Invalid source_key (expected platform:video_id:created_at_ms)", code=400)

    from_side = (data.from_side or "local").strip().lower()
    if from_side not in {"local", "remote"}:
        return R.error(msg="Invalid from_side (expected local|remote)", code=400)

    cfg = DifyConfig.from_env()
    profile = DifyConfigManager().get_active_profile()
    note_dataset_id = (cfg.note_dataset_id or cfg.dataset_id).strip()
    transcript_dataset_id = (cfg.transcript_dataset_id or cfg.dataset_id).strip()

    note_dir = note_output_dir()

    audio_json: dict[str, Any] | None = None
    transcript_json: dict[str, Any] | None = None
    note_markdown = ""
    request_meta: dict[str, Any] | None = None

    if from_side == "local":
        local_item = None
        try:
            for it in scan_local_notes(note_dir):
                if it.source_key == source_key:
                    local_item = it
                    break
        except Exception:
            local_item = None

        if not local_item:
            return R.error(msg="Local item not found for source_key", code=404)

        audio_json, transcript_json, note_markdown = _read_local_payloads(local_item)
        request_meta = _read_local_request_meta(local_item)
        if not isinstance(audio_json, dict):
            return R.error(msg="Missing local audio metadata", code=400)
    else:
        sync_id = compute_sync_id(source_key)
        try:
            storage, bucket = _sync_bucket(profile)
        except MinioConfigError as exc:
            return R.error(msg=str(exc), code=500)

        object_key = _bundle_object_key(storage, sync_id=sync_id)
        try:
            bundle_bytes = storage.get_bytes(bucket=bucket, object_key=object_key)
        except Exception as exc:
            return R.error(msg=str(exc), code=500)

        try:
            with zipfile.ZipFile(io.BytesIO(bundle_bytes)) as zf:
                meta_raw = zf.read("meta.json").decode("utf-8", errors="replace") if "meta.json" in zf.namelist() else "{}"
                note_markdown = zf.read("note.md").decode("utf-8", errors="replace") if "note.md" in zf.namelist() else ""
                transcript_raw = zf.read("transcript.json").decode("utf-8", errors="replace") if "transcript.json" in zf.namelist() else "{}"
                audio_raw = zf.read("audio.json").decode("utf-8", errors="replace") if "audio.json" in zf.namelist() else "{}"
            meta_json = json.loads(meta_raw) if meta_raw.strip() else {}
            if isinstance(meta_json, dict) and isinstance(meta_json.get("request"), dict):
                request_meta = meta_json.get("request") or {}
            transcript_json = json.loads(transcript_raw) if transcript_raw.strip() else {}
            audio_json = json.loads(audio_raw) if audio_raw.strip() else {}
        except Exception as exc:
            return R.error(msg=f"Invalid remote bundle zip: {exc}", code=500)

        if not isinstance(audio_json, dict):
            return R.error(msg="Missing remote audio metadata", code=500)

    if data.include_note and not (note_markdown or "").strip():
        return R.error(msg="Missing note markdown", code=400)
    if data.include_transcript and not isinstance(transcript_json, dict):
        return R.error(msg="Missing transcript", code=400)

    # Generate a new unique source_key via created_at_ms.
    created_at_ms = int(data.new_created_at_ms or 0)
    if created_at_ms <= 0:
        created_at_ms = int(time.time() * 1000)

    try:
        storage2, bucket2 = _sync_bucket(profile)
    except MinioConfigError as exc:
        return R.error(msg=str(exc), code=500)

    new_source_key = ""
    new_sync_id = ""
    for _ in range(20):
        new_source_key = make_source_key(platform=platform, video_id=video_id, created_at_ms=created_at_ms)
        new_sync_id = compute_sync_id(new_source_key)
        task_dir = note_dir / new_sync_id
        object_key = _bundle_object_key(storage2, sync_id=new_sync_id)
        exists_local = task_dir.exists()
        exists_remote = False
        try:
            exists_remote = storage2.stat(bucket=bucket2, object_key=object_key) is not None
        except Exception:
            exists_remote = False
        if not exists_local and not exists_remote:
            break
        created_at_ms += 1

    if not new_source_key or not new_sync_id:
        return R.error(msg="Failed to generate unique copy id", code=500)

    extra_meta = {"request": request_meta} if isinstance(request_meta, dict) and request_meta else None
    bundle = build_bundle_zip(
        source_key=new_source_key,
        sync_id=new_sync_id,
        audio=audio_json,
        note_markdown=note_markdown if data.include_note else None,
        transcript=transcript_json if data.include_transcript else None,
        extra_meta=extra_meta,
    )
    bundle_sha256 = hashlib.sha256(bundle).hexdigest()

    note_sha256: Optional[str] = None
    transcript_sha256: Optional[str] = None
    try:
        with zipfile.ZipFile(io.BytesIO(bundle)) as zf:
            meta_raw = zf.read("meta.json").decode("utf-8", errors="replace") if "meta.json" in zf.namelist() else "{}"
        meta_json = json.loads(meta_raw) if meta_raw.strip() else {}
        if isinstance(meta_json, dict) and isinstance(meta_json.get("content_sha256"), dict):
            hashes = meta_json.get("content_sha256") or {}
            note_sha256 = str(hashes.get("note_md") or "").strip() or None
            transcript_sha256 = str(hashes.get("transcript_json") or "").strip() or None
    except Exception:
        note_sha256 = None
        transcript_sha256 = None

    metadata = {
        "bundle-sha256": bundle_sha256,
        "sync-id": new_sync_id,
        "source-key": new_source_key,
    }
    if note_sha256:
        metadata["note-sha256"] = note_sha256
    if transcript_sha256:
        metadata["transcript-sha256"] = transcript_sha256

    object_key2 = _bundle_object_key(storage2, sync_id=new_sync_id)
    try:
        storage2.put_bytes(bucket=bucket2, object_key=object_key2, data=bundle, content_type="application/zip", metadata=metadata)
    except Exception as exc:
        return R.error(msg=str(exc), code=500)

    # Create local copy files for immediate use.
    task_dir2 = note_dir / new_sync_id
    task_dir2.mkdir(parents=True, exist_ok=True)
    if data.include_note:
        (task_dir2 / f"{new_sync_id}_markdown.md").write_text((note_markdown or "").lstrip("\ufeff"), encoding="utf-8")
    if data.include_transcript:
        (task_dir2 / f"{new_sync_id}_transcript.json").write_text(json.dumps(transcript_json or {}, ensure_ascii=False, indent=2), encoding="utf-8")
    (task_dir2 / f"{new_sync_id}_audio.json").write_text(json.dumps(audio_json or {}, ensure_ascii=False, indent=2), encoding="utf-8")

    try:
        ensure_local_sync_meta(
            note_dir=note_dir,
            task_id=new_sync_id,
            platform=platform,
            video_id=video_id,
            title=str(audio_json.get("title") or "").strip() if isinstance(audio_json, dict) else "",
            prefer_created_at_ms=created_at_ms,
        )
    except Exception:
        pass

    result = {
        "markdown": (note_markdown or "").lstrip("\ufeff") if data.include_note else "",
        "transcript": transcript_json if data.include_transcript else {},
        "audio_meta": audio_json,
        "request": request_meta if isinstance(request_meta, dict) else None,
        "sync": {"source_key": new_source_key, "sync_id": new_sync_id, "created_at_ms": created_at_ms},
        "dify": {"base_url": cfg.base_url},
    }
    (task_dir2 / f"{new_sync_id}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    status = {
        "status": "SUCCESS",
        "progress": 100,
        "message": "",
        "request": request_meta if isinstance(request_meta, dict) else None,
        "sync": {"source_key": new_source_key, "sync_id": new_sync_id, "created_at_ms": created_at_ms},
        "dify": {"base_url": cfg.base_url},
        "dify_error": None,
        "dify_indexing": None,
    }
    (task_dir2 / f"{new_sync_id}.status.json").write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")

    dify_info: dict[str, Any] = {"note": None, "transcript": None}
    dify_errors: dict[str, str] = {}
    if data.create_dify_docs and cfg.service_api_key and (note_dataset_id or transcript_dataset_id):
        audio_obj = audio_from_json(audio_json)
        base_name = build_rag_document_name(audio_obj, platform, created_at_ms=created_at_ms)
        client = DifyKnowledgeClient(cfg)
        try:
            if data.include_note and note_dataset_id:
                try:
                    doc_name = f"{base_name} (note)"
                    text = build_rag_note_document_text(audio=audio_obj, platform=platform, source_url="", note_markdown=note_markdown)
                    resp = client.create_document_by_text(dataset_id=note_dataset_id, name=doc_name, text=text, doc_language="Chinese Simplified")
                    doc = resp.get("document") or {}
                    dify_info["note"] = {"dataset_id": note_dataset_id, "document_id": doc.get("id"), "batch": resp.get("batch"), "name": doc_name}
                except DifyError as exc:
                    dify_errors["note"] = str(exc)

            if data.include_transcript and transcript_dataset_id:
                try:
                    doc_name = f"{base_name} (transcript)"
                    transcript_obj = transcript_from_json(transcript_json)
                    text = build_rag_document_text(audio=audio_obj, transcript=transcript_obj, platform=platform, source_url="")
                    resp = client.create_document_by_text(dataset_id=transcript_dataset_id, name=doc_name, text=text, doc_language="Chinese Simplified")
                    doc = resp.get("document") or {}
                    dify_info["transcript"] = {
                        "dataset_id": transcript_dataset_id,
                        "document_id": doc.get("id"),
                        "batch": resp.get("batch"),
                        "name": doc_name,
                    }
                except DifyError as exc:
                    dify_errors["transcript"] = str(exc)
        finally:
            client.close()

    dify_error = json.dumps(dify_errors, ensure_ascii=False) if dify_errors else None
    return R.success(
        data={
            "task_id": new_sync_id,
            "source_key": new_source_key,
            "sync_id": new_sync_id,
            "minio": {"bucket": bucket2, "object_key": object_key2, "bundle_sha256": bundle_sha256},
            "dify": dify_info,
            "dify_error": dify_error,
        }
    )


class SyncDeleteRemoteRequest(BaseModel):
    source_key: str
    delete_dify: bool = True
    dify_note_document_id: Optional[str] = None
    dify_transcript_document_id: Optional[str] = None

    @field_validator("source_key", mode="before")
    @classmethod
    def _strip_source_key(cls, v):
        return str(v).strip() if v is not None else ""

    @field_validator("dify_note_document_id", "dify_transcript_document_id", mode="before")
    @classmethod
    def _strip_optional_id(cls, v):
        return str(v).strip() if v is not None else None


@router.post("/sync/delete_remote")
def sync_delete_remote(data: SyncDeleteRemoteRequest):
    source_key = (data.source_key or "").strip()
    if not source_key:
        return R.error(msg="Missing source_key", code=400)

    sync_id = compute_sync_id(source_key)
    cfg = DifyConfig.from_env()
    profile = DifyConfigManager().get_active_profile()
    note_dataset_id = (cfg.note_dataset_id or cfg.dataset_id).strip()
    transcript_dataset_id = (cfg.transcript_dataset_id or cfg.dataset_id).strip()

    try:
        storage, bucket = _sync_bucket(profile)
    except MinioConfigError as exc:
        return R.error(msg=str(exc), code=500)

    tomb_key = _tombstone_object_key(storage, sync_id=sync_id)
    tombstone = {
        "version": 1,
        "source_key": source_key,
        "sync_id": sync_id,
        "deleted_at_ms": int(time.time() * 1000),
        "profile": profile,
    }
    try:
        storage.put_bytes(bucket=bucket, object_key=tomb_key, data=json.dumps(tombstone, ensure_ascii=False, indent=2).encode("utf-8"), content_type="application/json")
    except Exception as exc:
        return R.error(msg=str(exc), code=500)

    dify_info: dict[str, Any] = {"note": None, "transcript": None}
    dify_errors: dict[str, str] = {}

    if data.delete_dify and cfg.service_api_key and (note_dataset_id or transcript_dataset_id):
        client = DifyKnowledgeClient(cfg)
        try:
            if data.dify_note_document_id and note_dataset_id:
                try:
                    client.delete_document(dataset_id=note_dataset_id, document_id=str(data.dify_note_document_id))
                    dify_info["note"] = {"dataset_id": note_dataset_id, "document_id": str(data.dify_note_document_id)}
                except DifyError as exc:
                    dify_errors["note"] = str(exc)

            if data.dify_transcript_document_id and transcript_dataset_id:
                try:
                    client.delete_document(dataset_id=transcript_dataset_id, document_id=str(data.dify_transcript_document_id))
                    dify_info["transcript"] = {
                        "dataset_id": transcript_dataset_id,
                        "document_id": str(data.dify_transcript_document_id),
                    }
                except DifyError as exc:
                    dify_errors["transcript"] = str(exc)
        finally:
            client.close()

    dify_error = json.dumps(dify_errors, ensure_ascii=False) if dify_errors else None
    return R.success(
        data={
            "source_key": source_key,
            "sync_id": sync_id,
            "minio": {"bucket": bucket, "tombstone_key": tomb_key},
            "dify": dify_info,
            "dify_error": dify_error,
        }
    )
