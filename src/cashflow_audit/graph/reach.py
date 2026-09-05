from __future__ import annotations

from collections.abc import Iterable

from cashflow_audit.compile.models import CsrGraph


def reachable_from(csr: CsrGraph, starts: Iterable[str]) -> set[str]:
    seen: set[str] = set()
    stack = [node for node in starts if node in csr.node_index]
    while stack:
        node = stack.pop()
        if node in seen:
            continue
        seen.add(node)
        row = csr.matrix.getrow(csr.node_index[node])
        for idx in row.indices:
            stack.append(csr.nodes[int(idx)])
    return seen
