from __future__ import annotations

from cashflow_audit.mapping.taxonomy import load_taxonomy


def test_taxonomy_includes_fcf_repayment_drawdown() -> None:
    ids = {c.id for c in load_taxonomy()}
    assert {"cf.fcf", "cf.repayment", "cf.drawdown", "pnl.interest_rate", "pnl.tax_rate"} <= ids


def test_fcf_labels_include_net_cf_before_financing() -> None:
    by_id = {c.id: c for c in load_taxonomy()}
    labels = {label.lower() for label in by_id["cf.fcf"].labels}
    assert "fcf" in labels
    assert "net cf before financing" in labels


def test_tax_rate_labels_are_specific() -> None:
    by_id = {c.id: c for c in load_taxonomy()}
    labels = {label.lower() for label in by_id["pnl.tax_rate"].labels}
    assert "tax rate" in labels
    assert "ставка налога" in labels
    assert "effective tax rate" in labels
    assert "ставка" not in labels
