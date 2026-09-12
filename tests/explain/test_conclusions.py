from __future__ import annotations

from cashflow_audit.checkers.models import Candidate, CheckDocument
from cashflow_audit.explain.compose import compose_report
from cashflow_audit.explain.models import Report
from cashflow_audit.lineage.models import Impact, LineageDocument, LineageItem
from cashflow_audit.mapping.models import MappingDocument, MappingQuestion
from tests.helpers.ports import FakeChat, GrantSlots

EMPTY_HEADLINE = (
    "По включённым проверкам явных разрывов не найдено. Полнота не гарантируется."
)
DYNAMICS_HEADLINE = "По равенствам явных разрывов нет; есть сигналы риска."
MAPPING_NOTE = "Маппинг неполный, равенства могут молчать."


def _cand(**kwargs) -> Candidate:
    base: dict = {
        "detector": "excel_error",
        "cell_refs": ["P&L!B2"],
        "tags": [],
        "payload": {},
        "base_severity": "error",
    }
    base.update(kwargs)
    return Candidate.model_validate(base)


def _lin(index: int = 0, **kwargs) -> LineageItem:
    base: dict = {
        "candidate_index": index,
        "detector": "excel_error",
        "cell_refs": ["P&L!B2"],
        "output_refs": ["P&L!C3"],
        "affected_metrics": ["pnl.ebitda"],
        "impact": Impact(kind="direction", value="downstream"),
        "truncated": False,
        "complete": True,
        "path_refs": ["P&L!B2", "P&L!C3"],
    }
    base.update(kwargs)
    return LineageItem.model_validate(base)


def _report(
    candidates: list[Candidate],
    items: list[LineageItem],
    *,
    mapping: MappingDocument | None = None,
    check: CheckDocument | None = None,
    chat: FakeChat | None = None,
) -> Report:
    refs: set[str] = set()
    for cand in candidates:
        refs.update(cand.cell_refs)
    for item in items:
        refs.update(item.cell_refs)
        refs.update(item.output_refs)
        refs.update(item.path_refs)
    return compose_report(
        candidates=candidates,
        lineage=LineageDocument(items=items),
        mapping=mapping or MappingDocument(),
        check=check or CheckDocument(),
        ir_refs=refs,
        chat=chat,
        slots=GrantSlots(),
    )


def test_hardcode_and_ebitda_drop_is_combo() -> None:
    hard = _cand(
        detector="literal_in_ast",
        cell_refs=["P&L!D24"],
        payload={"kind": "literal_in_ast"},
        base_severity="warning",
    )
    drop = _cand(
        detector="frs.F02",
        cell_refs=["P&L!D20"],
        payload={"col": 4, "change": -0.2},
        base_severity="risk",
    )
    report = _report(
        [hard, drop],
        [
            _lin(
                0,
                detector="literal_in_ast",
                cell_refs=["P&L!D24"],
                path_refs=["P&L!D24", "P&L!C3"],
            ),
            _lin(
                1,
                detector="frs.F02",
                cell_refs=["P&L!D20"],
                output_refs=["P&L!D20"],
                path_refs=["P&L!D20"],
            ),
        ],
    )
    combos = [c for c in report.conclusions if c.kind == "combo"]
    assert len(combos) == 1
    combo = combos[0]
    assert set(combo.finding_ids) == {report.findings[0].id, report.findings[1].id}
    assert set(combo.cell_refs) <= {"P&L!D24", "P&L!D20", "P&L!C3"}
    assert set(combo.cell_refs) >= {"P&L!D24", "P&L!D20"}
    assert "pnl.ebitda" in combo.metrics
    blob = f"{combo.title} {combo.body}".casefold()
    assert "ручн" in blob or "подстанов" in blob
    rec = combo.recommendation.casefold()
    assert "не изменён" in rec or "не изменен" in rec
    assert not any(c.kind == "dynamics" for c in report.conclusions)


def test_identity_i1_two_periods_collapse_to_one_trust() -> None:
    a = _cand(
        detector="identity.I1",
        cell_refs=["BS!C27"],
        payload={"delta": 10.0, "col": 3},
        base_severity="error",
    )
    b = _cand(
        detector="identity.I1",
        cell_refs=["BS!D27"],
        payload={"delta": 20.0, "col": 4},
        base_severity="error",
    )
    report = _report(
        [a, b],
        [
            _lin(
                0,
                detector="identity.I1",
                cell_refs=["BS!C27"],
                output_refs=["BS!C27"],
                affected_metrics=["bs.assets_total", "bs.equity", "bs.liabilities"],
                path_refs=["BS!C27"],
            ),
            _lin(
                1,
                detector="identity.I1",
                cell_refs=["BS!D27"],
                output_refs=["BS!D27"],
                affected_metrics=["bs.assets_total", "bs.equity", "bs.liabilities"],
                path_refs=["BS!D27"],
            ),
        ],
    )
    trusts = [c for c in report.conclusions if c.kind == "trust"]
    assert len(trusts) == 1
    assert set(trusts[0].finding_ids) == {report.findings[0].id, report.findings[1].id}
    assert "2" in trusts[0].body
    assert trusts[0].severity == "error"


def test_i1_and_cash_negative_same_col_is_combo() -> None:
    i1 = _cand(
        detector="identity.I1",
        cell_refs=["BS!E27"],
        payload={"delta": 1200.0, "col": 5},
        base_severity="error",
    )
    cash = _cand(
        detector="frs.F08",
        cell_refs=["BS!E10"],
        payload={"col": 5},
        base_severity="risk",
    )
    report = _report(
        [i1, cash],
        [
            _lin(
                0,
                detector="identity.I1",
                cell_refs=["BS!E27"],
                output_refs=["BS!E27"],
                affected_metrics=["bs.assets_total", "bs.equity", "bs.liabilities"],
                path_refs=["BS!E27"],
            ),
            _lin(
                1,
                detector="frs.F08",
                cell_refs=["BS!E10"],
                output_refs=["BS!E10"],
                affected_metrics=["bs.cash"],
                path_refs=["BS!E10"],
            ),
        ],
    )
    combos = [c for c in report.conclusions if c.kind == "combo"]
    assert len(combos) == 1
    assert set(combos[0].finding_ids) == {report.findings[0].id, report.findings[1].id}
    assert combos[0].severity == "error"
    blob = f"{combos[0].title} {combos[0].body}".casefold()
    assert "финанс" in blob or "касс" in blob or "денеж" in blob
    assert not any(c.kind == "dynamics" for c in report.conclusions)
    assert not any(c.kind == "trust" for c in report.conclusions)


def test_external_link_and_metric_is_combo() -> None:
    ext = _cand(
        detector="external_link",
        cell_refs=["Inputs!B2"],
        payload={"externals": ["old.xlsx"]},
        base_severity="warning",
    )
    drop = _cand(
        detector="frs.F02",
        cell_refs=["P&L!D20"],
        payload={"col": 4, "change": -0.2},
        base_severity="risk",
    )
    report = _report(
        [ext, drop],
        [
            _lin(
                0,
                detector="external_link",
                cell_refs=["Inputs!B2"],
                output_refs=["P&L!C3"],
                path_refs=["Inputs!B2", "P&L!C3"],
            ),
            _lin(
                1,
                detector="frs.F02",
                cell_refs=["P&L!D20"],
                output_refs=["P&L!C3"],
                path_refs=["Inputs!B2", "P&L!D20", "P&L!C3"],
            ),
        ],
    )
    combos = [c for c in report.conclusions if c.kind == "combo"]
    assert len(combos) == 1
    assert "внешн" in combos[0].title.casefold() or "внешн" in combos[0].body.casefold()
    assert set(combos[0].finding_ids) == {report.findings[0].id, report.findings[1].id}


def test_ebitda_drop_without_hardcode_is_dynamics() -> None:
    drop = _cand(
        detector="frs.F02",
        cell_refs=["P&L!D20"],
        payload={"col": 4, "change": -0.2},
        base_severity="risk",
    )
    report = _report(
        [drop],
        [
            _lin(
                0,
                detector="frs.F02",
                cell_refs=["P&L!D20"],
                output_refs=["P&L!D20"],
                path_refs=["P&L!D20"],
            )
        ],
    )
    assert [c.kind for c in report.conclusions] == ["dynamics"]
    assert report.conclusions[0].finding_ids == [report.findings[0].id]
    assert report.summary.headline == DYNAMICS_HEADLINE


def test_hardcode_on_other_metric_is_not_combo() -> None:
    hard = _cand(
        detector="literal_in_ast",
        cell_refs=["P&L!D5"],
        payload={"kind": "literal_in_ast"},
        base_severity="warning",
    )
    drop = _cand(
        detector="frs.F02",
        cell_refs=["P&L!D20"],
        payload={"col": 4, "change": -0.2},
        base_severity="risk",
    )
    report = _report(
        [hard, drop],
        [
            _lin(
                0,
                detector="literal_in_ast",
                cell_refs=["P&L!D5"],
                output_refs=["P&L!D5"],
                affected_metrics=["pnl.revenue"],
                path_refs=["P&L!D5"],
            ),
            _lin(
                1,
                detector="frs.F02",
                cell_refs=["P&L!D20"],
                output_refs=["P&L!D20"],
                affected_metrics=["pnl.ebitda"],
                path_refs=["P&L!D20"],
            ),
        ],
    )
    assert not any(c.kind == "combo" for c in report.conclusions)
    assert any(c.kind == "dynamics" for c in report.conclusions)


def test_hist_hardcode_and_forecast_drop_is_not_combo() -> None:
    hard = _cand(
        detector="literal_in_ast",
        cell_refs=["P&L!D24"],
        tags=["hist_manual_adjustment"],
        payload={"kind": "literal_in_ast"},
        base_severity="warning",
    )
    drop = _cand(
        detector="frs.F02",
        cell_refs=["P&L!D20"],
        payload={"col": 4, "change": -0.2},
        base_severity="risk",
    )
    report = _report(
        [hard, drop],
        [
            _lin(
                0,
                detector="literal_in_ast",
                cell_refs=["P&L!D24"],
                path_refs=["P&L!D24", "P&L!C3"],
            ),
            _lin(
                1,
                detector="frs.F02",
                cell_refs=["P&L!D20"],
                output_refs=["P&L!D20"],
                path_refs=["P&L!D20"],
            ),
        ],
    )
    assert not any(c.kind == "combo" for c in report.conclusions)
    assert any(c.kind == "dynamics" for c in report.conclusions)


def test_empty_findings_headline_without_llm() -> None:
    chat = FakeChat(
        payload={"title": "LLM", "evidence": "e", "recommendation": "r", "cited_refs": []}
    )
    report = _report([], [], chat=chat)
    assert report.conclusions == []
    assert report.summary.headline == EMPTY_HEADLINE
    assert chat.calls == 0
    assert report.llm_used is False


def test_conclusion_refs_are_subset_of_cited_findings() -> None:
    hard = _cand(
        detector="literal_in_ast",
        cell_refs=["P&L!D24"],
        payload={"kind": "literal_in_ast"},
        base_severity="warning",
    )
    drop = _cand(
        detector="frs.F02",
        cell_refs=["P&L!D20"],
        payload={"col": 4},
        base_severity="risk",
    )
    report = _report(
        [hard, drop],
        [
            _lin(
                0,
                detector="literal_in_ast",
                cell_refs=["P&L!D24"],
                path_refs=["P&L!D24", "P&L!C3"],
            ),
            _lin(
                1,
                detector="frs.F02",
                cell_refs=["P&L!D20"],
                output_refs=["P&L!D20"],
                path_refs=["P&L!D20"],
            ),
        ],
    )
    by_id = {f.id: f for f in report.findings}
    for conclusion in report.conclusions:
        cited = set()
        for fid in conclusion.finding_ids:
            cited.update(by_id[fid].cell_refs)
        assert conclusion.finding_ids
        assert conclusion.cell_refs
        assert set(conclusion.cell_refs) <= cited


def test_needs_input_appends_mapping_note() -> None:
    q = MappingQuestion(
        id="q_001",
        kind="mapping",
        prompt="x",
        cell_refs=["P&L!A2"],
        options=["pnl.revenue", "unknown"],
    )
    report = _report([], [], mapping=MappingDocument(questions=[q]))
    assert report.status == "needs_input"
    assert report.conclusions == []
    assert EMPTY_HEADLINE in report.summary.headline
    assert MAPPING_NOTE in report.summary.headline


def test_unused_cell_is_not_a_conclusion() -> None:
    unused = _cand(
        detector="unused_cell",
        cell_refs=["P&L!Z9"],
        base_severity="warning",
    )
    report = _report(
        [unused],
        [
            _lin(
                0,
                detector="unused_cell",
                cell_refs=["P&L!Z9"],
                output_refs=[],
                affected_metrics=[],
                path_refs=["P&L!Z9"],
            )
        ],
    )
    assert report.findings
    assert report.conclusions == []
