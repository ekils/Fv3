"""第四階段：把前三階段「沒驗到」的四個坑補起來。

1. 生存者偏誤：加 16 檔這 20 年表現很差／曾經瀕死的公司。
2. 觀測重疊：每檔每 60 交易日只取一筆，做成互不重疊的獨立樣本。
3. 沒有信賴區間：用 ticker 分群 bootstrap，看訊號的優勢會不會被抽掉。
4. 沒有分年：2002–2026 逐年拆，看是不是靠某幾年撐起來。
"""

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from slope_backtest import _shift, load_prices
from zone_backtest import FWD, TECH, NEW, load_eps_yahoo, observations
from band_backtest import zone

# 前三階段的 14 檔全是今天還活著的好公司。這 16 檔是對照組：
# 長年落後大盤、砍過股利、出過大事、或被時代輾過的。
LAGGARD = ("INTC", "GE", "F", "T", "VZ", "WBA", "PFE", "KHC",
           "XOM", "CSCO", "DIS", "NKE", "IBM", "MMM", "BA", "C")
UNIVERSE = TECH + NEW + LAGGARD
K = 1.5
BUY = {("+", "below"), ("+", "lower"), ("-", "below"), ("-", "lower")}


def thin(obs: list[dict]) -> list[dict]:
    """每 60 個交易日只留一筆 —— 兩筆之間不共用任何一天的報酬。"""
    obs = sorted(obs, key=lambda o: o["date"])
    out, last = [], -10**9
    for i, o in enumerate(obs):
        if i - last >= FWD:
            out.append(o)
            last = i
    return out


def split(obs):
    buy = [o["fwd"] for o in obs if (o["sign"], zone(o["z"], K)) in BUY]
    avoid = [o["fwd"] for o in obs if (o["sign"], zone(o["z"], K)) not in BUY]
    return np.array(buy), np.array(avoid)


def win(v):
    return 100 * float((v > 0).mean()) if len(v) else np.nan


def line(name, obs):
    b, a = split(obs)
    allv = np.array([o["fwd"] for o in obs])
    print(f"  {name:<28}{len(obs):>7}{win(b):>9.1f}%{win(a):>9.1f}%"
          f"{win(b) - win(a):>8.1f}{np.median(b):>9.2f}%{np.median(a):>9.2f}%"
          f"{np.median(allv):>9.2f}%")
    return win(b) - win(a)


def bootstrap(by_ticker, n=2000, seed=0):
    """按「整檔股票」重抽，不是按天重抽 —— 同一檔內部的相關性才不會被當成獨立資訊。"""
    rng = np.random.default_rng(seed)
    names = list(by_ticker)
    out = []
    for _ in range(n):
        pick = rng.choice(names, len(names), replace=True)
        pool = [o for t in pick for o in by_ticker[t]]
        b, a = split(pool)
        if len(b) > 20 and len(a) > 20:
            out.append(win(b) - win(a))
    return np.percentile(out, [2.5, 50, 97.5]), np.array(out)


def run():
    eps = load_eps_yahoo(UNIVERSE)
    first = min(min(d for d, _ in v) for v in eps.values())
    last = max(max(d for d, _ in v) for v in eps.values())
    px = load_prices(UNIVERSE, _shift(first, -400), _shift(last, 200))

    full = {t: observations(px[t], eps[t]) for t in UNIVERSE}
    ind = {t: thin(v) for t, v in full.items()}
    print(f"\n重疊樣本 {sum(len(v) for v in full.values())} 天"
          f"　→　獨立樣本 {sum(len(v) for v in ind.values())} 筆")

    hdr = (f"  {'分組':<28}{'樣本':>7}{'買進側勝率':>10}{'迴避側勝率':>10}"
           f"{'差':>8}{'買進中位':>9}{'迴避中位':>9}{'全體中位':>9}")

    print("\n【坑 2】獨立樣本（每 60 交易日一筆，互不重疊）")
    print(hdr)
    groups = {"14 檔贏家（原樣本）": TECH + NEW,
              "16 檔落後股（新對照組）": LAGGARD,
              "全部 30 檔": UNIVERSE}
    for name, ts in groups.items():
        line(name, [o for t in ts for o in ind[t]])

    print("\n【坑 1】生存者偏誤：逐檔（獨立樣本、勝率差）")
    print(f"  {'股票':<8}{'樣本':>6}{'買進側':>9}{'迴避側':>9}{'差':>8}{'20年總報酬':>12}")
    worked = 0
    for t in UNIVERSE:
        b, a = split(ind[t])
        if len(b) < 10 or len(a) < 10:
            continue
        tot = (px[t][-1][1] / px[t][0][1] - 1) * 100
        d = win(b) - win(a)
        worked += d > 0
        print(f"  {t:<8}{len(ind[t]):>6}{win(b):>8.1f}%{win(a):>8.1f}%"
              f"{d:>7.1f}{tot:>11.0f}%  {'✅' if d > 0 else '❌'}")
    print(f"  → 30 檔裡 {worked} 檔方向正確")

    print("\n【坑 3】信賴區間：按股票重抽 2000 次（全部 30 檔、獨立樣本）")
    ci, dist = bootstrap(ind)
    print(f"  買進側 − 迴避側 勝率差：中位 {ci[1]:+.1f} 個百分點，"
          f"95% 區間 [{ci[0]:+.1f}, {ci[2]:+.1f}]")
    print(f"  區間不含 0？{'是 → 訊號站得住' if ci[0] > 0 else '否 → 可能是運氣'}")

    print("\n【坑 4】逐年（全部 30 檔、獨立樣本）")
    print(f"  {'年':<6}{'樣本':>6}{'買進側':>9}{'迴避側':>9}{'差':>8}{'全體中位':>10}")
    allo = [o for t in UNIVERSE for o in ind[t]]
    good = 0
    years = sorted({o["date"][:4] for o in allo})
    for y in years:
        sub = [o for o in allo if o["date"][:4] == y]
        b, a = split(sub)
        if len(b) < 5 or len(a) < 5:
            continue
        d = win(b) - win(a)
        good += d > 0
        m = np.median([o["fwd"] for o in sub])
        print(f"  {y:<6}{len(sub):>6}{win(b):>8.1f}%{win(a):>8.1f}%"
              f"{d:>7.1f}{m:>9.2f}%  {'✅' if d > 0 else '❌'}")
    print(f"  → 有效年份 {good} / {len(years)}")

    chart(ind, dist, ci, groups, px)
    return ind


def chart(ind, dist, ci, groups, px):
    fig, ax = plt.subplots(2, 2, figsize=(15, 9))

    # ① bootstrap 分布
    ax[0][0].hist(dist, bins=50, color="#1f77b4", alpha=.8)
    ax[0][0].axvline(0, c="k", lw=2)
    ax[0][0].axvline(ci[0], c="r", ls="--"); ax[0][0].axvline(ci[2], c="r", ls="--")
    ax[0][0].set_title(f"Bootstrap by ticker (2000x): buy-side minus avoid-side win rate\n"
                       f"median {ci[1]:+.1f}pp, 95% CI [{ci[0]:+.1f}, {ci[2]:+.1f}]")
    ax[0][0].set_xlabel("difference in % of positive 60d outcomes (pp)")

    # ② 贏家 vs 落後股
    names, bw, aw = [], [], []
    for n, ts in groups.items():
        b, a = split([o for t in ts for o in ind[t]])
        names.append(n.split("（")[0]); bw.append(win(b)); aw.append(win(a))
    x = np.arange(len(names))
    for j, (v, c, l) in enumerate(((bw, "#2ca02c", "buy side"), (aw, "#d62728", "avoid side"))):
        b = ax[0][1].bar(x + j * .4, v, .4, color=c, label=l)
        ax[0][1].bar_label(b, fmt="%.1f%%", fontsize=9)
    ax[0][1].set_xticks(x + .2)
    ax[0][1].set_xticklabels(["14 winners", "16 laggards", "all 30"])
    ax[0][1].set_ylim(0, 85); ax[0][1].legend()
    ax[0][1].set_title("Survivorship check: does the signal survive on bad companies?\n"
                       "independent (non-overlapping) samples")

    # ③ 逐檔勝率差 vs 該檔 20 年總報酬
    xs, ys, ls = [], [], []
    for t in ind:
        b, a = split(ind[t])
        if len(b) < 10 or len(a) < 10:
            continue
        xs.append((px[t][-1][1] / px[t][0][1] - 1) * 100)
        ys.append(win(b) - win(a)); ls.append(t)
    ax[1][0].scatter(xs, ys, c=["#2ca02c" if y > 0 else "#d62728" for y in ys])
    for xx, yy, ll in zip(xs, ys, ls):
        ax[1][0].annotate(ll, (xx, yy), fontsize=7, xytext=(3, 3), textcoords="offset points")
    ax[1][0].axhline(0, c="k", lw=1); ax[1][0].set_xscale("symlog")
    ax[1][0].set_xlabel("total price return over the sample (%, log scale)")
    ax[1][0].set_ylabel("buy-side minus avoid-side win rate (pp)")
    ax[1][0].set_title("Is the edge just 'the stock went up'?\nif so, all the green would sit on the right")

    # ④ 逐年
    allo = [o for t in ind for o in ind[t]]
    ys2, ds = [], []
    for y in sorted({o["date"][:4] for o in allo}):
        sub = [o for o in allo if o["date"][:4] == y]
        b, a = split(sub)
        if len(b) < 5 or len(a) < 5:
            continue
        ys2.append(y); ds.append(win(b) - win(a))
    ax[1][1].bar(ys2, ds, color=["#2ca02c" if d > 0 else "#d62728" for d in ds])
    ax[1][1].axhline(0, c="k", lw=1)
    ax[1][1].tick_params(axis="x", rotation=90)
    ax[1][1].set_ylabel("buy minus avoid win rate (pp)")
    ax[1][1].set_title("Year by year: green = the band helped that year")

    fig.tight_layout()
    fig.savefig("shots/confidence-stage4.png", dpi=110)
    print("\n圖：shots/confidence-stage4.png")


if __name__ == "__main__":
    run()
