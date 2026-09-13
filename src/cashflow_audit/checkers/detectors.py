from __future__ import annotations

from cashflow_audit.checkers.astutil import ERROR_TOKENS, as_number, has_func, parse_ast, sum_ranges
from cashflow_audit.checkers.context import CheckContext
from cashflow_audit.checkers.models import Candidate
from cashflow_audit.compile.csr import expand_range, split_sheet_ref
from cashflow_audit.graph.reach import reachable_from
from cashflow_audit.graph.scc import circular_groups
from cashflow_audit.layout.periods import classify_header
from cashflow_audit.parse.a1 import format_addr, parse_addr

INTENTIONAL_CONCEPTS = {"pnl.interest", "pnl.tax", "bs.debt"}
INTENTIONAL_LABELS = ("interest", "tax", "sweep", "процент", "налог")
KIND_TO_DETECTOR = {
    "template_change": "pattern_break",
    "ref_shift": "pattern_break",
    "value_instead_of_formula": "value_instead_of_formula",
    "literal_in_ast": "literal_in_ast",
    "source_sheet_change": "source_switch",
}
UNUSED_CAP = 50


def detect_excel_error(ctx: CheckContext) -> list[Candidate]:
    found: list[Candidate] = []
    for cell in ctx.cells:
        value = cell.get("cached_value")
        if value in ERROR_TOKENS:
            found.append(
                Candidate(
                    detector="excel_error",
                    cell_refs=[_ref(cell)],
                    payload={"token": value},
                    base_severity="error",
                )
            )
    return found


def detect_error_masking(ctx: CheckContext) -> list[Candidate]:
    found: list[Candidate] = []
    for cell in ctx.cells:
        ast = parse_ast(cell)
        if not has_func(ast, {"IFERROR", "IFNA"}):
            continue
        if cell.get("cached_value") in ERROR_TOKENS:
            continue
        found.append(
            Candidate(
                detector="error_masking",
                cell_refs=[_ref(cell)],
                payload={"func": "IFERROR"},
                base_severity="warning",
            )
        )
    return found


def detect_circular(ctx: CheckContext) -> list[Candidate]:
    found: list[Candidate] = []
    mapped = {(m.sheet, m.row): m for m in ctx.mapping.rows}
    labels = {
        (sheet.name, row.row): row.label
        for sheet in ctx.layout.sheets
        for block in sheet.blocks
        for row in block.rows
    }
    for group in circular_groups(ctx.csr):
        tags: list[str] = []
        if _likely_intentional(group, mapped, labels):
            tags.append("likely_intentional")
        found.append(
            Candidate(
                detector="circular",
                cell_refs=sorted(group),
                tags=tags,
                payload={"size": len(group)},
                base_severity="error",
            )
        )
    return found


def detect_unresolved_dynamic(ctx: CheckContext) -> list[Candidate]:
    found: list[Candidate] = []
    for edge in ctx.edges:
        if edge.kind == "dynamic" and edge.unresolved:
            found.append(
                Candidate(
                    detector="unresolved_dynamic",
                    cell_refs=[edge.source],
                    payload={"kind": "dynamic"},
                    base_severity="warning",
                )
            )
    return found


def detect_external_link(ctx: CheckContext) -> list[Candidate]:
    externals = list(ctx.workbook.get("externals") or [])
    edge_sources = [e.source for e in ctx.edges if e.kind == "external"]
    if not externals and not edge_sources:
        return []
    refs = edge_sources or [_ref(ctx.cells[0])] if ctx.cells else ["Workbook"]
    return [
        Candidate(
            detector="external_link",
            cell_refs=refs[:1],
            payload={"externals": externals},
            base_severity="warning",
        )
    ]


def detect_xlm_or_vba(ctx: CheckContext) -> list[Candidate]:
    if not ctx.workbook.get("has_vba") and not ctx.workbook.get("has_xlm"):
        return []
    refs = [_ref(ctx.cells[0])] if ctx.cells else ["Workbook"]
    return [
        Candidate(
            detector="xlm_or_vba",
            cell_refs=refs,
            payload={
                "has_vba": bool(ctx.workbook.get("has_vba")),
                "has_xlm": bool(ctx.workbook.get("has_xlm")),
            },
            base_severity="warning",
        )
    ]


def detect_series(ctx: CheckContext) -> list[Candidate]:
    found: list[Candidate] = []
    hist_cols = _historical_cols(ctx)
    for outlier in ctx.series_outliers:
        detector = KIND_TO_DETECTOR.get(outlier.kind, outlier.kind)
        tags: list[str] = []
        if outlier.edge_period:
            tags.append("edge_period")
        if outlier.col in hist_cols.get(outlier.block_id, set()) and outlier.kind in {
            "value_instead_of_formula",
            "literal_in_ast",
        }:
            tags.append("hist_manual_adjustment")
        found.append(
            Candidate(
                detector=detector,
                cell_refs=[outlier.cell_ref],
                tags=tags,
                payload={"kind": outlier.kind},
                base_severity="warning",
            )
        )
    return found


def detect_agg_range_gap(ctx: CheckContext) -> list[Candidate]:
    found: list[Candidate] = []
    for cell in ctx.cells:
        ast = parse_ast(cell)
        ranges = sum_ranges(ast, cell["sheet"])
        if not ranges:
            continue
        block = _block_for(ctx, cell["sheet"], int(cell["row"]))
        if block is None:
            continue
        article_rows = [
            r.row
            for r in block.rows
            if not r.check_row and r.row != int(cell["row"])
        ]
        col = int(cell["col"])
        expected = {f"{cell['sheet']}!{format_addr(col, row)}" for row in article_rows}
        covered: set[str] = set()
        for target in ranges:
            cells, _trunc = expand_range(target)
            covered.update(cells)
        missing = sorted(expected - covered)
        if missing:
            found.append(
                Candidate(
                    detector="agg_range_gap",
                    cell_refs=[_ref(cell), *missing],
                    payload={"missing": missing},
                    base_severity="warning",
                )
            )
    return found


def detect_agg_double_count(ctx: CheckContext) -> list[Candidate]:
    aggregators: list[tuple[str, set[str]]] = []
    sum_refs: set[str] = set()
    for cell in ctx.cells:
        ast = parse_ast(cell)
        ranges = sum_ranges(ast, cell["sheet"])
        if not ranges:
            continue
        block = _block_for(ctx, cell["sheet"], int(cell["row"]))
        if block is None:
            continue
        row_meta = next((item for item in block.rows if item.row == int(cell["row"])), None)
        if row_meta is not None and row_meta.check_row:
            continue
        sum_ref = _ref(cell)
        sum_refs.add(sum_ref)
        col = int(cell["col"])
        covered: set[str] = set()
        for target in ranges:
            cells, _trunc = expand_range(target)
            for item in cells:
                if item == sum_ref:
                    continue
                sheet, _, addr = item.partition("!")
                if sheet != cell["sheet"]:
                    continue
                try:
                    item_col, _row = parse_addr(addr)
                except ValueError:
                    continue
                if item_col != col:
                    continue
                covered.add(item)
        aggregators.append((sum_ref, covered))
    by_leaf: dict[str, list[str]] = {}
    for sum_ref, covered in aggregators:
        for leaf in covered:
            if leaf in sum_refs:
                continue
            by_leaf.setdefault(leaf, []).append(sum_ref)
    found: list[Candidate] = []
    for leaf, sums in by_leaf.items():
        uniq = list(dict.fromkeys(sums))
        if len(uniq) < 2:
            continue
        found.append(
            Candidate(
                detector="agg_double_count",
                cell_refs=[leaf, *uniq],
                payload={"sums": uniq},
                base_severity="error",
            )
        )
    return found


def detect_unused_cell(ctx: CheckContext) -> list[Candidate]:
    outputs = _output_refs(ctx)
    if not outputs:
        return []
    used = reachable_from(ctx.csr, outputs)
    found: list[Candidate] = []
    for cell in ctx.cells:
        ref = _ref(cell)
        if ref in used or ref in outputs:
            continue
        if not _is_value_cell(cell):
            continue
        found.append(
            Candidate(
                detector="unused_cell",
                cell_refs=[ref],
                payload={},
                base_severity="risk",
            )
        )
        if len(found) >= UNUSED_CAP:
            break
    return found


def detect_hidden_input(ctx: CheckContext) -> list[Candidate]:
    outputs = [ref for ref in _output_refs(ctx) if not _hidden_ref(ctx, ref)]
    if not outputs:
        return []
    used = reachable_from(ctx.csr, outputs)
    found: list[Candidate] = []
    for cell in ctx.cells:
        if not cell.get("hidden"):
            continue
        ref = _ref(cell)
        if ref not in used:
            continue
        found.append(
            Candidate(
                detector="hidden_input",
                cell_refs=[ref],
                payload={},
                base_severity="warning",
            )
        )
    return found


def detect_scenario_switch(ctx: CheckContext) -> list[Candidate]:
    mapped = [row for row in ctx.mapping.rows if row.concept_id]
    if not mapped:
        return []
    targets_by_source: dict[str, list[str]] = {}
    for edge in ctx.edges:
        if not edge.target:
            continue
        targets_by_source.setdefault(edge.source, []).append(edge.target)
    by_scenario: dict[str, list[str]] = {}
    for row in mapped:
        if _scenario_key(row.sheet) is not None:
            continue
        keys: set[str] = set()
        refs: list[str] = []
        for cell in ctx.cells:
            if cell["sheet"] != row.sheet or int(cell["row"]) != row.row:
                continue
            src = _ref(cell)
            refs.append(src)
            for target in targets_by_source.get(src, []):
                key = _scenario_key(_sheet_of(target))
                if key:
                    keys.add(key)
        if len(keys) != 1:
            continue
        by_scenario.setdefault(next(iter(keys)), []).extend(refs)
    if len(by_scenario) < 2:
        return []
    refs = list(dict.fromkeys(item for group in by_scenario.values() for item in group))
    return [
        Candidate(
            detector="scenario_switch",
            cell_refs=refs,
            payload={"scenarios": sorted(by_scenario)},
            base_severity="error",
        )
    ]


def _scenario_key(sheet: str) -> str | None:
    hit = classify_header(sheet)
    if hit is not None and hit.role == "scenario":
        return hit.period_key
    return None


def _sheet_of(ref: str) -> str:
    try:
        sheet, _body = split_sheet_ref(ref)
    except ValueError:
        return ref
    return sheet


def _ref(cell: dict) -> str:
    return f"{cell['sheet']}!{cell['addr']}"


def _likely_intentional(
    group: list[str],
    mapped: dict[tuple[str, int], object],
    labels: dict[tuple[str, int], str],
) -> bool:
    for ref in group:
        sheet, _, addr = ref.partition("!")
        try:
            _col, row = parse_addr(addr)
        except ValueError:
            continue
        item = mapped.get((sheet, row))
        concept = getattr(item, "concept_id", None) if item is not None else None
        if concept in INTENTIONAL_CONCEPTS:
            return True
        label = (labels.get((sheet, row)) or "").casefold()
        if any(token in label for token in INTENTIONAL_LABELS):
            return True
    return False


def _historical_cols(ctx: CheckContext) -> dict[str, set[int]]:
    out: dict[str, set[int]] = {}
    for sheet in ctx.layout.sheets:
        for block in sheet.blocks:
            cols = {h.col for h in block.axis.headers if h.role == "historical"}
            out[block.block_id] = cols
    return out


def _block_for(ctx: CheckContext, sheet: str, row: int):
    for sh in ctx.layout.sheets:
        if sh.name != sheet:
            continue
        for block in sh.blocks:
            if any(r.row == row for r in block.rows):
                return block
    return None


def _output_refs(ctx: CheckContext) -> list[str]:
    output_rows = {
        (m.sheet, m.row)
        for m in ctx.mapping.rows
        if m.article_role == "output"
        or (m.concept_id or "").endswith("_total")
        or m.concept_id in {"pnl.ebitda", "pnl.net_income", "pnl.ebit"}
    }
    refs: list[str] = []
    for sheet, row in output_rows:
        row_cells = [
            c for c in ctx.cells if c["sheet"] == sheet and int(c["row"]) == row
        ]
        formula_cells = [c for c in row_cells if c.get("formula_raw")]
        chosen = formula_cells or [c for c in row_cells if _is_value_cell(c)]
        refs.extend(_ref(c) for c in chosen)
    return refs


def _hidden_ref(ctx: CheckContext, ref: str) -> bool:
    for cell in ctx.cells:
        if _ref(cell) == ref:
            return bool(cell.get("hidden"))
    return False


def _is_value_cell(cell: dict) -> bool:
    if cell.get("formula_raw"):
        return True
    return as_number(cell.get("cached_value")) is not None
