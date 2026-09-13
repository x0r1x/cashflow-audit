from __future__ import annotations

from cashflow_audit.checkers.run import run_checks
from cashflow_audit.compile.models import Edge
from cashflow_audit.layout.models import LayoutRow
from cashflow_audit.mapping.models import MappingDocument
from cashflow_audit.series.models import SeriesOutlier
from tests.checkers.conftest import cell, ctx, headers, mapped, simple_layout


def _detectors(result, name: str) -> list:
    return [c for c in result.candidates if c.detector == name]


def test_excel_error_positive_and_clean_negative() -> None:
    dirty = ctx([cell("S", "A1", "#REF!", formula="B1")])
    clean = ctx([cell("S", "A1", "10", formula="B1")])
    assert _detectors(run_checks(dirty), "excel_error")
    assert not _detectors(run_checks(clean), "excel_error")


def test_error_masking_iferror_hides_error() -> None:
    masked = ctx([cell("S", "A1", "0", formula="IFERROR(B1,0)")])
    shown = ctx([cell("S", "A1", "#DIV/0!", formula="A1/B1")])
    assert _detectors(run_checks(masked), "error_masking")
    assert not _detectors(run_checks(shown), "error_masking")


def test_circular_scc_and_no_cycle_negative() -> None:
    cyc = ctx(
        [cell("S", "A1", "1", formula="B1"), cell("S", "B1", "1", formula="A1")],
        edges=[
            Edge(kind="ref", source="S!A1", target="S!B1"),
            Edge(kind="ref", source="S!B1", target="S!A1"),
        ],
    )
    acyc = ctx(
        [cell("S", "A1", "1", formula="B1"), cell("S", "B1", "1")],
        edges=[Edge(kind="ref", source="S!A1", target="S!B1")],
    )
    assert _detectors(run_checks(cyc), "circular")
    assert not _detectors(run_checks(acyc), "circular")


def test_circular_interest_is_likely_intentional_warning() -> None:
    result = run_checks(
        ctx(
            [cell("S", "A1", "1", formula="B1"), cell("S", "B1", "1", formula="A1")],
            edges=[
                Edge(kind="ref", source="S!A1", target="S!B1"),
                Edge(kind="ref", source="S!B1", target="S!A1"),
            ],
            mapping=MappingDocument(
                rows=[mapped("S", 1, "Interest", "pnl.interest", role="calculation")]
            ),
            layout=simple_layout([LayoutRow(row=1, label="Interest")], sheet="S"),
        )
    )
    found = _detectors(result, "circular")
    assert found
    assert "likely_intentional" in found[0].tags
    assert found[0].base_severity == "warning"


def test_iterate_flag_is_not_a_circular_finding() -> None:
    result = run_checks(
        ctx(
            [cell("S", "A1", "1")],
            workbook={
                "has_vba": False,
                "has_xlm": False,
                "externals": [],
                "iterate": True,
            },
        )
    )
    assert not _detectors(result, "circular")


def test_unresolved_dynamic_positive_and_negative() -> None:
    dirty = ctx(
        [cell("S", "A1", "1", formula='INDIRECT("B1")')],
        edges=[Edge(kind="dynamic", source="S!A1", target=None, unresolved=True)],
    )
    clean = ctx([cell("S", "A1", "1", formula="B1")])
    assert _detectors(run_checks(dirty), "unresolved_dynamic")
    assert not _detectors(run_checks(clean), "unresolved_dynamic")


def test_external_link_positive_and_negative() -> None:
    dirty = ctx(
        [cell("S", "A1", "1")],
        workbook={
            "has_vba": False,
            "has_xlm": False,
            "externals": ["other.xlsx"],
            "iterate": False,
        },
    )
    clean = ctx([cell("S", "A1", "1")])
    assert _detectors(run_checks(dirty), "external_link")
    assert not _detectors(run_checks(clean), "external_link")


def test_xlm_or_vba_one_candidate_per_book() -> None:
    dirty = ctx(
        [cell("S", "A1", "1"), cell("S", "B1", "2")],
        workbook={"has_vba": True, "has_xlm": False, "externals": [], "iterate": False},
    )
    clean = ctx([cell("S", "A1", "1")])
    found = _detectors(run_checks(dirty), "xlm_or_vba")
    assert len(found) == 1
    assert not _detectors(run_checks(clean), "xlm_or_vba")


def test_series_kinds_become_candidates_edge_period_frozen() -> None:
    outlier = SeriesOutlier(
        sheet="P&L",
        block_id="P&L!r1",
        row=2,
        col=3,
        addr="C2",
        cell_ref="P&L!C2",
        kind="template_change",
        edge_period=True,
        majority_template="=R[0]C[-1]",
        cell_template="=SUM(R[0]C[-1])",
    )
    hist = SeriesOutlier(
        sheet="P&L",
        block_id="P&L!r1",
        row=2,
        col=2,
        addr="B2",
        cell_ref="P&L!B2",
        kind="value_instead_of_formula",
        edge_period=False,
        majority_template="=R[0]C[-1]",
        cell_template=None,
    )
    result = run_checks(
        ctx(
            [cell("P&L", "B2", "1"), cell("P&L", "C2", "1")],
            layout=simple_layout(
                [LayoutRow(row=2, label="Revenue")],
                axis=headers((2, "2023", "historical"), (3, "2024E", "forecast")),
            ),
            series=[outlier, hist],
        )
    )
    pattern = _detectors(result, "pattern_break")
    assert pattern
    assert "edge_period" in pattern[0].tags
    assert pattern[0].base_severity == "warning"
    values = _detectors(result, "value_instead_of_formula")
    assert values
    assert "hist_manual_adjustment" in values[0].tags


def test_agg_range_gap_positive_and_full_range_negative() -> None:
    layout = simple_layout(
        [
            LayoutRow(row=2, label="Rev"),
            LayoutRow(row=3, label="Opex"),
            LayoutRow(row=4, label="Other"),
            LayoutRow(row=5, label="Total"),
        ]
    )
    gap = ctx(
        [
            cell("P&L", "B2", "1"),
            cell("P&L", "B3", "1"),
            cell("P&L", "B4", "1"),
            cell("P&L", "B5", "2", formula="SUM(B2:B3)"),
        ],
        layout=layout,
    )
    full = ctx(
        [
            cell("P&L", "B2", "1"),
            cell("P&L", "B3", "1"),
            cell("P&L", "B4", "1"),
            cell("P&L", "B5", "3", formula="SUM(B2:B4)"),
        ],
        layout=layout,
    )
    assert _detectors(run_checks(gap), "agg_range_gap")
    assert not _detectors(run_checks(full), "agg_range_gap")


def test_agg_double_count_subtotal_in_parent_sum_is_error() -> None:
    layout = simple_layout(
        [
            LayoutRow(row=2, label="Staff"),
            LayoutRow(row=3, label="Rent"),
            LayoutRow(row=4, label="OPEX"),
            LayoutRow(row=5, label="Total"),
        ]
    )
    result = run_checks(
        ctx(
            [
                cell("P&L", "B2", "1"),
                cell("P&L", "B3", "1"),
                cell("P&L", "B4", "2", formula="SUM(B2:B3)"),
                cell("P&L", "B5", "4", formula="SUM(B2:B4)"),
            ],
            layout=layout,
        )
    )
    found = _detectors(result, "agg_double_count")
    assert found
    assert found[0].base_severity == "error"
    staff = next(item for item in found if "P&L!B2" in item.cell_refs)
    assert "P&L!B4" in staff.cell_refs
    assert "P&L!B5" in staff.cell_refs


def test_agg_double_count_disjoint_sums_is_silent() -> None:
    layout = simple_layout(
        [
            LayoutRow(row=2, label="COGS a"),
            LayoutRow(row=3, label="COGS b"),
            LayoutRow(row=4, label="COGS"),
            LayoutRow(row=5, label="OPEX a"),
            LayoutRow(row=6, label="OPEX b"),
            LayoutRow(row=7, label="OPEX"),
        ]
    )
    result = run_checks(
        ctx(
            [
                cell("P&L", "B2", "1"),
                cell("P&L", "B3", "1"),
                cell("P&L", "B4", "2", formula="SUM(B2:B3)"),
                cell("P&L", "B5", "1"),
                cell("P&L", "B6", "1"),
                cell("P&L", "B7", "2", formula="SUM(B5:B6)"),
            ],
            layout=layout,
        )
    )
    assert not _detectors(result, "agg_double_count")


def test_agg_double_count_check_row_same_range_is_silent() -> None:
    layout = simple_layout(
        [
            LayoutRow(row=2, label="Staff"),
            LayoutRow(row=3, label="Rent"),
            LayoutRow(row=4, label="OPEX"),
            LayoutRow(row=5, label="Check", check_row=True),
        ]
    )
    result = run_checks(
        ctx(
            [
                cell("P&L", "B2", "1"),
                cell("P&L", "B3", "1"),
                cell("P&L", "B4", "2", formula="SUM(B2:B3)"),
                cell("P&L", "B5", "2", formula="SUM(B2:B3)"),
            ],
            layout=layout,
        )
    )
    assert not _detectors(result, "agg_double_count")


def test_unused_cell_positive_and_used_negative() -> None:
    mapping = MappingDocument(
        rows=[mapped("P&L", 2, "EBITDA", "pnl.ebitda", role="output")]
    )
    unused = ctx(
        [cell("P&L", "B2", "1", formula="C2"), cell("P&L", "C2", "1"), cell("P&L", "D2", "9")],
        edges=[Edge(kind="ref", source="P&L!B2", target="P&L!C2")],
        mapping=mapping,
        layout=simple_layout([LayoutRow(row=2, label="EBITDA")]),
    )
    used = ctx(
        [cell("P&L", "B2", "1", formula="C2"), cell("P&L", "C2", "1")],
        edges=[Edge(kind="ref", source="P&L!B2", target="P&L!C2")],
        mapping=mapping,
        layout=simple_layout([LayoutRow(row=2, label="EBITDA")]),
    )
    assert _detectors(run_checks(unused), "unused_cell")
    assert not _detectors(run_checks(used), "unused_cell")


def test_hidden_input_in_visible_output_and_tag_only_otherwise() -> None:
    mapping = MappingDocument(
        rows=[mapped("P&L", 2, "EBITDA", "pnl.ebitda", role="output")]
    )
    layout = simple_layout([LayoutRow(row=2, label="EBITDA")])
    used_hidden = ctx(
        [
            cell("P&L", "B2", "1", formula="C2"),
            cell("P&L", "C2", "5", hidden=True),
        ],
        edges=[Edge(kind="ref", source="P&L!B2", target="P&L!C2")],
        mapping=mapping,
        layout=layout,
    )
    unused_hidden = ctx(
        [
            cell("P&L", "B2", "1", formula="A2"),
            cell("P&L", "C2", "5", hidden=True),
        ],
        edges=[Edge(kind="ref", source="P&L!B2", target="P&L!A2")],
        mapping=mapping,
        layout=layout,
    )
    assert _detectors(run_checks(used_hidden), "hidden_input")
    assert not _detectors(run_checks(unused_hidden), "hidden_input")


def test_scenario_switch_live_pnl_and_debt_disagree() -> None:
    mapping = MappingDocument(
        rows=[
            mapped("P&L", 2, "Rev", "pnl.revenue", role="output"),
            mapped("BS", 2, "Debt", "bs.debt", role="output"),
        ]
    )
    result = run_checks(
        ctx(
            [
                cell("P&L", "B2", "1", formula="Base!B2"),
                cell("BS", "B2", "1", formula="Upside!B2"),
                cell("Base", "B2", "10"),
                cell("Upside", "B2", "20"),
            ],
            edges=[
                Edge(kind="cross_sheet", source="P&L!B2", target="Base!B2"),
                Edge(kind="cross_sheet", source="BS!B2", target="Upside!B2"),
            ],
            mapping=mapping,
        )
    )
    found = _detectors(result, "scenario_switch")
    assert found
    assert found[0].base_severity == "error"
    assert "P&L!B2" in found[0].cell_refs
    assert "BS!B2" in found[0].cell_refs


def test_scenario_switch_same_source_is_silent() -> None:
    mapping = MappingDocument(
        rows=[
            mapped("P&L", 2, "Rev", "pnl.revenue", role="output"),
            mapped("BS", 2, "Debt", "bs.debt", role="output"),
        ]
    )
    result = run_checks(
        ctx(
            [
                cell("P&L", "B2", "1", formula="Base!B2"),
                cell("BS", "B2", "1", formula="Base!B2"),
                cell("Base", "B2", "10"),
            ],
            edges=[
                Edge(kind="cross_sheet", source="P&L!B2", target="Base!B2"),
                Edge(kind="cross_sheet", source="BS!B2", target="Base!B2"),
            ],
            mapping=mapping,
        )
    )
    assert not _detectors(result, "scenario_switch")


def test_scenario_switch_full_copies_are_silent() -> None:
    mapping = MappingDocument(
        rows=[
            mapped("Base", 2, "Rev", "pnl.revenue", role="output"),
            mapped("Upside", 2, "Debt", "bs.debt", role="output"),
        ]
    )
    result = run_checks(
        ctx(
            [cell("Base", "B2", "10"), cell("Upside", "B2", "20")],
            mapping=mapping,
        )
    )
    assert not _detectors(result, "scenario_switch")


def test_dedup_same_detector_and_refs() -> None:
    result = run_checks(
        ctx(
            [cell("S", "A1", "#REF!", formula="B1"), cell("S", "A1", "#REF!", formula="B1")],
        )
    )
    assert len(_detectors(result, "excel_error")) == 1


def test_no_fcf_or_dscr_detectors() -> None:
    result = run_checks(ctx([cell("S", "A1", "1")]))
    names = {c.detector.lower() for c in result.candidates}
    assert not any("fcf" in n or "dscr" in n for n in names)
