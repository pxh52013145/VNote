import os
import json as jsonlib
from dataclasses import dataclass
from typing import Any, Optional

import httpx

from app.services.dify_config_manager import DifyConfigManager


class DifyError(RuntimeError):
    pass


@dataclass(frozen=True)
class DifyConfig:
    base_url: str
    dataset_id: str
    note_dataset_id: str
    transcript_dataset_id: str
    service_api_key: Optional[str]
    app_api_key: Optional[str]
    app_user: str
    indexing_technique: str
    timeout_seconds: float

    @staticmethod
    def from_env() -> "DifyConfig":
        # Defaults come from env/.env, but can be overridden by the persisted UI config.
        base_url = os.getenv("DIFY_BASE_URL", "http://localhost").strip() or "http://localhost"
        dataset_id = os.getenv("DIFY_DATASET_ID", "").strip()
        note_dataset_id = os.getenv("DIFY_NOTE_DATASET_ID", "").strip()
        transcript_dataset_id = os.getenv("DIFY_TRANSCRIPT_DATASET_ID", "").strip()
        service_api_key = os.getenv("DIFY_SERVICE_API_KEY")
        app_api_key = os.getenv("DIFY_APP_API_KEY")
        app_user = os.getenv("DIFY_APP_USER", "bilinote").strip() or "bilinote"
        indexing_technique = os.getenv("DIFY_INDEXING_TECHNIQUE", "high_quality").strip() or "high_quality"
        timeout_seconds = float(os.getenv("DIFY_TIMEOUT_SECONDS", "60") or "60")

        persisted = DifyConfigManager().get()
        if isinstance(persisted, dict) and persisted:
            p_base_url = str(persisted.get("base_url") or "").strip()
            if p_base_url:
                base_url = p_base_url

            p_dataset_id = str(persisted.get("dataset_id") or "").strip()
            if p_dataset_id:
                dataset_id = p_dataset_id

            p_note_dataset_id = str(persisted.get("note_dataset_id") or "").strip()
            if p_note_dataset_id:
                note_dataset_id = p_note_dataset_id

            p_transcript_dataset_id = str(persisted.get("transcript_dataset_id") or "").strip()
            if p_transcript_dataset_id:
                transcript_dataset_id = p_transcript_dataset_id

            p_service_key = str(persisted.get("service_api_key") or "").strip()
            if p_service_key:
                service_api_key = p_service_key

            p_app_key = str(persisted.get("app_api_key") or "").strip()
            if p_app_key:
                app_api_key = p_app_key

            p_app_user = str(persisted.get("app_user") or "").strip()
            if p_app_user:
                app_user = p_app_user

            p_indexing = str(persisted.get("indexing_technique") or "").strip()
            if p_indexing:
                indexing_technique = p_indexing

            p_timeout = persisted.get("timeout_seconds")
            if p_timeout is not None:
                try:
                    timeout_seconds = float(p_timeout)
                except (TypeError, ValueError):
                    pass

        # Allow copying from paths like: "datasets/<uuid>" or "/datasets/<uuid>"
        def _normalize_dataset_id(value: str) -> str:
            v = (value or "").strip().lstrip("/")
            if v.startswith("datasets/"):
                v = v.split("/", 1)[1].strip()
            return v

        dataset_id = _normalize_dataset_id(dataset_id)
        note_dataset_id = _normalize_dataset_id(note_dataset_id)
        transcript_dataset_id = _normalize_dataset_id(transcript_dataset_id)

        return DifyConfig(
            base_url=base_url,
            dataset_id=dataset_id,
            note_dataset_id=note_dataset_id,
            transcript_dataset_id=transcript_dataset_id,
            service_api_key=service_api_key.strip() if service_api_key else None,
            app_api_key=app_api_key.strip() if app_api_key else None,
            app_user=app_user,
            indexing_technique=indexing_technique,
            timeout_seconds=timeout_seconds,
        )

    def v1_base_url(self) -> str:
        base = self.base_url.rstrip("/")
        if base.endswith("/v1"):
            return base
        return f"{base}/v1"


class DifyHttpClient:
    def __init__(self, config: DifyConfig):
        self._config = config
        self._client = httpx.Client(timeout=httpx.Timeout(config.timeout_seconds))

    def close(self) -> None:
        self._client.close()

    def _request(
        self,
        method: str,
        path: str,
        *,
        api_key: str,
        params: Optional[dict[str, Any]] = None,
        json: Any = None,
    ) -> dict[str, Any]:
        url = f"{self._config.v1_base_url()}/{path.lstrip('/')}"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        try:
            resp = self._client.request(method, url, headers=headers, params=params, json=json)
        except httpx.RequestError as exc:
            raise DifyError(f"Dify request failed: {exc}") from exc

        if resp.status_code >= 400:
            body = resp.content.decode("utf-8", errors="replace")
            raise DifyError(f"Dify API error {resp.status_code}: {body}")

        try:
            # Avoid resp.text encoding heuristics; Dify responses are JSON (UTF-8).
            return jsonlib.loads(resp.content)
        except ValueError as exc:
            preview = resp.content[:2000].decode("utf-8", errors="replace")
            raise DifyError(f"Dify response is not JSON: {preview}") from exc


class DifyKnowledgeClient:
    def __init__(self, config: DifyConfig):
        self._config = config
        self._http = DifyHttpClient(config)

    def close(self) -> None:
        self._http.close()

    def list_documents(
        self,
        *,
        dataset_id: Optional[str] = None,
        page: int = 1,
        limit: int = 20,
    ) -> dict[str, Any]:
        dataset = (dataset_id or self._config.dataset_id).strip() if (dataset_id or self._config.dataset_id) else ""
        if not dataset:
            raise DifyError("Missing Dify dataset id (set DIFY_DATASET_ID or per-call dataset_id)")
        if not self._config.service_api_key:
            raise DifyError("Missing DIFY_SERVICE_API_KEY")
        page = max(1, int(page or 1))
        limit = max(1, min(int(limit or 20), 100))

        return self._http._request(
            "GET",
            f"/datasets/{dataset}/documents",
            api_key=self._config.service_api_key,
            params={"page": page, "limit": limit},
        )

    def retrieve(
        self,
        *,
        dataset_id: Optional[str] = None,
        query: str,
        top_k: int = 5,
        score_threshold: Optional[float] = None,
    ) -> dict[str, Any]:
        dataset = (dataset_id or self._config.dataset_id).strip() if (dataset_id or self._config.dataset_id) else ""
        if not dataset:
            raise DifyError("Missing Dify dataset id (set DIFY_DATASET_ID or per-call dataset_id)")
        if not self._config.service_api_key:
            raise DifyError("Missing DIFY_SERVICE_API_KEY")

        payload: dict[str, Any] = {
            "query": query,
            "top_k": max(1, int(top_k or 5)),
        }
        if score_threshold is not None:
            payload["score_threshold"] = float(score_threshold)

        return self._http._request(
            "POST",
            f"/datasets/{dataset}/retrieve",
            api_key=self._config.service_api_key,
            json=payload,
        )

    def create_document_by_text(
        self,
        *,
        dataset_id: Optional[str] = None,
        name: str,
        text: str,
        doc_language: str = "Chinese Simplified",
    ) -> dict[str, Any]:
        dataset = (dataset_id or self._config.dataset_id).strip() if (dataset_id or self._config.dataset_id) else ""
        if not dataset:
            raise DifyError("Missing Dify dataset id (set DIFY_DATASET_ID or per-call dataset_id)")
        if not self._config.service_api_key:
            raise DifyError("Missing DIFY_SERVICE_API_KEY")

        payload = {
            "name": name,
            "text": text,
            "doc_language": doc_language,
            # Dify v1.11+ requires this field for knowledge indexing.
            "indexing_technique": self._config.indexing_technique,
        }
        return self._http._request(
            "POST",
            f"/datasets/{dataset}/document/create-by-text",
            api_key=self._config.service_api_key,
            json=payload,
        )

    def get_batch_indexing_status(self, *, batch: str, dataset_id: Optional[str] = None) -> dict[str, Any]:
        dataset = (dataset_id or self._config.dataset_id).strip() if (dataset_id or self._config.dataset_id) else ""
        if not dataset:
            raise DifyError("Missing Dify dataset id (set DIFY_DATASET_ID or per-call dataset_id)")
        if not self._config.service_api_key:
            raise DifyError("Missing DIFY_SERVICE_API_KEY")

        return self._http._request(
            "GET",
            f"/datasets/{dataset}/documents/{batch}/indexing-status",
            api_key=self._config.service_api_key,
        )


class DifyChatClient:
    def __init__(self, config: DifyConfig):
        self._config = config
        self._http = DifyHttpClient(config)

    def close(self) -> None:
        self._http.close()

    def chat(
        self,
        *,
        query: str,
        conversation_id: Optional[str] = None,
        user: Optional[str] = None,
        response_mode: str = "blocking",
        inputs: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        if not self._config.app_api_key:
            raise DifyError("Missing DIFY_APP_API_KEY")

        payload: dict[str, Any] = {
            "inputs": inputs or {},
            "query": query,
            "response_mode": response_mode,
            "user": (user or self._config.app_user),
        }
        if conversation_id:
            payload["conversation_id"] = conversation_id

        return self._http._request("POST", "/chat-messages", api_key=self._config.app_api_key, json=payload)
