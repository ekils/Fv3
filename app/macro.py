"""把 FRED 的原始觀測值變成「現在幾趴、最近是升是降」。不做預測，只做算術。"""

from datetime import date, timedelta

from .config import (
    DEFAULT_FRED,
    FRED_CHANGE_DAYS,
    FRED_EFFECT,
    FRED_EFFECT_BY_SECTOR,
    FRED_SERIES,
    FRED_SPARK_POINTS,
    SECTOR_FRED,
    SECTOR_LABELS,
)
from .series import downsample


def series_for(sector: str) -> tuple[str, ...]:
    return SECTOR_FRED.get(sector, DEFAULT_FRED)


def _at(obs: list[dict], target: date) -> dict | None:
    """目標日期當天或之前最近的一筆。用日期而不是位置，日頻月頻季頻才會是同一套邏輯。"""
    prior = [o for o in obs if o["date"] <= target.isoformat()]
    return prior[-1] if prior else None


def _yoy(obs: list[dict]) -> list[dict]:
    out = []
    for o in obs:
        base = _at(obs, date.fromisoformat(o["date"]) - timedelta(days=365))
        if base and base["value"]:
            out.append({"date": o["date"], "value": (o["value"] / base["value"] - 1) * 100})
    return out


def effect_for(sid: str, sector: str) -> tuple[int, str, str]:
    """這個指標往上，對這個產業是好是壞，以及升／降各代表什麼。

    方向是按產業查的：升息壓垮多數公司，但銀行保險就是靠利差賺錢。
    """
    return FRED_EFFECT_BY_SECTOR.get(sector, {}).get(sid, FRED_EFFECT[sid])


def _effect(sid: str, sector: str, change: float | None) -> dict | None:
    """沒動就沒有結論 —— 不硬給一個方向。"""
    if not change:
        return None
    sign, up, down = effect_for(sid, sector)
    return {"good": (change > 0) == (sign > 0), "text": up if change > 0 else down}


def build_macro(sector: str, raw: dict[str, list[dict]]) -> dict:
    rows = []
    for sid in series_for(sector):
        name, unit, how = FRED_SERIES[sid]
        obs = _yoy(raw[sid]) if how == "yoy" else raw[sid]
        if not obs:
            continue
        latest = obs[-1]
        before = _at(obs, date.fromisoformat(latest["date"]) - timedelta(days=FRED_CHANGE_DAYS))
        change = round(latest["value"] - before["value"], 2) if before else None
        rows.append({
            "id": sid,
            "name": name,
            "unit": unit,
            "date": latest["date"],
            "value": round(latest["value"], 2),
            "change": change,
            "effect": _effect(sid, sector, change),
            "rising_good": effect_for(sid, sector)[0] > 0,
            "spark": [round(v, 3) for v in downsample([o["value"] for o in obs], FRED_SPARK_POINTS)],
        })
    return {"sector": sector, "sector_label": SECTOR_LABELS.get(sector, sector),
            "months": FRED_CHANGE_DAYS // 30, "rows": rows}
