from __future__ import annotations

import hashlib


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def audit_id_for(actor_id: str, content_sha256: str) -> str:
    return hashlib.sha256(f"{actor_id}:{content_sha256}".encode()).hexdigest()
