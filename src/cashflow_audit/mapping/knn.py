from __future__ import annotations

import numpy as np

from cashflow_audit.errors import PortError
from cashflow_audit.mapping.models import Concept
from cashflow_audit.ports.protocols import EmbedPort

COSINE_MIN = 0.85
COSINE_GAP = 0.08
TOP_K = 5


def cosine(a: list[float], b: list[float]) -> float:
    va = np.asarray(a, dtype=float)
    vb = np.asarray(b, dtype=float)
    na = float(np.linalg.norm(va))
    nb = float(np.linalg.norm(vb))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return float(np.dot(va, vb) / (na * nb))


def concept_vectors(embed: EmbedPort, taxonomy: list[Concept]) -> dict[str, list[float]]:
    labels: list[str] = []
    owners: list[str] = []
    for concept in taxonomy:
        for label in concept.labels:
            labels.append(label)
            owners.append(concept.id)
    if not labels:
        return {}
    try:
        vectors = embed.embed(labels)
    except PortError:
        return {}
    buckets: dict[str, list[list[float]]] = {}
    for concept_id, vec in zip(owners, vectors, strict=True):
        buckets.setdefault(concept_id, []).append(vec)
    return {cid: np.mean(np.asarray(vecs), axis=0).tolist() for cid, vecs in buckets.items()}


def rank_concepts(
    query: list[float],
    index: dict[str, list[float]],
) -> list[tuple[str, float]]:
    scored = [(cid, cosine(query, vec)) for cid, vec in index.items()]
    scored.sort(key=lambda item: item[1], reverse=True)
    return scored


def confident_match(ranked: list[tuple[str, float]]) -> str | None:
    if not ranked:
        return None
    top_id, top_score = ranked[0]
    if top_score < COSINE_MIN:
        return None
    if len(ranked) > 1 and top_score - ranked[1][1] < COSINE_GAP:
        return None
    return top_id
