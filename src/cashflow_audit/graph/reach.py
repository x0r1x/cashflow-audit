from __future__ import annotations

from collections.abc import Iterable

from cashflow_audit.compile.models import CsrGraph


def reverse_reachable_from(csr: CsrGraph, starts: Iterable[str]) -> set[str]:
    """Walk formulas that use `starts` (transpose of formula→dependency CSR)."""
    start_list = list(starts)
    if not csr.nodes:
        return set(start_list)
    transpose = csr.matrix.transpose().tocsr()
    seen: set[str] = set()
    stack = [node for node in start_list if node in csr.node_index]
    while stack:
        node = stack.pop()
        if node in seen:
            continue
        seen.add(node)
        row = transpose.getrow(csr.node_index[node])
        for idx in row.indices:
            stack.append(csr.nodes[int(idx)])
    seen.update(node for node in start_list if node not in csr.node_index)
    return seen


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
