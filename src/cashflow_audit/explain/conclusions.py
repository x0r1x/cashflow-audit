from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Literal

from cashflow_audit.checkers.models import Candidate
from cashflow_audit.explain.models import Conclusion, Finding
from cashflow_audit.explain.template import card_title
from cashflow_audit.lineage.models import LineageItem
from cashflow_audit.parse.a1 import parse_addr

Kind = Literal["trust", "combo", "dynamics"]

MAX_CONCLUSIONS = 8
SKIP_DETECTORS = frozenset({"unused_cell", "xlm_or_vba"})
HARDCODE = frozenset({"literal_in_ast", "value_instead_of_formula", "pattern_break"})
TRUST_DETECTORS = frozenset(
    {
        "identity.I1",
        "identity.I3a",
        "identity.I3b",
        "identity.I5",
        "identity.I7",
        "identity.I9",
        "identity.I10",
        "identity.I11",
        "identity.I12",
        "identity.I8",
        "excel_error",
        "error_masking",
        "circular",
        "agg_range_gap",
        "agg_double_count",
        "hidden_input",
        "external_link",
    }
)
_SEV_RANK = {"error": 0, "warning": 1, "risk": 2}
_FILE_UNCHANGED = "Проверить указанные ячейки. Файл не изменён."
EMPTY_HEADLINE = (
    "По включённым проверкам явных разрывов не найдено. Полнота не гарантируется."
)
DYNAMICS_HEADLINE = "По равенствам явных разрывов нет; есть сигналы риска."
MAPPING_NOTE = "Маппинг неполный, равенства могут молчать."


@dataclass(frozen=True)
class _Row:
    finding: Finding
    candidate: Candidate
    lineage: LineageItem

    @property
    def cols(self) -> frozenset[int]:
        found: set[int] = set()
        raw = self.candidate.payload.get("col")
        if isinstance(raw, int):
            found.add(raw)
        for ref in self.finding.cell_refs:
            col = _col_from_ref(ref)
            if col is not None:
                found.add(col)
        return frozenset(found)

    @property
    def metrics(self) -> frozenset[str]:
        return frozenset(self.finding.affected_metrics)

    @property
    def outputs(self) -> frozenset[str]:
        return frozenset(self.lineage.output_refs)

    @property
    def path(self) -> frozenset[str]:
        return frozenset(self.lineage.path_refs)


def build_conclusions(
    findings: Sequence[Finding],
    candidates: Sequence[Candidate],
    lineage: Sequence[LineageItem],
) -> list[Conclusion]:
    rows = [
        _Row(finding=finding, candidate=cand, lineage=item)
        for finding, cand, item in zip(findings, candidates, lineage, strict=False)
    ]
    used: set[str] = set()
    out: list[Conclusion] = []
    for builder in (_hardcode_ebitda, _balance_cash, _external_metric):
        available = [row for row in rows if row.finding.id not in used]
        conclusion, picked = builder(available)
        if conclusion is None:
            continue
        out.append(conclusion)
        used.update(picked)
    remaining = [
        row
        for row in rows
        if row.finding.id not in used and row.finding.detector not in SKIP_DETECTORS
    ]
    out.extend(_grouped(remaining, kind="trust", accept=_is_trust))
    out.extend(_grouped(remaining, kind="dynamics", accept=_is_risk))
    capped = out[:MAX_CONCLUSIONS]
    for index, item in enumerate(capped, start=1):
        item.id = f"c_{index:03d}"
    return capped


def headline_for(
    conclusions: Sequence[Conclusion],
    findings: Sequence[Finding],
    questions: Sequence[object],
) -> str:
    if not findings:
        text = EMPTY_HEADLINE
    elif any(item.kind in {"combo", "trust"} for item in conclusions):
        titles = [item.title for item in conclusions if item.kind in {"combo", "trust"}][:2]
        text = "; ".join(titles)
    elif any(item.kind == "dynamics" for item in conclusions):
        text = DYNAMICS_HEADLINE
    else:
        text = EMPTY_HEADLINE
    if questions:
        text = f"{text} {MAPPING_NOTE}"
    return text


def _hardcode_ebitda(rows: Sequence[_Row]) -> tuple[Conclusion | None, set[str]]:
    picked = _pair(
        rows,
        left_dets=HARDCODE,
        right_dets=frozenset({"frs.F02"}),
        ok=_hardcode_ok,
    )
    if not picked:
        return None, set()
    refs = _ref_text(picked)
    return (
        _emit(
            kind="combo",
            rows=picked,
            title="Снижение EBITDA зависит от ручной подстановки",
            body=(
                f"В формуле зашита константа ({refs}); одновременно падает EBITDA/маржа. "
                "Динамику нельзя читать как факт модели."
            ),
            recommendation="Сверить константу с блоком предпосылок. Файл не изменён.",
        ),
        {row.finding.id for row in picked},
    )


def _balance_cash(rows: Sequence[_Row]) -> tuple[Conclusion | None, set[str]]:
    picked = _pair(
        rows,
        left_dets=frozenset({"identity.I1"}),
        right_dets=frozenset({"frs.F08"}),
        ok=_period_ok,
    )
    if not picked:
        return None, set()
    refs = _ref_text(picked)
    return (
        _emit(
            kind="combo",
            rows=picked,
            title="Потребность в финансировании недостоверна",
            body=f"Баланс не сходится и это влияет на денежные средства ({refs}).",
            recommendation=_FILE_UNCHANGED,
        ),
        {row.finding.id for row in picked},
    )


def _external_metric(rows: Sequence[_Row]) -> tuple[Conclusion | None, set[str]]:
    lefts = [row for row in rows if row.finding.detector == "external_link"]
    rights = [
        row
        for row in rows
        if row.finding.detector != "external_link"
        and row.finding.detector not in SKIP_DETECTORS
    ]
    picked: list[_Row] = []
    seen: set[str] = set()
    for left in lefts:
        for right in rights:
            if not _external_ok(left, right):
                continue
            for row in (left, right):
                if row.finding.id not in seen:
                    seen.add(row.finding.id)
                    picked.append(row)
    if len(seen) < 2:
        return None, set()
    refs = _ref_text(picked)
    return (
        _emit(
            kind="combo",
            rows=picked,
            title="Результат зависит от внешней предпосылки",
            body=(
                f"Финансовый результат зависит от внешней ссылки, "
                f"которую нельзя подтвердить в этом файле ({refs})."
            ),
            recommendation=_FILE_UNCHANGED,
        ),
        seen,
    )


def _grouped(
    rows: Sequence[_Row],
    *,
    kind: Kind,
    accept: Callable[[_Row], bool],
) -> list[Conclusion]:
    buckets: dict[str, list[_Row]] = {}
    order: list[str] = []
    for row in rows:
        if not accept(row):
            continue
        detector = row.finding.detector
        if detector not in buckets:
            order.append(detector)
            buckets[detector] = []
        buckets[detector].append(row)
    found: list[Conclusion] = []
    for detector in order:
        group = buckets[detector]
        refs = _ref_text(group)
        metrics = ", ".join(_metrics(group)) or "—"
        if kind == "trust" and detector.startswith("identity."):
            body = (
                f"Контрольное равенство нарушено в {len(group)} периодах. "
                "Метрики этих периодов недостоверны, пока разрыв не объяснён."
            )
            rec = _FILE_UNCHANGED
        elif kind == "trust":
            body = (
                f"Расчёт метрик {metrics} опирается на сломанную или скрытую "
                f"ячейку ({refs})."
            )
            rec = _FILE_UNCHANGED
        else:
            body = (
                f"По равенствам эта метрика в этом прогоне не опровергнута; "
                f"наблюдается сигнал {detector} ({refs})."
            )
            rec = _FILE_UNCHANGED
        item = _emit(
            kind=kind,
            rows=group,
            title=card_title(detector),
            body=body,
            recommendation=rec,
        )
        if item is not None:
            found.append(item)
    return found


def _pair(
    rows: Sequence[_Row],
    *,
    left_dets: frozenset[str],
    right_dets: frozenset[str],
    ok: Callable[[_Row, _Row], bool],
) -> list[_Row]:
    lefts = [row for row in rows if row.finding.detector in left_dets]
    rights = [row for row in rows if row.finding.detector in right_dets]
    picked: list[_Row] = []
    seen: set[str] = set()
    for left in lefts:
        for right in rights:
            if not ok(left, right):
                continue
            for row in (left, right):
                if row.finding.id not in seen:
                    seen.add(row.finding.id)
                    picked.append(row)
    if not any(row.finding.detector in left_dets for row in picked):
        return []
    if not any(row.finding.detector in right_dets for row in picked):
        return []
    return picked


def _hardcode_ok(left: _Row, right: _Row) -> bool:
    if "hist_manual_adjustment" in left.finding.tags:
        return False
    if not _period_ok(left, right):
        return False
    return bool(left.metrics & right.metrics or left.outputs & right.outputs)


def _period_ok(left: _Row, right: _Row) -> bool:
    if not left.cols or not right.cols:
        return True
    return bool(left.cols & right.cols)


def _external_ok(left: _Row, right: _Row) -> bool:
    if left.metrics & right.metrics:
        return True
    if left.outputs & right.outputs:
        return True
    return bool(set(left.finding.cell_refs) & right.path)


def _is_trust(row: _Row) -> bool:
    detector = row.finding.detector
    if detector in TRUST_DETECTORS:
        return True
    return detector in HARDCODE and bool(row.metrics)


def _is_risk(row: _Row) -> bool:
    return row.finding.detector.startswith("frs.")


def _emit(
    *,
    kind: Kind,
    rows: Sequence[_Row],
    title: str,
    body: str,
    recommendation: str,
) -> Conclusion | None:
    finding_ids = [row.finding.id for row in rows]
    refs: list[str] = []
    seen_refs: set[str] = set()
    for row in rows:
        for ref in row.finding.cell_refs:
            if ref in seen_refs:
                continue
            seen_refs.add(ref)
            refs.append(ref)
    if not finding_ids or not refs:
        return None
    worst = min(rows, key=lambda row: _SEV_RANK.get(row.finding.severity, 9))
    return Conclusion(
        id="c_tmp",
        kind=kind,
        severity=worst.finding.severity,
        metrics=_metrics(rows),
        title=title,
        body=body,
        finding_ids=finding_ids,
        cell_refs=refs,
        recommendation=recommendation,
    )


def _metrics(rows: Sequence[_Row]) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for metric in row.finding.affected_metrics:
            if metric in seen:
                continue
            seen.add(metric)
            found.append(metric)
    return found


def _ref_text(rows: Sequence[_Row]) -> str:
    refs: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for ref in row.finding.cell_refs:
            if ref in seen:
                continue
            seen.add(ref)
            refs.append(ref)
    return ", ".join(refs) or "—"


def _col_from_ref(ref: str) -> int | None:
    addr = ref.rsplit("!", 1)[-1].replace("$", "")
    try:
        col, _row = parse_addr(addr)
    except ValueError:
        return None
    return col
