import re
import os
from collections import defaultdict
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.models.audio_model import AudioDownloadResult
from app.models.transcriber_model import TranscriptResult


def _format_timestamp(seconds: float) -> str:
    total = int(max(0, seconds))
    hours = total // 3600
    minutes = (total % 3600) // 60
    secs = total % 60
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


_DROP_SOURCE_QUERY_KEYS = {
    "vd_source",
    "spm_id_from",
    "from",
    "share_source",
    "share_medium",
    "share_plat",
    "share_session_id",
    "share_tag",
}


def _normalize_source_url(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return ""

    try:
        parts = urlsplit(raw)
    except Exception:
        return raw

    if parts.scheme not in ("http", "https"):
        return raw

    pairs = []
    for k, v in parse_qsl(parts.query, keep_blank_values=True):
        if not k:
            continue
        if k in _DROP_SOURCE_QUERY_KEYS or k.lower().startswith("utm_"):
            continue
        pairs.append((k, v))

    query = urlencode(pairs, doseq=True)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, ""))


def _merge_transcript_segments_by_chars(
    segments: list[Any] | None,
    *,
    max_chars: int = 900,
    max_seconds: float = 60.0,
) -> list[tuple[float, float, str]]:
    """
    Dify indexing with Ollama embeddings may fail when a document is split into hundreds of tiny chunks.
    To reduce total chunks while preserving time ranges, merge consecutive transcript segments into
    larger blocks capped by `max_chars` characters and `max_seconds` duration (rough heuristic).
    """
    if not segments or max_chars <= 0:
        return []

    merged: list[tuple[float, float, str]] = []
    buf: list[str] = []
    buf_len = 0
    start_ts: float | None = None
    end_ts: float | None = None

    for seg in segments:
        text = getattr(seg, "text", None)
        if text is None:
            continue
        text = str(text).replace("\n", " ").strip()
        if not text:
            continue
        text = re.sub(r"\s+", " ", text).strip()

        seg_start = float(getattr(seg, "start", 0.0) or 0.0)
        seg_end = float(getattr(seg, "end", seg_start) or seg_start)

        extra = (1 if buf else 0) + len(text)
        span_ok = True
        if max_seconds and max_seconds > 0 and start_ts is not None:
            span_ok = (seg_end - float(start_ts)) <= float(max_seconds)

        if buf and ((buf_len + extra) > max_chars or not span_ok):
            merged.append((float(start_ts or 0.0), float(end_ts or float(start_ts or 0.0)), " ".join(buf)))
            buf = [text]
            buf_len = len(text)
            start_ts = seg_start
            end_ts = seg_end
            continue

        if not buf:
            start_ts = seg_start
        buf.append(text)
        buf_len += extra
        end_ts = seg_end

    if buf:
        merged.append((float(start_ts or 0.0), float(end_ts or float(start_ts or 0.0)), " ".join(buf)))

    return merged


def build_rag_document_name(audio: AudioDownloadResult, platform: str) -> str:
    safe_title = (audio.title or "").strip() or "Untitled"
    safe_video_id = (audio.video_id or "").strip() or "unknown"
    return f"{safe_title} [{platform}:{safe_video_id}]"


def build_rag_note_document_text(
    *,
    audio: AudioDownloadResult,
    platform: str,
    source_url: str,
    note_markdown: str,
) -> str:
    normalized_source = _normalize_source_url(source_url)
    header = [
        f"[TITLE]={audio.title}",
        f"[PLATFORM]={platform}",
        f"[VIDEO_ID]={audio.video_id}",
        f"[SOURCE]={normalized_source}",
        "",
    ]

    md = (note_markdown or "").strip()
    parts: list[str] = []
    parts.extend(header)
    if md:
        parts.append(md)
        parts.append("")
    return "\n".join(parts).strip() + "\n"


def build_rag_document_text(
    *,
    audio: AudioDownloadResult,
    transcript: TranscriptResult,
    platform: str,
    source_url: str,
) -> str:
    normalized_source = _normalize_source_url(source_url)
    header = [
        f"[TITLE]={audio.title}",
        f"[PLATFORM]={platform}",
        f"[VIDEO_ID]={audio.video_id}",
        f"[SOURCE]={normalized_source}",
        "",
    ]

    parts: list[str] = []
    parts.extend(header)

    max_chars = int(os.getenv("RAG_TRANSCRIPT_MERGE_MAX_CHARS", "900") or "900")
    max_seconds = float(os.getenv("RAG_TRANSCRIPT_MERGE_MAX_SECONDS", "60") or "60")
    merged = _merge_transcript_segments_by_chars(
        transcript.segments,
        max_chars=max_chars,
        max_seconds=max_seconds,
    )
    if merged:
        for start_s, end_s, text in merged:
            start = _format_timestamp(start_s)
            end = _format_timestamp(end_s)
            parts.append(f"[VID={audio.video_id}][PLATFORM={platform}][TIME={start}-{end}] {text}")
            parts.append("")
    else:
        for seg in transcript.segments or []:
            text = (seg.text or "").replace("\n", " ").strip()
            if not text:
                continue
            start = _format_timestamp(seg.start)
            end = _format_timestamp(seg.end)
            parts.append(f"[VID={audio.video_id}][PLATFORM={platform}][TIME={start}-{end}] {text}")
            parts.append("")

    return "\n".join(parts).strip() + "\n"


def build_rag_document_text_with_note(
    *,
    audio: AudioDownloadResult,
    transcript: TranscriptResult,
    platform: str,
    source_url: str,
    note_markdown: str,
) -> str:
    normalized_source = _normalize_source_url(source_url)
    header = [
        f"[TITLE]={audio.title}",
        f"[PLATFORM]={platform}",
        f"[VIDEO_ID]={audio.video_id}",
        f"[SOURCE]={normalized_source}",
        "",
    ]

    parts: list[str] = []
    parts.extend(header)

    md = (note_markdown or "").strip()
    if md:
        parts.append("[NOTE_MARKDOWN]")
        parts.append(md)
        parts.append("")

    parts.append("[TRANSCRIPT]")
    parts.append("")

    max_chars = int(os.getenv("RAG_TRANSCRIPT_MERGE_MAX_CHARS", "900") or "900")
    max_seconds = float(os.getenv("RAG_TRANSCRIPT_MERGE_MAX_SECONDS", "60") or "60")
    merged = _merge_transcript_segments_by_chars(
        transcript.segments,
        max_chars=max_chars,
        max_seconds=max_seconds,
    )
    if merged:
        for start_s, end_s, text in merged:
            start = _format_timestamp(start_s)
            end = _format_timestamp(end_s)
            parts.append(f"[VID={audio.video_id}][PLATFORM={platform}][TIME={start}-{end}] {text}")
            parts.append("")
    else:
        for seg in transcript.segments or []:
            text = (seg.text or "").replace("\n", " ").strip()
            if not text:
                continue
            start = _format_timestamp(seg.start)
            end = _format_timestamp(seg.end)
            parts.append(f"[VID={audio.video_id}][PLATFORM={platform}][TIME={start}-{end}] {text}")
            parts.append("")

    return "\n".join(parts).strip() + "\n"


_LIBRARY_QUERY_PATTERNS = [
    r"知识库.*(有什么|有哪些|有没有|有无)",
    r"知识库.*有.*(视频|课程|课|内容|资料)",
    r"(库里|库中).*(有什么|有哪些|有没有|有无)",
    r"(库里|库中).*有.*(视频|课程|课|内容|资料)",
    r"(有没有|是否有|有无|存在).*(视频|课程|课|内容|资料)",
    r"(有什么|有哪些).*(视频|课程|课|内容|资料)",
]

_TIME_RANGE_RE = re.compile(r"TIME=([0-9]{1,2}:[0-9]{2}(?::[0-9]{2})?-[0-9]{1,2}:[0-9]{2}(?::[0-9]{2})?)")


def is_library_query(query: str) -> bool:
    q = (query or "").strip()
    if not q:
        return False
    return any(re.search(p, q, flags=re.IGNORECASE) for p in _LIBRARY_QUERY_PATTERNS)


def _extract_time_ranges(text: str) -> list[str]:
    if not text:
        return []
    return list(dict.fromkeys(m.group(1) for m in _TIME_RANGE_RE.finditer(text)))


def _is_video_doc_name(name: str) -> bool:
    n = (name or "").strip()
    if not n.endswith("]"):
        return False
    left = n.rfind("[")
    if left < 0:
        return False
    tag = n[left + 1 : -1]
    if ":" not in tag:
        return False
    platform, video_id = tag.split(":", 1)
    return bool(platform.strip()) and bool(video_id.strip())


def _score_of(hit: dict[str, Any]) -> float:
    raw = hit.get("score")
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 0.0


def _is_video_like_resource(hit: dict[str, Any]) -> bool:
    doc_name = str(hit.get("document_name") or "").strip()
    content = str(hit.get("content") or "")
    if _TIME_RANGE_RE.search(content):
        return True
    if doc_name and _is_video_doc_name(doc_name):
        return True
    return False


def build_library_answer_from_resources(
    *,
    query: str,
    resources: Iterable[dict[str, Any]] | None,
    high_confidence_score: float = 0.65,
    max_docs: int = 5,
    max_time_ranges_per_doc: int = 3,
) -> str | None:
    """
    For meta questions like "知识库里有没有 XX 的视频？", the LLM may answer poorly even when
    retrieval resources are present. This function converts retriever_resources into a stable answer.
    """
    if not is_library_query(query):
        return None

    resources_list = [r for r in (resources or []) if isinstance(r, dict)]
    resources_list = [r for r in resources_list if _is_video_like_resource(r)]
    if not resources_list:
        return (
            "目前没有检索到相关视频。你可以先入库视频，或换更具体的关键词（例如具体章节/概念名）再试。"
        )

    best_score = max((_score_of(r) for r in resources_list), default=0.0)
    confident = bool(best_score >= high_confidence_score)

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in resources_list:
        doc = (r.get("document_name") or r.get("document_id") or "unknown").strip()
        grouped[doc].append(r)

    def doc_sort_key(item: tuple[str, list[dict[str, Any]]]):
        _, hits = item
        return max((_score_of(h) for h in hits), default=0.0)

    doc_items = sorted(grouped.items(), key=doc_sort_key, reverse=True)[:max_docs]

    lines: list[str] = []
    for doc, hits in doc_items:
        time_ranges: list[str] = []
        for hit in sorted(hits, key=lambda x: (x.get("position") or 0)):
            time_ranges.extend(_extract_time_ranges(str(hit.get("content") or "")))
            time_ranges = list(dict.fromkeys(time_ranges))
            if len(time_ranges) >= max_time_ranges_per_doc:
                time_ranges = time_ranges[:max_time_ranges_per_doc]
                break
        if time_ranges:
            lines.append(f"- {doc}（命中时间戳：{'、'.join(time_ranges)}）")
        else:
            lines.append(f"- {doc}")

    header = "有。知识库检索到以下相关视频：" if confident else "检索到一些可能相关的视频（匹配度不高，仅供参考）："
    return header + "\n" + "\n".join(lines)
