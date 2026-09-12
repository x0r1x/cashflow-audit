from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ControlSpec:
    id: str
    name: str
    need: frozenset[str]
    nice: frozenset[str] = field(default_factory=frozenset)
    axis: str = "year"


CATALOG: tuple[ControlSpec, ...] = (
    ControlSpec("F01", "Динамика выручки факт→прогноз", frozenset({"pnl.revenue"})),
    ControlSpec("F02", "EBITDA и маржа", frozenset({"pnl.ebitda", "pnl.revenue"})),
    ControlSpec("F03", "Убытки / отр. EBITDA", frozenset({"pnl.ebitda"})),
    ControlSpec(
        "F04",
        "CFO/FCF и разрыв прибыль→деньги",
        frozenset({"pnl.net_income"}),
        nice=frozenset({"cf.cfo", "cf.fcf", "cf.capex"}),
    ),
    ControlSpec(
        "F05",
        "Оборотный капитал",
        frozenset({"bs.ar", "pnl.revenue"}),
        nice=frozenset({"bs.inventory", "bs.ap", "pnl.cogs"}),
    ),
    ControlSpec(
        "F06",
        "Долговая нагрузка",
        frozenset({"bs.debt", "pnl.ebitda"}),
        nice=frozenset({"bs.cash"}),
    ),
    ControlSpec("F07", "ICR / DSCR", frozenset({"pnl.ebitda", "pnl.interest"})),
    ControlSpec(
        "F08",
        "Ликвидность",
        frozenset({"bs.cash"}),
        nice=frozenset({"cf.drawdown"}),
        axis="finest",
    ),
    ControlSpec(
        "F09",
        "Концентрация погашений",
        frozenset({"cf.repayment"}),
        axis="finest",
    ),
    ControlSpec("F10", "Процентный / валютный", frozenset({"pnl.interest"})),
    ControlSpec("F11", "Агрессивность предпосылок", frozenset({"pnl.revenue"})),
    ControlSpec(
        "F12",
        "Непоследовательность драйверов",
        frozenset({"pnl.revenue"}),
        nice=frozenset({"pnl.ebitda", "cf.fcf", "cf.capex"}),
    ),
    ControlSpec(
        "F13",
        "Дивиденды vs FCFE",
        frozenset({"cf.dividends"}),
        nice=frozenset({"cf.fcf", "cf.cfo", "cf.capex", "cf.drawdown", "cf.repayment"}),
    ),
    ControlSpec("F14", "Headroom", frozenset({"bs.cash"})),
)
