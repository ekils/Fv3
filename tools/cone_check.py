"""第九階段之三：帶子要不要隨著時間變寬？

之二證明帶子會隨著離財報越遠而失效，而且 |z| 中位數一路從 0.59 爬到 1.43 ——
股價離錨點越來越遠，帶寬卻固定不動，所以同樣一個「超出下緣」在第 30 天和
第 80 天講的根本不是同一件事。

我先前說「這是判斷門檻不是預測區間，所以不用喇叭形」—— 這支腳本就是要檢查
那句話對不對。三種帶寬各跑一次：

  flat  固定（現在的做法）
  sqrt  乘上 sqrt(t / 21)  —— 隨機漫步的標準答案
  lin   乘上 t / 21        —— 更激進

哪一種能讓「離財報 50 天以後」那幾格恢復單調，就是對的。
"""

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from slope_backtest import _shift, load_prices
from zone_backtest import TECH, NEW, load_eps_yahoo
from confidence_backtest import LAGGARD, K, thin, split, win, bootstrap
from window_check import prepare, grade

UNIVERSE = TECH + NEW + LAGGARD
GOOD = TECH + NEW
WIN = (7, 21)
LAM = 0.1
BUCKETS = ((21, 35), (35, 50), (50, 65), (65, 95))
WIDEN = {
    "flat": lambda t: 1.0,
    "sqrt": lambda t: float(np.sqrt(max(t, WIN[1]) / WIN[1])),
    "lin": lambda t: max(t, WIN[1]) / WIN[1],
}


def rows_for(segs, kind, lo=0, hi=10**9):
    f = WIDEN[kind]
    out = []
    for s in segs:
        cell = s["cells"].get(WIN)
        if cell is None:
            continue
        anchor, slope, sg = cell
        m = LAM * slope
        for o in s["obs"]:
            if not lo <= o["t"] < hi:
                continue
            out.append({"date": o["date"], "fwd": o["fwd"],
                        "z": (o["price"] - (anchor + m * o["t"])) / (sg * f(o["t"])),
                        "sign": "+" if m > 0 else "-"})
    return out


def run():
    eps = load_eps_yahoo(UNIVERSE)
    f = min(min(d for d, _ in v) for v in eps.values())
    l = max(max(d for d, _ in v) for v in eps.values())
    px = load_prices(UNIVERSE, _shift(f, -400), _shift(l, 200))
    prep = {t: prepare(px[t], eps[t], obs_from=WIN[1], windows=[WIN]) for t in UNIVERSE}
    print(f"\n觀測 {sum(len(s['obs']) for t in UNIVERSE for s in prep[t])} 天，"
          f"全部在畫線範圍之外，λ={LAM}")

    print("\n【A】三種帶寬 × 離財報幾天（14 檔好公司，四區勝率）")
    print(f"  {'帶寬':<7}{'離財報':<11}{'超出下緣':>9}{'中間偏下':>9}{'中間偏上':>9}"
          f"{'超出上緣':>9}{'高低差':>8}{'單調':>6}{'|z|':>7}{'觸發':>8}")
    res = {}
    for kind in WIDEN:
        for lo, hi in BUCKETS:
            r = [o for t in GOOD for o in rows_for(prep[t], kind, lo, hi)]
            g = grade(r)
            res[(kind, lo)] = g
            spread = g["zones"][0] - g["zones"][3]
            print(f"  {kind:<7}{f'{lo}-{hi} 天':<11}"
                  + "".join(f"{x:>8.1f}%" for x in g["zones"])
                  + f"{spread:>+8.1f}{'✅' if g['mono'] else '❌':>6}"
                  + f"{g['absz']:>7.2f}{g['fire']:>7.1f}%")
        print()

    print("【B】整季一起看（不分組）")
    print(f"  {'帶寬':<7}{'超出下緣':>9}{'中間偏下':>9}{'中間偏上':>9}{'超出上緣':>9}"
          f"{'單調':>6}{'|z|':>7}{'觸發':>8}{'14 檔優勢':>22}{'全部 30 檔':>22}")
    for kind in WIDEN:
        r = [o for t in GOOD for o in rows_for(prep[t], kind)]
        g = grade(r)
        cg, _ = bootstrap({t: thin(rows_for(prep[t], kind)) for t in GOOD})
        ca, _ = bootstrap({t: thin(rows_for(prep[t], kind)) for t in UNIVERSE})
        print(f"  {kind:<7}" + "".join(f"{x:>8.1f}%" for x in g["zones"])
              + f"{'✅' if g['mono'] else '❌':>6}{g['absz']:>7.2f}{g['fire']:>7.1f}%"
              + f"{f'{cg[1]:+.1f} [{cg[0]:+.1f},{cg[2]:+.1f}]':>20}"
              + f"{f'{ca[1]:+.1f} [{ca[0]:+.1f},{ca[2]:+.1f}]':>20}")

    chart(res)
    return res


def chart(res):
    fig, ax = plt.subplots(1, 2, figsize=(14, 5))
    xs = [lo for lo, _ in BUCKETS]
    col = {"flat": "#d62728", "sqrt": "#2ca02c", "lin": "#1f77b4"}
    for kind in WIDEN:
        sp = [res[(kind, lo)]["zones"][0] - res[(kind, lo)]["zones"][3] for lo in xs]
        ax[0].plot(xs, sp, "o-", color=col[kind], label=kind)
        ax[1].plot(xs, [res[(kind, lo)]["absz"] for lo in xs], "o-", color=col[kind], label=kind)
    ax[0].axhline(0, c="k", lw=1)
    ax[0].set_xlabel("days since earnings"); ax[0].set_ylabel("below-lower minus above-upper (pp)")
    ax[0].legend(fontsize=9)
    ax[0].set_title("Does widening the band keep it meaningful late in the quarter?\n"
                    "higher = the four zones still separate winners from losers")
    ax[1].axhspan(0.5, 0.9, color="#2ca02c", alpha=.12)
    ax[1].set_xlabel("days since earnings"); ax[1].set_ylabel("median |z|")
    ax[1].legend(fontsize=9)
    ax[1].set_title("A well-scaled band keeps |z| flat across the quarter")
    fig.tight_layout()
    fig.savefig("shots/cone-stage9c.png", dpi=110)
    print("\n圖：shots/cone-stage9c.png")


if __name__ == "__main__":
    run()
