"""時間序列的共用算術。"""


def downsample(values: list, n: int) -> list:
    """挑 n 個點畫縮圖。頭尾一定保留，中間等距取樣。"""
    if len(values) <= n:
        return values
    step = (len(values) - 1) / (n - 1)
    return [values[round(i * step)] for i in range(n)]


def at_or_before(rows: list[dict], key: str, day: str) -> dict | None:
    """目標日期當天或之前最近的一筆。用日期而不是位置，日頻月頻季頻才會是同一套邏輯。"""
    prior = [r for r in rows if r[key] <= day]
    return prior[-1] if prior else None
