from __future__ import annotations

from collections import defaultdict

from scipy.sparse.csgraph import connected_components

from cashflow_audit.compile.models import CsrGraph


def circular_groups(csr: CsrGraph) -> list[list[str]]:
    n = len(csr.nodes)
    if n == 0:
        return []
    _count, labels = connected_components(csr.matrix, directed=True, connection="strong")
    buckets: dict[int, list[str]] = defaultdict(list)
    for node, label in zip(csr.nodes, labels, strict=True):
        buckets[int(label)].append(node)
    groups: list[list[str]] = []
    for members in buckets.values():
        if len(members) >= 2:
            groups.append(members)
            continue
        if len(members) == 1:
            idx = csr.node_index[members[0]]
            if csr.matrix[idx, idx] != 0:
                groups.append(members)
    return groups
