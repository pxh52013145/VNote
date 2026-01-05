from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

from app.services.dify_client import DifyChatClient, DifyConfig, DifyError, DifyKnowledgeClient
from app.services.rag_service import (
    build_small_talk_answer,
    build_library_answer_from_documents,
    build_library_answer_from_resources,
    is_library_query,
    is_small_talk_query,
)
from app.utils.logger import get_logger
from app.utils.response import ResponseWrapper as R

router = APIRouter()
logger = get_logger(__name__)


class RagChatRequest(BaseModel):
    query: str
    conversation_id: Optional[str] = None
    user: Optional[str] = None


@router.post("/rag/chat")
def rag_chat(data: RagChatRequest):
    # For greetings / chit-chat, don't run retrieval (avoids irrelevant citations).
    if is_small_talk_query(data.query):
        return R.success(
            {
                "answer": build_small_talk_answer(data.query),
                "conversation_id": data.conversation_id,
                "message_id": None,
                "task_id": None,
                "retriever_resources": [],
                "raw": {"source": "small_talk"},
            }
        )

    cfg = DifyConfig.from_env()
    transcript_dataset_id = cfg.transcript_dataset_id or cfg.dataset_id
    client = DifyChatClient(cfg)
    try:
        resp = client.chat(
            query=data.query,
            conversation_id=data.conversation_id,
            user=data.user,
            response_mode="blocking",
        )
    except DifyError as exc:
        return R.error(msg=str(exc), code=500)
    finally:
        client.close()

    metadata = resp.get("metadata") if isinstance(resp, dict) else {}
    resources = metadata.get("retriever_resources") if isinstance(metadata, dict) else []
    if not isinstance(resources, list):
        resources = []

    # If Dify didn't return citations, try a direct dataset retrieve so the UI can still show references.
    if not resources and not is_library_query(data.query):
        knowledge_client: DifyKnowledgeClient | None = None
        try:
            knowledge_client = DifyKnowledgeClient(cfg)
            retrieve_resp = knowledge_client.retrieve(
                dataset_id=transcript_dataset_id,
                query=data.query,
                top_k=5,
                score_threshold=0.3,
            )

            records = retrieve_resp.get("records") if isinstance(retrieve_resp, dict) else []
            fallback: list[dict] = []
            if isinstance(records, list):
                for idx, rec in enumerate(records, start=1):
                    if not isinstance(rec, dict):
                        continue
                    seg = rec.get("segment") if isinstance(rec.get("segment"), dict) else {}
                    doc = seg.get("document") if isinstance(seg.get("document"), dict) else {}

                    content = str(seg.get("content") or "")
                    if not content.strip():
                        continue

                    try:
                        score = float(rec.get("score") or 0.0)
                    except (TypeError, ValueError):
                        score = 0.0

                    fallback.append(
                        {
                            "position": idx,
                            "dataset_id": transcript_dataset_id,
                            "dataset_name": "knowledge",
                            "document_id": str(seg.get("document_id") or doc.get("id") or ""),
                            "document_name": str(doc.get("name") or ""),
                            "segment_id": str(seg.get("id") or ""),
                            "score": score,
                            "content": content,
                        }
                    )

            resources = fallback
        except DifyError as exc:
            logger.warning("Dify retrieve fallback failed: %s", exc)
        finally:
            if knowledge_client:
                knowledge_client.close()

    answer = resp.get("answer")

    override = None
    if is_library_query(data.query):
        docs = None
        knowledge_client: DifyKnowledgeClient | None = None
        try:
            knowledge_client = DifyKnowledgeClient(cfg)
            docs_all: list[dict] = []
            page = 1
            while True:
                docs_resp = knowledge_client.list_documents(dataset_id=transcript_dataset_id, page=page, limit=100)
                if not isinstance(docs_resp, dict):
                    break
                batch = docs_resp.get("data")
                if isinstance(batch, list):
                    docs_all.extend([d for d in batch if isinstance(d, dict)])
                if not docs_resp.get("has_more"):
                    break
                page += 1
                if page > 50:
                    break
            docs = docs_all
        except DifyError as exc:
            logger.warning("Dify list_documents failed: %s", exc)
        finally:
            if knowledge_client:
                knowledge_client.close()

        try:
            override = build_library_answer_from_documents(query=data.query, documents=docs, resources=resources)
        except Exception:
            logger.exception("build_library_answer_from_documents failed")

    if not override:
        try:
            override = build_library_answer_from_resources(query=data.query, resources=resources)
        except Exception:
            logger.exception("build_library_answer_from_resources failed")

    if override:
        answer = override

    return R.success(
        {
            "answer": answer,
            "conversation_id": resp.get("conversation_id"),
            "message_id": resp.get("message_id"),
            "task_id": resp.get("task_id"),
            "retriever_resources": resources or [],
            "raw": resp,
        }
    )
