import re
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


def build_rag_document_name(audio: AudioDownloadResult, platform: str, created_at_ms: int | None = None) -> str:
    safe_title = (audio.title or "").strip() or "Untitled"
    safe_video_id = (audio.video_id or "").strip() or "unknown"
    tag = f"{platform}:{safe_video_id}"
    if isinstance(created_at_ms, int) and created_at_ms > 0:
        tag = f"{tag}:{created_at_ms}"
    return f"{safe_title} [{tag}]"


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

_TIME_RANGE_RE = re.compile(
    r"TIME=([0-9]{1,2}:[0-9]{2}(?::[0-9]{2})?-[0-9]{1,2}:[0-9]{2}(?::[0-9]{2})?)"
)
_LIBRARY_LIST_ALL_RE = re.compile(r"(都有什么|有哪些|列表|清单|全部|所有)", flags=re.IGNORECASE)
_LIBRARY_QUERY_TOKEN_RE = re.compile(r"[A-Za-z0-9]{2,}|[\u4e00-\u9fff]{2,}")
_LIBRARY_QUERY_STOPWORDS = {
    "知识库",
    "库",
    "里面",
    "里",
    "都有",
    "都有啥",
    "都有什么",
    "有哪些",
    "有什么",
    "列表",
    "清单",
    "全部",
    "所有",
    "有没有",
    "有无",
    "是否",
    "存在",
    "视频",
    "课程",
    "课",
    "内容",
    "资料",
    "链接",
    "网址",
    "url",
    "link",
}

_BILIBILI_PART_RE = re.compile(r"^(BV[0-9A-Za-z]+)(?:_p([0-9]+))?$", flags=re.IGNORECASE)


def is_library_query(query: str) -> bool:
    q = (query or "").strip()
    if not q:
        return False
    return any(re.search(p, q, flags=re.IGNORECASE) for p in _LIBRARY_QUERY_PATTERNS)


_SMALL_TALK_EXACT = {
    "你好",
    "您好",
    "嗨",
    "哈喽",
    "哈囉",
    "hello",
    "hi",
    "hey",
    "在吗",
    "在不在",
    "早上好",
    "中午好",
    "下午好",
    "晚上好",
    "晚安",
    "谢谢",
    "谢谢你",
    "thanks",
    "thankyou",
    "thank you",
    "再见",
    "拜拜",
    "bye",
    "goodbye",
    "好的",
    "嗯",
    "哦",
    "ok",
    "okay",
    "收到",
    "明白",
    "你是谁",
    "你叫什么",
    "你能做什么",
    "你可以做什么",
    "help",
    "帮助",
}

_SMALL_TALK_PREFIX_RE = re.compile(r"^(你好|您好|嗨|哈喽|哈囉)(呀|啊|哇|哈|~|～|！|!|。|\.|,|，)?$")
_SMALL_TALK_ASCII_RE = re.compile(r"^(hi|hello|hey)(there)?$", flags=re.IGNORECASE)


def _normalize_short_query(query: str) -> str:
    q = (query or "").strip()
    if not q:
        return ""
    q = re.sub(r"\s+", "", q)
    q = re.sub(r"^[,，。.!！?？:：;；~～…]+", "", q)
    q = re.sub(r"[,，。.!！?？:：;；~～…]+$", "", q)
    return q.strip()


def is_small_talk_query(query: str) -> bool:
    q = _normalize_short_query(query)
    if not q:
        return True
    # If the user typed a real question, don't treat it as small talk.
    if len(q) > 12 and any(ch in q for ch in ("?", "？", "怎么", "如何", "为什么", "哪里", "时间戳", "链接")):
        return False

    q_lower = q.lower()
    if q in _SMALL_TALK_EXACT or q_lower in _SMALL_TALK_EXACT:
        return True
    if _SMALL_TALK_PREFIX_RE.fullmatch(q):
        return True
    if _SMALL_TALK_ASCII_RE.fullmatch(q_lower):
        return True
    return False


def build_small_talk_answer(query: str) -> str:
    q = _normalize_short_query(query)
    q_lower = q.lower()

    if q in {"谢谢", "谢谢你"} or q_lower in {"thanks", "thankyou", "thank you"}:
        return "不客气！你想查哪个视频/哪个概念？我可以帮你定位到具体时间戳。"

    if q in {"再见", "拜拜"} or q_lower in {"bye", "goodbye"}:
        return "再见！下次需要定位视频内容或时间戳，随时叫我。"

    if q in {"你是谁", "你叫什么", "你能做什么", "你可以做什么"} or q_lower in {"help", "帮助"}:
        return (
            "我是你的视频知识库助手，可以基于已入库的视频字幕/笔记回答问题，并给出可跳转的时间戳引用。\n"
            "你可以试试问：哪里讲到“XXX”？给我时间戳。"
        )

    return "你好！我可以帮你在已入库的视频里检索内容、总结要点，并定位到具体时间戳。你想查什么？"


def _extract_time_ranges(text: str) -> list[str]:
    if not text:
        return []
    return list(dict.fromkeys(m.group(1) for m in _TIME_RANGE_RE.finditer(text)))


def _is_video_doc_name(name: str) -> bool:
    n = (name or "").strip()
    right = n.rfind("]")
    if right < 0:
        return False
    left = n.rfind("[", 0, right)
    if left < 0:
        return False
    tag = n[left + 1 : right]
    if ":" not in tag:
        return False
    platform, video_id = tag.split(":", 1)
    return bool(platform.strip()) and bool(video_id.strip())


def _split_video_doc_tag(name: str) -> tuple[str, str, str] | None:
    n = (name or "").strip()
    right = n.rfind("]")
    if right < 0:
        return None
    left = n.rfind("[", 0, right)
    if left < 0:
        return None
    tag = n[left + 1 : right].strip()
    if ":" not in tag:
        return None
    parts = [p.strip() for p in tag.split(":")]
    if len(parts) < 2:
        return None
    platform = parts[0]
    video_id = parts[1]
    if not platform or not video_id:
        return None
    title = n[:left].strip()
    return title, platform, video_id


def _build_video_url(platform: str, video_id: str) -> str:
    p = (platform or "").strip().lower()
    vid = (video_id or "").strip()
    if not p or not vid:
        return ""

    if p == "bilibili":
        m = _BILIBILI_PART_RE.match(vid)
        base = vid
        part: str | None = None
        if m:
            base = m.group(1)
            part = m.group(2)
        url = f"https://www.bilibili.com/video/{base}"
        if part:
            try:
                url += f"?p={int(part)}"
            except ValueError:
                url += f"?p={part}"
        return url

    if p in {"youtube", "yt"}:
        return f"https://www.youtube.com/watch?v={vid}"

    return ""


def _library_query_tokens(query: str) -> list[str]:
    raw_tokens = _LIBRARY_QUERY_TOKEN_RE.findall(query or "")
    cleaned: list[str] = []
    seen: set[str] = set()
    for token in raw_tokens:
        t = str(token).strip()
        if not t:
            continue
        if t in _LIBRARY_QUERY_STOPWORDS or t.lower() in _LIBRARY_QUERY_STOPWORDS:
            continue
        # Drop long tokens that are mostly question boilerplate, e.g. "知识库里都有什么视频".
        if any(len(sw) >= 2 and sw in t for sw in _LIBRARY_QUERY_STOPWORDS):
            continue
        key = t.lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(t)
    return cleaned


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
        doc_raw = r.get("document_name") or r.get("document_id") or "unknown"
        doc = str(doc_raw).strip() or "unknown"
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


def build_library_answer_from_documents(
    *,
    query: str,
    documents: Iterable[dict[str, Any]] | None,
    resources: Iterable[dict[str, Any]] | None = None,
    max_docs: int = 20,
    max_time_ranges_per_doc: int = 3,
) -> str | None:
    """
    For questions like “知识库里都有什么视频/链接？”，比起依赖检索命中，更可靠的方式是直接列出数据集中已有文档。
    同时，如果文档名以 “[platform:video_id]” 结尾，则会补充可点击的外链（例如 B 站）。
    """
    if not is_library_query(query):
        return None

    docs = [d for d in (documents or []) if isinstance(d, dict)]
    if not docs:
        fallback = build_library_answer_from_resources(query=query, resources=resources)
        return fallback or "知识库里暂时还没有任何视频/资料。"

    wants_all = bool(_LIBRARY_LIST_ALL_RE.search(query or ""))
    tokens = _library_query_tokens(query)

    matched_docs = docs
    no_match = False
    if tokens:
        filtered: list[dict[str, Any]] = []
        for d in docs:
            name_l = str(d.get("name") or "").lower()
            if any(t.lower() in name_l for t in tokens):
                filtered.append(d)
        if filtered:
            matched_docs = filtered
        else:
            no_match = True
            matched_docs = docs

    show_hit_ranges = bool(tokens) and not no_match

    def sort_key(d: dict[str, Any]) -> int:
        pos = d.get("position")
        try:
            return int(pos)
        except (TypeError, ValueError):
            return 0

    matched_docs = sorted(matched_docs, key=sort_key)

    time_by_doc_key: dict[str, list[str]] = {}
    for r in [x for x in (resources or []) if isinstance(x, dict)]:
        doc_id = str(r.get("document_id") or "").strip()
        doc_name = str(r.get("document_name") or "").strip()
        key = doc_id or doc_name
        if not key:
            continue
        for tr in _extract_time_ranges(str(r.get("content") or "")):
            existing = time_by_doc_key.setdefault(key, [])
            if tr in existing:
                continue
            existing.append(tr)
            if len(existing) >= max_time_ranges_per_doc:
                break

    shown = matched_docs[: max(1, int(max_docs or 20))]
    lines: list[str] = []
    for d in shown:
        name = str(d.get("name") or "").strip()
        if not name:
            continue

        doc_id = str(d.get("id") or "").strip()
        tag = _split_video_doc_tag(name)
        url: str | None = None
        if tag:
            title, platform, video_id = tag
            display = title or name
            url = _build_video_url(platform, video_id) or None
            main = display
            extra = f"（{platform}:{video_id}）"
        else:
            main = name
            extra = ""

        hit_ranges: list[str] = []
        if doc_id and doc_id in time_by_doc_key:
            hit_ranges = time_by_doc_key[doc_id]
        elif name in time_by_doc_key:
            hit_ranges = time_by_doc_key[name]

        parts = [f"- {main}{extra}".rstrip()]
        if url:
            parts.append(f"  链接：{url}")
        if show_hit_ranges and hit_ranges:
            parts.append(f"  命中时间戳：{'、'.join(hit_ranges)}")
        lines.append("\n".join(parts).rstrip())

    total = len(docs)
    matched = len(matched_docs) if not no_match else 0

    if no_match and tokens:
        header = f"知识库里暂时没找到包含“{'/'.join(tokens)}”的视频；当前已入库 {total} 条："
    elif tokens and not wants_all:
        header = f"知识库里找到 {matched} 条可能相关的视频："
    else:
        header = f"知识库当前共有 {total} 条视频/资料："

    if len(matched_docs) > len(shown):
        header += f"（仅展示前 {len(shown)} 条）"

    return header + "\n" + "\n".join(lines)
