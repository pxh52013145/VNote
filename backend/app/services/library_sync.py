from __future__ import annotations

import hashlib
import io
import json
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from app.models.audio_model import AudioDownloadResult
from app.models.transcriber_model import TranscriptResult, TranscriptSegment


def compute_sync_id(source_key: str) -> str:
    return hashlib.sha256((source_key or "").encode("utf-8")).hexdigest()


def make_source_key(*, platform: str, video_id: str, created_at_ms: int) -> str:
    p = (platform or "").strip()
    vid = (video_id or "").strip()
    return f"{p}:{vid}:{int(created_at_ms)}"


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _coerce_int_ms(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    try:
        ms = int(value)
    except (TypeError, ValueError):
        return None
    return ms if ms > 0 else None


def _parse_created_at_ms_from_source_key(source_key: str) -> Optional[int]:
    raw = str(source_key or "").strip()
    if not raw:
        return None
    parts = [p.strip() for p in raw.split(":") if p is not None]
    if len(parts) < 3:
        return None
    tail = parts[-1]
    if not tail.isdigit():
        return None
    try:
        ms = int(tail)
    except ValueError:
        return None
    return ms if ms > 0 else None


def _read_prefer_created_at_ms(paths: dict[str, Optional[Path]]) -> Optional[int]:
    """
    Prefer the created_at_ms persisted inside result/status JSON ("sync.created_at_ms").

    This value is authoritative and should be stable even when file mtimes change.
    """
    for p in (paths.get("result_path"), paths.get("status_path")):
        if not isinstance(p, Path) or not p.exists():
            continue
        payload = _read_json(p)
        if not isinstance(payload, dict):
            continue
        sync = payload.get("sync") if isinstance(payload.get("sync"), dict) else None
        if not isinstance(sync, dict):
            continue
        ms = _coerce_int_ms(sync.get("created_at_ms"))
        if ms:
            return ms
        ms2 = _parse_created_at_ms_from_source_key(str(sync.get("source_key") or ""))
        if ms2:
            return ms2
    return None


def _safe_mtime_ms(path: Path) -> int:
    try:
        return int(path.stat().st_mtime * 1000)
    except Exception:
        return int(time.time() * 1000)


def _sync_meta_path(note_dir: Path, task_id: str) -> Path:
    tid = (task_id or "").strip()
    task_dir = note_dir / tid
    if task_dir.is_dir():
        return task_dir / f"{tid}.sync.json"
    return note_dir / f"{tid}.sync.json"


def ensure_local_sync_meta(
    *,
    note_dir: Path,
    task_id: str,
    platform: str,
    video_id: str,
    title: str,
    prefer_created_at_ms: Optional[int] = None,
) -> dict[str, Any]:
    """
    Persist a small sync meta file so `created_at_ms/source_key/sync_id` stay stable even if
    the main result/status files are touched.
    """
    meta_path = _sync_meta_path(note_dir, task_id)
    prefer_ms = _coerce_int_ms(prefer_created_at_ms)
    existing = _read_json(meta_path)
    if isinstance(existing, dict):
        existing_source_key = str(existing.get("source_key") or "").strip()
        existing_sync_id = str(existing.get("sync_id") or "").strip()
        existing_created_at_ms = _coerce_int_ms(existing.get("created_at_ms"))
        existing_platform = str(existing.get("platform") or "").strip()
        existing_video_id = str(existing.get("video_id") or "").strip()
        existing_title = str(existing.get("title") or "").strip()

        has_existing = bool(existing_source_key and existing_sync_id and existing_created_at_ms)
        if has_existing and not prefer_ms:
            return existing

        if has_existing and prefer_ms:
            expected_source_key = make_source_key(platform=platform, video_id=video_id, created_at_ms=prefer_ms)
            expected_sync_id = compute_sync_id(expected_source_key)
            if (
                existing_created_at_ms == prefer_ms
                and existing_source_key == expected_source_key
                and existing_sync_id == expected_sync_id
                and existing_platform == platform
                and existing_video_id == video_id
                and existing_title == title
            ):
                return existing

    created_at_ms = int(prefer_ms or 0)
    if created_at_ms <= 0:
        # Pick the earliest reasonable mtime as the "created" timestamp.
        candidates = []
        task_dir = note_dir / str(task_id).strip()
        if task_dir.is_dir():
            candidates.extend(
                [
                    task_dir / f"{task_id}.status.json",
                    task_dir / f"{task_id}.json",
                    task_dir / f"{task_id}_markdown.md",
                ]
            )
        else:
            candidates.extend(
                [
                    note_dir / f"{task_id}.status.json",
                    note_dir / f"{task_id}.json",
                    note_dir / f"{task_id}_markdown.md",
                ]
            )

        mtimes = [int(p.stat().st_mtime * 1000) for p in candidates if p.exists()]
        created_at_ms = min(mtimes) if mtimes else int(time.time() * 1000)

    source_key = make_source_key(platform=platform, video_id=video_id, created_at_ms=created_at_ms)
    sync_id = compute_sync_id(source_key)

    meta = {
        "version": 1,
        "task_id": str(task_id),
        "title": title,
        "platform": platform,
        "video_id": video_id,
        "created_at_ms": created_at_ms,
        "source_key": source_key,
        "sync_id": sync_id,
    }
    _atomic_write_json(meta_path, meta)
    return meta


@dataclass(frozen=True)
class LocalNoteItem:
    task_id: str
    title: str
    platform: str
    video_id: str
    created_at_ms: int
    source_key: str
    sync_id: str
    task_dir: Path
    markdown_path: Optional[Path]
    transcript_path: Optional[Path]
    audio_path: Optional[Path]
    result_path: Optional[Path]
    status_path: Optional[Path]


def _resolve_local_paths(note_dir: Path, task_id: str) -> dict[str, Optional[Path]]:
    tid = (task_id or "").strip()
    task_dir = note_dir / tid
    if task_dir.is_dir():
        base = task_dir
    else:
        base = note_dir
    result_path = base / f"{tid}.json"
    status_path = base / f"{tid}.status.json"
    markdown_path = base / f"{tid}_markdown.md"
    transcript_path = base / f"{tid}_transcript.json"
    audio_path = base / f"{tid}_audio.json"

    return {
        "task_dir": task_dir if task_dir.is_dir() else base,
        "result_path": result_path if result_path.exists() else None,
        "status_path": status_path if status_path.exists() else None,
        "markdown_path": markdown_path if markdown_path.exists() else None,
        "transcript_path": transcript_path if transcript_path.exists() else None,
        "audio_path": audio_path if audio_path.exists() else None,
    }


def _parse_audio_meta(paths: dict[str, Optional[Path]]) -> tuple[str, str, str] | None:
    """
    Returns (title, platform, video_id).
    """
    audio_path = paths.get("audio_path")
    if isinstance(audio_path, Path) and audio_path.exists():
        audio = _read_json(audio_path) or {}
        title = str(audio.get("title") or "").strip()
        platform = str(audio.get("platform") or "").strip()
        video_id = str(audio.get("video_id") or "").strip()
        if platform and video_id:
            return title, platform, video_id

    result_path = paths.get("result_path")
    if isinstance(result_path, Path) and result_path.exists():
        res = _read_json(result_path) or {}
        audio = res.get("audio_meta") if isinstance(res.get("audio_meta"), dict) else {}
        title = str(audio.get("title") or "").strip()
        platform = str(audio.get("platform") or "").strip()
        video_id = str(audio.get("video_id") or "").strip()
        if platform and video_id:
            return title, platform, video_id

    return None


def scan_local_notes(note_dir: Path) -> list[LocalNoteItem]:
    if not note_dir.exists():
        return []

    task_ids: set[str] = set()

    # New format: note_dir/<task_id>/<task_id>.status.json
    try:
        for entry in note_dir.iterdir():
            if not entry.is_dir():
                continue
            tid = entry.name.strip()
            if not tid:
                continue
            if (entry / f"{tid}.status.json").exists() or (entry / f"{tid}.json").exists():
                task_ids.add(tid)
    except Exception:
        pass

    # Legacy format: note_dir/<task_id>.status.json
    try:
        for status_path in note_dir.glob("*.status.json"):
            tid = status_path.name[: -len(".status.json")].strip()
            if tid:
                task_ids.add(tid)
    except Exception:
        pass

    items: list[LocalNoteItem] = []
    for task_id in sorted(task_ids):
        paths = _resolve_local_paths(note_dir, task_id)
        meta = _parse_audio_meta(paths)
        if not meta:
            continue
        title, platform, video_id = meta

        prefer_created_at_ms = _read_prefer_created_at_ms(paths)
        sync_meta = ensure_local_sync_meta(
            note_dir=note_dir,
            task_id=task_id,
            platform=platform,
            video_id=video_id,
            title=title,
            prefer_created_at_ms=prefer_created_at_ms,
        )
        created_at_ms = int(sync_meta.get("created_at_ms") or 0)
        source_key = str(sync_meta.get("source_key") or "").strip()
        sync_id = str(sync_meta.get("sync_id") or "").strip()

        items.append(
            LocalNoteItem(
                task_id=task_id,
                title=title,
                platform=platform,
                video_id=video_id,
                created_at_ms=created_at_ms or _safe_mtime_ms(paths.get("status_path") or paths.get("result_path") or note_dir),
                source_key=source_key,
                sync_id=sync_id,
                task_dir=paths["task_dir"] or (note_dir / task_id),
                markdown_path=paths.get("markdown_path"),
                transcript_path=paths.get("transcript_path"),
                audio_path=paths.get("audio_path"),
                result_path=paths.get("result_path"),
                status_path=paths.get("status_path"),
            )
        )

    return items


def load_local_note_item(note_dir: Path, task_id: str) -> LocalNoteItem | None:
    tid = (task_id or "").strip()
    if not tid:
        return None
    paths = _resolve_local_paths(note_dir, tid)
    meta = _parse_audio_meta(paths)
    if not meta:
        return None
    title, platform, video_id = meta
    prefer_created_at_ms = _read_prefer_created_at_ms(paths)
    sync_meta = ensure_local_sync_meta(
        note_dir=note_dir,
        task_id=tid,
        platform=platform,
        video_id=video_id,
        title=title,
        prefer_created_at_ms=prefer_created_at_ms,
    )
    created_at_ms = int(sync_meta.get("created_at_ms") or 0) or _safe_mtime_ms(
        paths.get("status_path") or paths.get("result_path") or note_dir
    )
    source_key = str(sync_meta.get("source_key") or "").strip()
    sync_id = str(sync_meta.get("sync_id") or "").strip()
    return LocalNoteItem(
        task_id=tid,
        title=title,
        platform=platform,
        video_id=video_id,
        created_at_ms=created_at_ms,
        source_key=source_key,
        sync_id=sync_id,
        task_dir=paths["task_dir"] or (note_dir / tid),
        markdown_path=paths.get("markdown_path"),
        transcript_path=paths.get("transcript_path"),
        audio_path=paths.get("audio_path"),
        result_path=paths.get("result_path"),
        status_path=paths.get("status_path"),
    )


def parse_dify_sync_tag(name: str) -> tuple[str, str, str, Optional[int]] | None:
    """
    Parse Dify document name tail tag like:
      "<title> [platform:video_id]" or "<title> [platform:video_id:created_at_ms]"
    Returns (title, platform, video_id, created_at_ms|None).
    """
    n = (name or "").strip()
    right = n.rfind("]")
    if right < 0:
        return None
    left = n.rfind("[", 0, right)
    if left < 0:
        return None
    tag = n[left + 1 : right].strip()
    parts = [p.strip() for p in tag.split(":")]
    if len(parts) < 2:
        return None
    platform = parts[0]
    video_id = parts[1]
    if not platform or not video_id:
        return None

    created_at_ms: Optional[int] = None
    if len(parts) >= 3:
        raw = parts[2]
        if raw.isdigit():
            try:
                created_at_ms = int(raw)
            except ValueError:
                created_at_ms = None

    title = n[:left].strip()
    return title, platform, video_id, created_at_ms


@dataclass(frozen=True)
class RemoteDifyDoc:
    dataset_id: str
    document_id: str
    name: str


@dataclass(frozen=True)
class RemoteNoteItem:
    title: str
    platform: str
    video_id: str
    created_at_ms: Optional[int]
    source_key: Optional[str]
    sync_id: Optional[str]
    note: Optional[RemoteDifyDoc]
    transcript: Optional[RemoteDifyDoc]


def build_bundle_zip(
    *,
    source_key: str,
    sync_id: str,
    audio: dict[str, Any] | None,
    note_markdown: str | None,
    transcript: dict[str, Any] | None,
    extra_meta: dict[str, Any] | None = None,
) -> bytes:
    """
    Build a deterministic zip bundle so hashing/idempotency and cross-device equality checks are stable.

    Bundle layout:
    - meta.json
    - audio.json (optional)
    - transcript.json (optional)
    - transcript.srt (optional, derived)
    - note.md (optional)
    """

    def _sha256_hex(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    def _canonical_json_bytes(obj: Any) -> bytes:
        return json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")

    def _format_srt_timestamp(ms: int) -> str:
        ms = max(0, int(ms))
        hh = ms // 3_600_000
        mm = (ms % 3_600_000) // 60_000
        ss = (ms % 60_000) // 1_000
        mmm = ms % 1_000
        return f"{hh:02}:{mm:02}:{ss:02},{mmm:03}"

    def _transcript_to_srt(payload: dict[str, Any]) -> str:
        segments = payload.get("segments") if isinstance(payload.get("segments"), list) else []
        if not segments:
            full_text = str(payload.get("full_text") or "").strip()
            if not full_text:
                return ""
            return "1\n00:00:00,000 --> 00:00:00,000\n" + full_text + "\n"

        lines: list[str] = []
        idx = 1
        for seg in segments:
            if not isinstance(seg, dict):
                continue
            try:
                start_s = float(seg.get("start") or 0.0)
                end_s = float(seg.get("end") or start_s)
            except (TypeError, ValueError):
                continue
            text = str(seg.get("text") or "").strip()
            if not text:
                continue

            start_ms = int(start_s * 1000)
            end_ms = int(end_s * 1000)
            lines.append(str(idx))
            lines.append(f"{_format_srt_timestamp(start_ms)} --> {_format_srt_timestamp(end_ms)}")
            lines.append(text)
            lines.append("")
            idx += 1

        return "\n".join(lines).strip() + ("\n" if lines else "")

    note_text = (note_markdown or "").lstrip("\ufeff")
    note_bytes = note_text.encode("utf-8") if note_text.strip() else b""

    audio_bytes = _canonical_json_bytes(audio) if audio else b""
    transcript_bytes = _canonical_json_bytes(transcript) if transcript else b""
    srt_text = _transcript_to_srt(transcript or {}) if transcript else ""
    srt_bytes = srt_text.encode("utf-8") if srt_text.strip() else b""

    created_at_ms: Optional[int] = None
    try:
        parts = [p.strip() for p in (source_key or "").split(":")]
        if len(parts) >= 3 and parts[-1].isdigit():
            created_at_ms = int(parts[-1])
    except Exception:
        created_at_ms = None

    content_sha256: dict[str, str] = {}
    if note_bytes:
        content_sha256["note_md"] = _sha256_hex(note_bytes)
    if audio_bytes:
        content_sha256["audio_json"] = _sha256_hex(audio_bytes)
    if transcript_bytes:
        content_sha256["transcript_json"] = _sha256_hex(transcript_bytes)
    if srt_bytes:
        content_sha256["transcript_srt"] = _sha256_hex(srt_bytes)

    meta = {
        "version": 1,
        "source_key": source_key,
        "sync_id": sync_id,
        "created_at_ms": created_at_ms,
        "files": {
            "note_md": bool(note_bytes),
            "transcript_json": bool(transcript_bytes),
            "transcript_srt": bool(srt_bytes),
            "audio_json": bool(audio_bytes),
        },
        "content_sha256": content_sha256,
    }
    if isinstance(extra_meta, dict) and extra_meta:
        # Keep meta extensible for cross-device sync (e.g. store request parameters).
        meta.update(extra_meta)

    def _zipinfo(name: str) -> zipfile.ZipInfo:
        info = zipfile.ZipInfo(name)
        # 1980-01-01 is the earliest date supported by ZIP.
        info.date_time = (1980, 1, 1, 0, 0, 0)
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = 0o644 << 16
        return info

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w") as zf:
        zf.writestr(_zipinfo("meta.json"), _canonical_json_bytes(meta))
        if audio_bytes:
            zf.writestr(_zipinfo("audio.json"), audio_bytes)
        if transcript_bytes:
            zf.writestr(_zipinfo("transcript.json"), transcript_bytes)
        if srt_bytes:
            zf.writestr(_zipinfo("transcript.srt"), srt_bytes)
        if note_bytes:
            zf.writestr(_zipinfo("note.md"), note_bytes)

    return buf.getvalue()


def transcript_from_json(payload: dict[str, Any] | None) -> TranscriptResult:
    p = payload or {}
    language = p.get("language")
    full_text = str(p.get("full_text") or "")
    segments_raw = p.get("segments") if isinstance(p.get("segments"), list) else []
    segments: list[TranscriptSegment] = []
    for seg in segments_raw:
        if not isinstance(seg, dict):
            continue
        try:
            start = float(seg.get("start") or 0.0)
            end = float(seg.get("end") or start)
        except (TypeError, ValueError):
            continue
        text = str(seg.get("text") or "")
        segments.append(TranscriptSegment(start=start, end=end, text=text))

    return TranscriptResult(
        language=str(language) if language is not None else None,
        full_text=full_text,
        segments=segments,
        raw=p.get("raw") if isinstance(p.get("raw"), dict) else None,
    )


def audio_from_json(payload: dict[str, Any] | None) -> AudioDownloadResult:
    p = payload or {}
    return AudioDownloadResult(
        file_path=str(p.get("file_path") or ""),
        title=str(p.get("title") or ""),
        duration=float(p.get("duration") or 0.0),
        cover_url=str(p.get("cover_url") or "") or None,
        platform=str(p.get("platform") or ""),
        video_id=str(p.get("video_id") or ""),
        raw_info=p.get("raw_info") if isinstance(p.get("raw_info"), dict) else {},
        video_path=str(p.get("video_path") or "") or None,
    )
