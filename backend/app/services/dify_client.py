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
        dataset_id = dataset_id.lstrip("/")
        if dataset_id.startswith("datasets/"):
            dataset_id = dataset_id.split("/", 1)[1].strip()

        return DifyConfig(
            base_url=base_url,
            dataset_id=dataset_id,
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

    def create_document_by_text(
        self,
        *,
        name: str,
        text: str,
        doc_language: str = "Chinese Simplified",
    ) -> dict[str, Any]:
        if not self._config.dataset_id:
            raise DifyError("Missing DIFY_DATASET_ID")
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
            f"/datasets/{self._config.dataset_id}/document/create-by-text",
            api_key=self._config.service_api_key,
            json=payload,
        )

    def get_batch_indexing_status(self, *, batch: str) -> dict[str, Any]:
        if not self._config.dataset_id:
            raise DifyError("Missing DIFY_DATASET_ID")
        if not self._config.service_api_key:
            raise DifyError("Missing DIFY_SERVICE_API_KEY")

        return self._http._request(
            "GET",
            f"/datasets/{self._config.dataset_id}/documents/{batch}/indexing-status",
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
