"""本月要注意的日子。規則能算的自己算，算不出來的就承認沒有，不編日期。"""

from calendar import monthrange
from datetime import date

from .config import CALENDAR_RULES, FOMC_DATES


def _weekdays(year: int, month: int, weekday: int) -> list[date]:
    days = monthrange(year, month)[1]
    return [d for d in (date(year, month, i) for i in range(1, days + 1)) if d.weekday() == weekday]


_RULES = {
    "first-friday": lambda y, m: _weekdays(y, m, 4)[:1],
    "third-friday": lambda y, m: _weekdays(y, m, 4)[2:3],
    "weekly-wed": lambda y, m: _weekdays(y, m, 2),
}


def _impact(scope: str, sector: str) -> str:
    if scope == "all":
        return "market"
    return "direct" if scope == sector else "unrelated"


def build_calendar(year: int, month: int, sector: str, earnings: list[dict]) -> dict:
    events = [
        {
            "date": day.isoformat(),
            "name": rule["name"],
            "note": rule["note"],
            "impact": _impact(rule["scope"], sector),
        }
        for rule in CALENDAR_RULES
        for day in _RULES[rule["rule"]](year, month)
    ]

    events += [
        {
            "date": d.isoformat(),
            "name": f"{fomc}",
            "note": "聯準會利率決議，全市場都會反應",
            "impact": "market",
        }
        for d, fomc in ((date.fromisoformat(x["date"]), x["name"]) for x in FOMC_DATES)
        if d.year == year and d.month == month
    ]

    events += [
        {
            "date": e["date"],
            "name": f"{e['symbol']} 財報",
            "note": "盤後公布" if e.get("hour") == "amc" else "盤前公布",
            "impact": "direct",
        }
        for e in earnings
        if e["date"][:7] == f"{year:04d}-{month:02d}"
    ]

    upcoming = sorted(e["date"] for e in earnings if e["date"] >= date.today().isoformat())

    return {
        "year": year,
        "month": month,
        "first_weekday": date(year, month, 1).weekday(),
        "days": monthrange(year, month)[1],
        "events": sorted(events, key=lambda e: e["date"]),
        "next_earnings": upcoming[0] if upcoming else "",
        "has_fomc_data": bool(FOMC_DATES),
    }
