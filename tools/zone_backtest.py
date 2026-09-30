"""第三階段：斜率正負 × 帶子四段 = 八格決策表，每一格的正確率。

跟前兩階段的差別：
1. EPS 改從 Yahoo 抓，不讀 ref 的 epsdata.pkl —— pkl 沒有 JNJ／CB，RL 也只有 10 季。
   Yahoo 給到 2002 年，防禦股才進得來，RL 也才跑得動。
2. 中心線只用 A（第二階段證明它當門檻最好用），不再比變體。
3. 看的是八格各自的「往後 60 交易日是漲是跌」，不是誤差。
"""

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import yfinance as yf

from slope_backtest import (
    AFTER_DAYS, BEFORE_DAYS, _d, _shift, _window, load_prices, segments, slope_ref,
)
from band_backtest import SIGMA_QUARTERS, sigma_at, zone

TECH = ("AAPL", "COST", "DECK", "GD", "GOOGL", "LLY",
        "MA", "MCD", "META", "MSFT", "NVDA", "RL")
NEW = ("JNJ", "CB")
ALL = TECH + NEW
KS = (1.0, 1.5, 2.0)
FWD = 60
ACTION = {("+", "below"): "買入", ("+", "lower"): "考慮買入",
          ("+", "upper"): "不考慮", ("+", "above"): "不買",
          ("-", "below"): "買入", ("-", "lower"): "買入",
          ("-", "upper"): "不考慮", ("-", "above"): "不買"}


def load_eps_yahoo(tickers) -> dict[str, list[tuple[str, float]]]:
    """(公布日, 當季 EPS)，由新到舊。Yahoo 的 Reported EPS 是調整後盈餘，
    跟 ref pkl 的口徑不完全一樣 —— 所以這一階段的數字不能直接跟前兩階段並排。"""
    out = {}
    for t in tickers:
        df = yf.Ticker(t).get_earnings_dates(limit=100).dropna(subset=["Reported EPS"])
        rows = [(i.date().isoformat(), float(e))
                for i, e in df["Reported EPS"].items()]
        out[t] = sorted(rows, reverse=True)
        print(f"{t}: {len(rows)} 季，{out[t][-1][0]} ~ {out[t][0][0]}")
    return out


def observations(closes, eps_rows) -> list[dict]:
    """每個交易日一筆：斜率正負、落在帶子哪一段、往後 60 交易日漲跌。"""
    idx = {d: i for i, (d, _) in enumerate(closes)}
    out = []
    for j, seg in enumerate(segments(closes, eps_rows)):
        m = slope_ref(seg["full"], seg["eps"], seg["anchor"])
        sg = sigma_at(closes, [r for r, _ in eps_rows[j + 1:]], "std")
        if m is None or not sg:
            continue
        a = seg["anchor"]
        for d, price in _window(closes, seg["rel"], seg["next"]):
            i = idx[d]
            if i + FWD >= len(closes):
                continue
            t = (_d(d) - _d(seg["rel"])).days
            out.append({"date": d, "sign": "+" if m > 0 else "-",
                        "z": (price - (a + m * t)) / sg,
                        "fwd": (closes[i + FWD][1] / price - 1) * 100})
    return out


def cells(obs, k: float) -> dict:
    g = {}
    for o in obs:
        g.setdefault((o["sign"], zone(o["z"], k)), []).append(o["fwd"])
    return g


def table(title, obs, k: float):
    base = np.array([o["fwd"] for o in obs])
    bwin = 100 * float((base > 0).mean())
    print(f"\n{title}（k={k}、往後 {FWD} 交易日、樣本 {len(obs)}）")
    print(f"  全樣本基準：中位數 {np.median(base):+.2f}%、上漲比率 {bwin:.1f}%")
    print(f"  {'斜率':<5}{'帶子位置':<12}{'你的判斷':<10}"
          f"{'中位報酬':>10}{'上漲比率':>10}{'超額':>8}{'佔比':>8}{'天數':>8}")
    g = cells(obs, k)
    for sign in ("+", "-"):
        for z, name in (("below", "超出下緣"), ("lower", "中間偏下"),
                        ("upper", "中間偏上"), ("above", "超出上緣")):
            v = np.array(g.get((sign, z), []))
            if not len(v):
                print(f"  {sign:<5}{name:<12}{ACTION[(sign, z)]:<10}{'（無樣本）':>10}")
                continue
            win = 100 * float((v > 0).mean())
            print(f"  {sign:<5}{name:<12}{ACTION[(sign, z)]:<10}"
                  f"{np.median(v):>9.2f}%{win:>9.1f}%{win - bwin:>7.1f}"
                  f"{100 * len(v) / len(base):>7.1f}%{len(v):>8}")
    return g, bwin


def run():
    eps = load_eps_yahoo(ALL)
    first = min(min(d for d, _ in v) for v in eps.values())
    last = max(max(d for d, _ in v) for v in eps.values())
    px = load_prices(ALL, _shift(first, -400), _shift(last, 200))

    obs = {t: observations(px[t], eps[t]) for t in ALL}
    print("\n每檔觀測天數：", {t: len(v) for t, v in obs.items()})

    pools = {"全部 14 檔": [o for t in ALL for o in obs[t]],
             "原 12 檔（多為大型成長股）": [o for t in TECH for o in obs[t]],
             "新加 JNJ+CB（防禦／保險）": [o for t in NEW for o in obs[t]]}
    for name, pool in pools.items():
        table(name, pool, 1.5)

    print("\n" + "=" * 70)
    for t in NEW + ("RL",):
        table(f"單檔 {t}", obs[t], 1.5)

    print("\n" + "=" * 70)
    for k in KS:
        table(f"全部 14 檔 · k={k}", pools["全部 14 檔"], k)

    chart(pools, obs)
    return obs, pools


def chart(pools, obs):
    fig, ax = plt.subplots(2, 2, figsize=(15, 9))
    zs = [("below", "below\nlower band"), ("lower", "lower\nhalf"),
          ("upper", "upper\nhalf"), ("above", "above\nupper band")]

    # ① 八格的上漲比率（全部 14 檔）
    pool = pools["全部 14 檔"]
    g = cells(pool, 1.5)
    base = np.array([o["fwd"] for o in pool])
    bwin = 100 * float((base > 0).mean())
    x = np.arange(4)
    for j, (sign, c, lab) in enumerate((("+", "#1f77b4", "slope +"),
                                        ("-", "#d62728", "slope -"))):
        v = [100 * float((np.array(g[(sign, z)]) > 0).mean())
             if g.get((sign, z)) else np.nan for z, _ in zs]
        b = ax[0][0].bar(x + j * 0.4, v, 0.4, color=c, label=lab)
        ax[0][0].bar_label(b, fmt="%.0f%%", fontsize=8)
    ax[0][0].axhline(bwin, ls="--", c="k", lw=1, label=f"all days {bwin:.0f}%")
    ax[0][0].set_xticks(x + 0.2); ax[0][0].set_xticklabels([l for _, l in zs])
    ax[0][0].set_ylim(0, 105); ax[0][0].legend(fontsize=8)
    ax[0][0].set_title("8-cell hit rate: % of days with positive 60d return\n"
                       "all 14 tickers, k=1.5")

    # ② 同樣八格，但看中位報酬
    for j, (sign, c, lab) in enumerate((("+", "#1f77b4", "slope +"),
                                        ("-", "#d62728", "slope -"))):
        v = [float(np.median(g[(sign, z)])) if g.get((sign, z)) else np.nan
             for z, _ in zs]
        b = ax[0][1].bar(x + j * 0.4, v, 0.4, color=c, label=lab)
        ax[0][1].bar_label(b, fmt="%+.1f%%", fontsize=8)
    ax[0][1].axhline(float(np.median(base)), ls="--", c="k", lw=1)
    ax[0][1].set_xticks(x + 0.2); ax[0][1].set_xticklabels([l for _, l in zs])
    ax[0][1].legend(fontsize=8)
    ax[0][1].set_title("8-cell median forward 60d return")

    # ③ 成長股 vs 防禦股：下緣訊號還成不成立
    for j, (name, key, c) in enumerate((("12 growth", "原 12 檔（多為大型成長股）", "#2ca02c"),
                                        ("JNJ+CB", "新加 JNJ+CB（防禦／保險）", "#9467bd"))):
        p = pools[key]
        gg = cells(p, 1.5)
        bb = 100 * float((np.array([o["fwd"] for o in p]) > 0).mean())
        v = [100 * float((np.array(sum((gg.get((s, z), []) for s in "+-"), [])) > 0).mean())
             if sum((gg.get((s, z), []) for s in "+-"), []) else np.nan for z, _ in zs]
        ax[1][0].plot(range(4), v, "o-", color=c, label=f"{name} (base {bb:.0f}%)")
        ax[1][0].axhline(bb, ls=":", c=c, lw=1)
    ax[1][0].set_xticks(range(4)); ax[1][0].set_xticklabels([l for _, l in zs])
    ax[1][0].set_ylabel("% positive 60d"); ax[1][0].legend(fontsize=8)
    ax[1][0].set_title("Does the band survive outside megacap growth?\n"
                       "dotted = that group's own baseline")

    # ④ 逐檔：下緣（含超出）vs 上緣（含超出）的上漲比率
    lo, hi, base_t = [], [], []
    for t in ALL:
        gg = cells(obs[t], 1.5)
        L = sum((gg.get((s, z), []) for s in "+-" for z in ("below", "lower")), [])
        H = sum((gg.get((s, z), []) for s in "+-" for z in ("upper", "above")), [])
        f = lambda v: 100 * float((np.array(v) > 0).mean()) if v else np.nan
        lo.append(f(L)); hi.append(f(H))
        base_t.append(f([o["fwd"] for o in obs[t]]))
    xx = np.arange(len(ALL))
    ax[1][1].bar(xx - 0.2, lo, 0.4, color="#2ca02c", label="lower two zones")
    ax[1][1].bar(xx + 0.2, hi, 0.4, color="#d62728", label="upper two zones")
    ax[1][1].plot(xx, base_t, "k_", ms=18, label="ticker baseline")
    ax[1][1].set_xticks(xx); ax[1][1].set_xticklabels(ALL, rotation=45)
    ax[1][1].set_ylabel("% positive 60d"); ax[1][1].legend(fontsize=8)
    ax[1][1].set_title("Per ticker: buy half vs avoid half (k=1.5)")

    fig.tight_layout()
    fig.savefig("shots/zone-stage3.png", dpi=110)
    print("\n圖：shots/zone-stage3.png")


if __name__ == "__main__":
    run()
