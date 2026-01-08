from __future__ import annotations

import io
import os
import re
import hashlib
from dataclasses import dataclass
from typing import Optional

try:
    from minio import Minio  # type: ignore
    from minio.error import S3Error  # type: ignore
except Exception:  # pragma: no cover
    Minio = None  # type: ignore
    S3Error = Exception  # type: ignore


class MinioConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class MinioConfig:
    endpoint: str
    access_key: str
    secret_key: str
    secure: bool
    bucket_prefix: str
    object_prefix: str
    tombstone_prefix: str
    region: Optional[str]

    @staticmethod
    def from_env() -> "MinioConfig":
        endpoint = (os.getenv("MINIO_ENDPOINT") or "").strip()
        access_key = (os.getenv("MINIO_ACCESS_KEY") or "").strip()
        secret_key = (os.getenv("MINIO_SECRET_KEY") or "").strip()
        secure_raw = (os.getenv("MINIO_SECURE") or "false").strip().lower()
        secure = secure_raw in {"1", "true", "yes", "y", "on"}
        bucket_prefix = (os.getenv("MINIO_BUCKET_PREFIX") or "ragvideo-").strip() or "ragvideo-"
        object_prefix = (os.getenv("MINIO_OBJECT_PREFIX") or "bundles/").strip() or "bundles/"
        tombstone_prefix = (os.getenv("MINIO_TOMBSTONE_PREFIX") or "tombstones/").strip() or "tombstones/"
        region = (os.getenv("MINIO_REGION") or "").strip() or None

        if not endpoint:
            raise MinioConfigError("Missing MINIO_ENDPOINT")
        if not access_key:
            raise MinioConfigError("Missing MINIO_ACCESS_KEY")
        if not secret_key:
            raise MinioConfigError("Missing MINIO_SECRET_KEY")

        if not object_prefix.endswith("/"):
            object_prefix = f"{object_prefix}/"
        if not tombstone_prefix.endswith("/"):
            tombstone_prefix = f"{tombstone_prefix}/"

        return MinioConfig(
            endpoint=endpoint,
            access_key=access_key,
            secret_key=secret_key,
            secure=secure,
            bucket_prefix=bucket_prefix,
            object_prefix=object_prefix,
            tombstone_prefix=tombstone_prefix,
            region=region,
        )


_BUCKET_SAFE_RE = re.compile(r"[^a-z0-9.-]+")


def bucket_name_for_profile(profile_name: str, *, prefix: str) -> str:
    original = (profile_name or "").strip()

    slug = (original.lower() or "default").strip()
    slug = _BUCKET_SAFE_RE.sub("-", slug)
    slug = re.sub(r"-{2,}", "-", slug).strip("-.") or "default"

    base = f"{(prefix or '').strip().lower()}{slug}"
    base = _BUCKET_SAFE_RE.sub("-", base)
    base = re.sub(r"-{2,}", "-", base).strip("-.") or "ragvideo-default"

    suffix = ""
    if original:
        # Ensure different profiles never collide even if they slugify to the same value
        # (e.g. Chinese names or names that differ only in punctuation).
        suffix = f"-{hashlib.sha1(original.encode('utf-8')).hexdigest()[:8]}"

    # S3 bucket constraints: 3-63 chars, lowercase letters, digits, '.' and '-'.
    max_len = 63 - len(suffix)
    if max_len < 3:
        max_len = 3
    base = base[:max_len].strip("-.") or "ragvideo-default"
    full = f"{base}{suffix}"
    full = _BUCKET_SAFE_RE.sub("-", full)
    full = re.sub(r"-{2,}", "-", full).strip("-.")
    if not full:
        full = "ragvideo-default"

    full = full[:63]
    if len(full) < 3:
        full = (full + "-bin")[:3]
    if not re.match(r"^[a-z0-9]", full):
        full = f"b{full[1:]}"
    if not re.match(r"[a-z0-9]$", full):
        full = f"{full[:-1]}0"
    return full


class MinioStorage:
    def __init__(self, config: MinioConfig):
        self._config = config
        if Minio is None:
            raise MinioConfigError("Missing python package 'minio' (pip install -r backend/requirements.txt)")
        self._client = Minio(
            config.endpoint,
            access_key=config.access_key,
            secret_key=config.secret_key,
            secure=config.secure,
            region=config.region,
        )

    @property
    def config(self) -> MinioConfig:
        return self._config

    def ensure_bucket(self, bucket: str) -> None:
        b = (bucket or "").strip()
        if not b:
            raise ValueError("Missing bucket")
        try:
            if self._client.bucket_exists(b):
                return
            self._client.make_bucket(b)
        except S3Error as exc:
            raise RuntimeError(f"MinIO ensure_bucket failed: {exc}") from exc

    def put_bytes(
        self,
        *,
        bucket: str,
        object_key: str,
        data: bytes,
        content_type: str,
        metadata: Optional[dict[str, str]] = None,
    ) -> None:
        b = (bucket or "").strip()
        k = (object_key or "").strip().lstrip("/")
        if not b:
            raise ValueError("Missing bucket")
        if not k:
            raise ValueError("Missing object_key")

        self.ensure_bucket(b)

        try:
            stream = io.BytesIO(data)
            self._client.put_object(
                b,
                k,
                stream,
                length=len(data),
                content_type=content_type,
                metadata=metadata,
            )
        except S3Error as exc:
            raise RuntimeError(f"MinIO put_object failed: {exc}") from exc

    def get_bytes(self, *, bucket: str, object_key: str) -> bytes:
        b = (bucket or "").strip()
        k = (object_key or "").strip().lstrip("/")
        if not b:
            raise ValueError("Missing bucket")
        if not k:
            raise ValueError("Missing object_key")

        resp = None
        try:
            resp = self._client.get_object(b, k)
            return resp.read()
        except S3Error as exc:
            raise RuntimeError(f"MinIO get_object failed: {exc}") from exc
        finally:
            try:
                if resp:
                    resp.close()
                    resp.release_conn()
            except Exception:
                pass

    def stat(self, *, bucket: str, object_key: str) -> dict[str, str] | None:
        b = (bucket or "").strip()
        k = (object_key or "").strip().lstrip("/")
        if not b or not k:
            return None
        try:
            st = self._client.stat_object(b, k)
            meta = getattr(st, "metadata", None)
            meta_dict = meta if isinstance(meta, dict) else {}
            return {
                "etag": getattr(st, "etag", "") or "",
                "content_type": getattr(st, "content_type", "") or "",
                "size": str(getattr(st, "size", "") or ""),
                "last_modified": str(getattr(st, "last_modified", "") or ""),
                "metadata": meta_dict,  # keys are typically like "x-amz-meta-..."
            }
        except S3Error as exc:
            code = str(getattr(exc, "code", "") or "").strip()
            # Treat missing objects/buckets as "not found".
            if code in {"NoSuchKey", "NoSuchObject", "NoSuchBucket", "NotFound"}:
                return None
            raise RuntimeError(f"MinIO stat_object failed: {exc}") from exc

    def remove_object(self, *, bucket: str, object_key: str) -> None:
        b = (bucket or "").strip()
        k = (object_key or "").strip().lstrip("/")
        if not b:
            raise ValueError("Missing bucket")
        if not k:
            raise ValueError("Missing object_key")
        try:
            self._client.remove_object(b, k)
        except S3Error as exc:
            raise RuntimeError(f"MinIO remove_object failed: {exc}") from exc
