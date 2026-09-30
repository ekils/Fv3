"""帶寬回測：那條帶子當進出場門檻好不好用。

第一階段比的是中心線準不準，這裡比的是**訊號有沒有用** ——
跌破下緣之後真的比較會漲嗎？衝出上緣之後真的比較會跌嗎？

中心線 A 和 D 都測。第一階段兩者的誤差實質平手，不先射箭再畫靶。
"""

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from slope_backtest import (
    AFTER_DAYS, BEFORE_DAYS, TICKERS, _d, _shift, _window,
    eps_long_slope, load_eps, load_prices, predict, segments, slope_log, slope_ref,
)

SIGMA_QUARTERS = 5
KS = (0.8, 1.0, 1.2, 1.5, 2.0)
FWD = (20, 60)          # 往後幾個交易日看報酬
BEST_LAMBDA = 0.1       # 第一階段前半段挑出來的


def sigma_at(closes, rels: list[str], how: str) -> float | None:
    """ref 的算法：最近 5 季、每季拆公布前／後取均價 → 相鄰相減取絕對值 → 收斂成一個數。

    how="std" 是現況（對那些絕對差再取標準差），how="med" 是改用中位數。
    差別在：每季都剛好平移 10 塊的股票，std 會給 0（帶子沒有寬度），med 給 10。
    """
    means = []
    for r in rels[:SIGMA_QUARTERS]:
        before = _window(closes, _shift(r, -BEFORE_DAYS), _shift(r, -1))
        after = _window(closes, r, _shift(r, AFTER_DAYS))
        means += [float(np.mean([c for _, c in w])) for w in (before, after) if w]
    if len(means) < 4:
        return None
    diff = [abs(means[i] - means[i + 1]) for i in range(len(means) - 1)]
    return float(np.std(diff, ddof=1) if how == "std" else np.median(diff))


def centre_fn(seg, variant: str):
    """回傳 t（距離公布日幾天）→ 中心線股價。"""
    a = seg["anchor"]
    if variant == "A":
        m = slope_ref(seg["full"], seg["eps"], a)
        return None if m is None else (lambda t: a + m * t)
    m, long = slope_log(seg["settled"], seg["eps"], a), eps_long_slope(seg["eps"])
    if m is None or long is None:
        return None
    mix = BEST_LAMBDA * m + (1 - BEST_LAMBDA) * long
    return lambda t: a * np.exp(mix * t)


def observations(closes, eps_rows, variant: str, how: str) -> list[dict]:
    """一段裡的每一個交易日一筆：它落在帶子的哪一區、往後 20／60 天漲跌多少。"""
    idx = {d: i for i, (d, _) in enumerate(closes)}
    segs = segments(closes, eps_rows)
    # segments() 是由新到舊逐段吐出來的，第 j 段對應 eps_rows[j+1]
    out = []
    for j, seg in enumerate(segs):
        c = centre_fn(seg, variant)
        sg = sigma_at(closes, [r for r, _ in eps_rows[j + 1:]], how)
        if c is None or not sg:
            continue
        for d, price in _window(closes, seg["rel"], seg["next"]):
            i = idx[d]
            fwd = {}
            for n in FWD:
                fwd[n] = (closes[i + n][1] / price - 1) * 100 if i + n < len(closes) else None
            out.append({"date": d, "price": price,
                        "z": (price - c((_d(d) - _d(seg["rel"])).days)) / sg, **{f"f{n}": fwd[n] for n in FWD}})
    return out


def zone(z: float, k: float) -> str:
    if z < -k:
        return "below"
    if z < 0:
        return "lower"
    if z <= k:
        return "upper"
    return "above"


def summarise(obs, k: float, n: int) -> dict:
    buckets = {}
    for o in obs:
        if o[f"f{n}"] is None:
            continue
        buckets.setdefault(zone(o["z"], k), []).append(o[f"f{n}"])
    base = [o[f"f{n}"] for o in obs if o[f"f{n}"] is not None]
    return {z: {"n": len(v), "med": float(np.median(v)), "share": 100 * len(v) / len(base)}
            for z, v in buckets.items()} | {
        "all": {"n": len(base), "med": float(np.median(base)), "share": 100.0}}


def run():
    eps_all = load_eps()
    first = min(min(d for d, _ in v) for v in eps_all.values())
    last = max(max(d for d, _ in v) for v in eps_all.values())
    px = load_prices(TICKERS, _shift(first, -400), _shift(last, 200))

    data = {}
    for variant in ("A", "D"):
        for how in ("std", "med"):
            obs = []
            for t in TICKERS:
                for o in observations(px[t], eps_all[t], variant, how):
                    obs.append({**o, "ticker": t})
            data[(variant, how)] = obs
            print(f"{variant}/{how}: {len(obs)} 個交易日觀測")

    # 跟第一階段同一個理由：D 要 24 季 EPS，覆蓋的日子比 A 少一半。
    # 各比各的，比到的是「誰站在比較好的那幾年」，不是誰的門檻比較好用。
    common = set.intersection(*({(o["ticker"], o["date"]) for o in v} for v in data.values()))
    data = {k: [o for o in v if (o["ticker"], o["date"]) in common] for k, v in data.items()}
    print(f"共同觀測 {len(common)} 個交易日")

    print(f"\n{'中心線/σ':<12}{'k':>5}{'跌破下緣':>10}{'佔比':>8}"
          f"{'中間偏下':>10}{'中間偏上':>10}{'衝出上緣':>10}{'全樣本':>9}")
    for key, obs in data.items():
        for k in KS:
            s = summarise(obs, k, 60)
            g = lambda z: f"{s[z]['med']:+.2f}%" if z in s else "  --  "
            print(f"{key[0]}/{key[1]:<10}{k:>5}{g('below'):>10}"
                  f"{s.get('below',{}).get('share',0):>7.1f}%"
                  f"{g('lower'):>10}{g('upper'):>10}{g('above'):>10}{g('all'):>9}")

    # 這 12 檔是他自己的觀察名單，全是大型贏家股。整段 2013–2025 又幾乎是多頭 ——
    # 「跌破就買比較好」有可能只是多頭裡的逢低買進，跟這條帶子無關。逐年拆開看才知道。
    print(f"\n逐年（中心線 A、σ=std、k=1.5、往後 60 交易日）")
    print(f"{'年':<6}{'跌破下緣':>10}{'全樣本':>10}{'超額':>9}{'訊號天數':>10}")
    obs = data[("A", "std")]
    for y in sorted({o["date"][:4] for o in obs}):
        sub = [o for o in obs if o["date"][:4] == y and o["f60"] is not None]
        low = [o["f60"] for o in sub if zone(o["z"], 1.5) == "below"]
        if not sub:
            continue
        base = float(np.median([o["f60"] for o in sub]))
        m = float(np.median(low)) if low else float("nan")
        print(f"{y:<6}{m:>9.2f}%{base:>9.2f}%{m - base:>8.2f}{len(low):>10}")

    chart(data)
    return data


def chart(data):
    fig, ax = plt.subplots(2, 2, figsize=(14, 9))
    zones = ["below", "lower", "upper", "above"]
    label = ["below lower band", "lower half", "upper half", "above upper band"]
    col = ["#2ca02c", "#98df8a", "#ffbb78", "#d62728"]

    # ① 四區的 60 日報酬（中心線 D、σ 兩種算法）
    for col_i, how in enumerate(("std", "med")):
        s = summarise(data[("D", how)], 1.0, 60)
        base = s["all"]["med"]
        vals = [s[z]["med"] if z in s else 0 for z in zones]
        b = ax[0][col_i].bar(label, vals, color=col)
        ax[0][col_i].bar_label(b, fmt="%+.2f%%")
        ax[0][col_i].axhline(base, ls="--", c="k", lw=1,
                             label=f"all days {base:+.2f}%")
        ax[0][col_i].set_title(f"Forward 60-day return by zone\ncentre=D, sigma={how}, k=1.0")
        ax[0][col_i].set_ylabel("median forward 60d return (%)")
        ax[0][col_i].tick_params(axis="x", rotation=20)
        ax[0][col_i].legend()

    # ② k 掃描：下緣訊號的超額報酬 vs 觸發頻率
    for key, style in ((("A", "std"), "o--"), (("D", "std"), "o-"),
                       (("D", "med"), "s-")):
        edge, share = [], []
        for k in KS:
            s = summarise(data[key], k, 60)
            edge.append(s["below"]["med"] - s["all"]["med"] if "below" in s else np.nan)
            share.append(s.get("below", {}).get("share", 0))
        ax[1][0].plot(KS, edge, style, label=f"{key[0]}/{key[1]}")
        ax[1][1].plot(KS, share, style, label=f"{key[0]}/{key[1]}")
    ax[1][0].axhline(0, c="k", lw=1)
    ax[1][0].set_title("Lower-band signal: excess return over all days\n(forward 60 trading days)")
    ax[1][0].set_xlabel("k (band = centre +/- k*sigma)")
    ax[1][0].set_ylabel("excess median return (pp)"); ax[1][0].legend()
    ax[1][1].set_title("How often the lower band is breached")
    ax[1][1].set_xlabel("k"); ax[1][1].set_ylabel("% of trading days"); ax[1][1].legend()

    fig.tight_layout()
    fig.savefig("shots/band-stage2.png", dpi=110)
    print("\n圖：shots/band-stage2.png")


if __name__ == "__main__":
    run()
