from __future__ import annotations

from cashflow_audit.mapping.taxonomy import load_taxonomy


def test_taxonomy_includes_fcf_repayment_drawdown() -> None:
    ids = {c.id for c in load_taxonomy()}
    assert {
        "cf.fcf",
        "cf.repayment",
        "cf.drawdown",
        "pnl.interest_rate",
        "pnl.tax_rate",
        "cf.da",
        "bs.ppe",
        "pnl.volume",
        "pnl.price",
        "covenant.headroom",
        "val.npv",
        "val.irr",
        "val.wacc",
        "ops.headcount",
    } <= ids


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


def test_cf_da_labels_are_addback_not_generic_depreciation() -> None:
    by_id = {c.id: c for c in load_taxonomy()}
    labels = {label.lower() for label in by_id["cf.da"].labels}
    assert "d&a add-back" in labels
    assert "depreciation add-back" in labels
    assert "depreciation" not in labels
    assert "амортизация" not in labels


def test_ppe_labels_are_specific() -> None:
    by_id = {c.id: c for c in load_taxonomy()}
    labels = {label.lower() for label in by_id["bs.ppe"].labels}
    assert "ppe" in labels
    assert "fixed assets" in labels
    assert "основные средства" in labels
    assert "assets" not in labels


def test_volume_and_price_labels_are_specific() -> None:
    by_id = {c.id: c for c in load_taxonomy()}
    volume = {label.lower() for label in by_id["pnl.volume"].labels}
    price = {label.lower() for label in by_id["pnl.price"].labels}
    assert "volume" in volume
    assert "объём" in volume
    assert "price" in price
    assert "цена" in price
    assert "sales" not in volume
    assert "sales" not in price
    assert "revenue" not in volume
    assert "revenue" not in price


def test_covenant_headroom_labels_are_specific() -> None:
    by_id = {c.id: c for c in load_taxonomy()}
    assert "covenant.headroom" in by_id
    labels = {label.lower() for label in by_id["covenant.headroom"].labels}
    assert "covenant headroom" in labels
    assert "запас по ковенанту" in labels
    assert "headroom" not in labels
    assert "dscr" not in labels


def test_npv_labels_are_specific() -> None:
    by_id = {c.id: c for c in load_taxonomy()}
    assert "val.npv" in by_id
    labels = {label.lower() for label in by_id["val.npv"].labels}
    assert "npv" in labels
    assert "net present value" in labels
    assert "чпс" in labels
    assert "value" not in labels
    assert "irr" not in labels
    assert "wacc" not in labels


def test_irr_and_wacc_labels_are_specific() -> None:
    by_id = {c.id: c for c in load_taxonomy()}
    assert "val.irr" in by_id
    assert "val.wacc" in by_id
    irr = {label.lower() for label in by_id["val.irr"].labels}
    wacc = {label.lower() for label in by_id["val.wacc"].labels}
    assert "irr" in irr
    assert "internal rate of return" in irr
    assert "внд" in irr
    assert "wacc" in wacc
    assert "discount rate" in wacc
    assert "ставка дисконта" in wacc
    assert "rate" not in irr
    assert "rate" not in wacc
    assert "npv" not in irr
    assert "npv" not in wacc


def test_headcount_labels_are_specific() -> None:
    by_id = {c.id: c for c in load_taxonomy()}
    assert "ops.headcount" in by_id
    labels = {label.lower() for label in by_id["ops.headcount"].labels}
    assert "headcount" in labels
    assert "fte" in labels
    assert "численность" in labels
    assert "staff" not in labels
    assert "employees" not in labels
    assert "opex" not in labels
