"""第九階段之二：帶子的效力，離財報越遠是不是越差？

第九階段把「偷看未來」擋掉之後，整張表全垮 —— 但那次只留下每季的最後 30 天。
所以有兩種可能，這支腳本要分開它們：

  (甲) 帶子本來就沒用，之前是偷看未來撐起來的
  (乙) 帶子只在財報後一段時間有用，越晚越沒用，前一次剛好只測到最沒用的那段

測法：窗口固定 -7/+21（畫線只用到財報後 21 天，之後每一天都是乾淨的樣本外），
然後把觀測日按「離財報幾天」分組，每一組各自算一次。
乙的話會看到一條往下掉的曲線；甲的話每一組都是平的零。
"""

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from slope_backtest import _shift, load_prices
from zone_backtest import TECH, NEW, load_eps_yahoo
from confidence_backtest import LAGGARD, K, thin, split, win, bootstrap
from window_check import prepare, flatten, grade, zone4

UNIVERSE = TECH + NEW + LAGGARD
GOOD = TECH + NEW
WIN = (7, 21)                    # 固定成產品現在的窗口
LAMS = (0.05, 0.1, 0.2, 1.0)
BUCKETS = ((21, 35), (35, 50), (50, 65), (65, 95))


def bucket(segs, lo, hi):
    """只留下離財報 lo~hi 天的觀測日。"""
    out = []
    for s in segs:
        out.append({"obs": [o for o in s["obs"] if lo <= o["t"] < hi],
                    "cells": s["cells"]})
    return [s for s in out if s["obs"]]


def run():
    eps = load_eps_yahoo(UNIVERSE)
    f = min(min(d for d, _ in v) for v in eps.values())
    l = max(max(d for d, _ in v) for v in eps.values())
    px = load_prices(UNIVERSE, _shift(f, -400), _shift(l, 200))
    # 觀測從財報後第 21 天開始 —— 畫線用到的最後一天就是第 21 天，之後全是樣本外
    prep = {t: prepare(px[t], eps[t], obs_from=WIN[1], windows=[WIN]) for t in UNIVERSE}
    n = sum(len(s["obs"]) for t in UNIVERSE for s in prep[t])
    print(f"\n窗口固定 -{WIN[0]}/+{WIN[1]}，觀測日全部在畫線範圍之外，共 {n} 天")

    print("\n【A】離財報越遠，帶子還有沒有用（14 檔好公司）")
    print(f"  {'離財報':<12}{'λ':<7}{'樣本':>7}{'優勢':>8}"
          f"{'超出下緣':>9}{'中間偏下':>9}{'中間偏上':>9}{'超出上緣':>9}{'單調':>6}{'|z|':>7}")
    res = {}
    for lo, hi in BUCKETS:
        sub = {t: bucket(prep[t], lo, hi) for t in UNIVERSE}
        for lam in LAMS:
            rows = [o for t in GOOD for o in flatten(sub[t], *WIN, lam)]
            if len(rows) < 500:
                continue
            g = grade(rows)
            ind = [o for t in GOOD for o in thin(flatten(sub[t], *WIN, lam))]
            b, a = split(ind)
            d = win(b) - win(a)
            res[(lo, lam)] = {"edge": d, **g, "n": len(ind)}
            print(f"  {f'{lo}-{hi} 天':<12}{lam:<7}{len(ind):>7}{d:>+8.1f}"
                  + "".join(f"{x:>8.1f}%" for x in g["zones"])
                  + f"{'✅' if g['mono'] else '❌':>6}{g['absz']:>7.2f}")

    print("\n【B】最好的那一組加上信賴區間")
    ok = sorted((v["edge"], k) for k, v in res.items() if v["mono"])
    for edge, (lo, lam) in ok[-3:][::-1]:
        hi = dict(BUCKETS)[lo]
        sub = {t: bucket(prep[t], lo, hi) for t in UNIVERSE}
        cg, _ = bootstrap({t: thin(flatten(sub[t], *WIN, lam)) for t in GOOD})
        ca, _ = bootstrap({t: thin(flatten(sub[t], *WIN, lam)) for t in UNIVERSE})
        print(f"  {lo}-{hi} 天 λ={lam}：14 檔好公司 {cg[1]:+.1f} [{cg[0]:+.1f},{cg[2]:+.1f}]"
              f"　全部 30 檔 {ca[1]:+.1f} [{ca[0]:+.1f},{ca[2]:+.1f}]")
    if not ok:
        print("  沒有任何一組是單調的 —— 支持 (甲)：帶子本來就沒用")

    chart(res)
    return res


def chart(res):
    fig, ax = plt.subplots(1, 2, figsize=(14, 5))
    xs = [lo for lo, _ in BUCKETS]
    for lam, c in zip(LAMS, ["#2ca02c", "#1f77b4", "#ff7f0e", "#d62728"]):
        v = [res[(lo, lam)]["edge"] if (lo, lam) in res else np.nan for lo in xs]
        ax[0].plot(xs, v, "o-", color=c, label=f"lam={lam}")
        z = [res[(lo, lam)]["absz"] if (lo, lam) in res else np.nan for lo in xs]
        ax[1].plot(xs, z, "o-", color=c, label=f"lam={lam}")
    ax[0].axhline(0, c="k", lw=1)
    ax[0].set_xlabel("days since the earnings release (bucket start)")
    ax[0].set_ylabel("buy minus avoid (pp)"); ax[0].legend(fontsize=8)
    ax[0].set_title("Does the band decay as the quarter goes on?\n"
                    "window fixed at -7/+21, every observation is out of sample")
    ax[1].axhspan(0.5, 0.9, color="#2ca02c", alpha=.12)
    ax[1].set_xlabel("days since the earnings release"); ax[1].set_ylabel("median |z|")
    ax[1].legend(fontsize=8)
    ax[1].set_title("How far the price has run from the anchor\n"
                    "green = the range where the band still means something")
    fig.tight_layout()
    fig.savefig("shots/decay-stage9b.png", dpi=110)
    print("\n圖：shots/decay-stage9b.png")


if __name__ == "__main__":
    run()
