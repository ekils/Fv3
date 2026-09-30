"""第九階段：財報後的情緒到底該看幾天？順便把收縮倍數一起掃。

前八階段每一支腳本都自己重寫一份算法，抄錯了也看不出來。這支不一樣 ——
它直接 import app/trend.py 的函式，測的就是線上跑的那份程式碼。

窗口一改，錨點、斜率、帶寬三樣東西會一起動（它們在 app/trend.py 裡本來就
共用同一段），所以不能只掃斜率。這裡三樣都跟著參數走。

觀測的日子不隨參數改變 —— 每一格都是同一批交易日、同一批未來報酬，
差別純粹在中心線畫在哪。這樣格子之間才能直接比。
"""

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from app.trend import _days, _price_slope, _shift, _sigma

from slope_backtest import _close_at, _d, _window, load_prices
from zone_backtest import FWD, TECH, NEW, load_eps_yahoo
from confidence_backtest import LAGGARD, K, thin, split, win, bootstrap

UNIVERSE = TECH + NEW + LAGGARD
GOOD = TECH + NEW

BEFORES = (0, 7, 14)
AFTERS = (7, 14, 21, 28, 42, 60)
LAMS = (0.05, 0.1, 0.2, 0.4, 1.0)
EPS_W = 0.1              # 跟 app/config.py 一致，這次不動它
SIGMA_QUARTERS = 5


def _rows(closes, day, before, after):
    """app/trend.py 的 _around，只是窗口寬度變成參數。"""
    return _window(closes, _shift(day, -before), _shift(day, after))


def _sigma_at(closes, rels, before, after):
    """app/trend.py 的 _sigmas：最近幾季、每季拆公布前後兩個均價，取相鄰差的標準差。"""
    means = []
    for r in rels[-SIGMA_QUARTERS:]:
        rows = _rows(closes, r, before, after)
        for half in ([c for d, c in rows if d < r], [c for d, c in rows if d >= r]):
            if half:
                means.append(sum(half) / len(half))
    return _sigma(means) if len(means) > 2 else 0.0


def prepare(closes, eps_rows, obs_from=max(AFTERS), windows=None):
    """每一季算出：觀測日＋未來報酬（不隨參數變），以及每個窗口的錨點與斜率。"""
    idx = {d: i for i, (d, _) in enumerate(closes)}
    segs = []
    for i in range(1, len(eps_rows)):
        rel, nxt = eps_rows[i][0], eps_rows[i - 1][0]
        if not 40 <= (_d(nxt) - _d(rel)).days <= 160:
            continue
        eps = [e for _, e in eps_rows[i:]]
        if len(eps) < 5 or eps[4] == 0:
            continue
        rels = [r for r, _ in eps_rows[i:]][::-1]      # 由舊到新，只到這一季

        obs = []
        # 觀測日必須晚於「最寬的那個窗口」的結尾，否則畫線時用到了那一天之後的股價，
        # 等於偷看未來 —— 而且窗口越寬偷得越多，寬窗口會不公平地贏。
        for d, price in _window(closes, _shift(rel, obs_from), nxt):
            j = idx[d]
            if j + FWD >= len(closes):
                continue
            obs.append({"date": d, "price": price, "t": _days(rel, d),
                        "fwd": (closes[j + FWD][1] / price - 1) * 100})
        if not obs:
            continue

        cells = {}
        for b, a in (windows or [(x, y) for x in BEFORES for y in AFTERS]):
            rows = _rows(closes, rel, b, a)
            sg = _sigma_at(closes, rels, b, a)
            if len({d for d, _ in rows}) < 2 or sg <= 0:
                continue
            anchor = float(np.median([c for _, c in rows]))
            m = _price_slope([{"date": d, "close": c} for d, c in rows])
            drift = anchor * (eps[0] / eps[4] - 1) / 365.25
            cells[(b, a)] = (anchor, (m + EPS_W * drift) / (1 + EPS_W), sg)
        if cells:
            segs.append({"obs": obs, "cells": cells})
    return segs


def flatten(segs, b, a, lam):
    """把一組參數攤平成回測要的觀測列表。算不出來的季直接跳過。"""
    out = []
    for s in segs:
        cell = s["cells"].get((b, a))
        if cell is None:
            continue
        anchor, slope, sg = cell
        m = lam * slope
        for o in s["obs"]:
            z = (o["price"] - (anchor + m * o["t"])) / sg
            out.append({"date": o["date"], "fwd": o["fwd"], "z": z,
                        "sign": "+" if m > 0 else "-"})
    return out


def zone4(z):
    if z < -K:
        return 0
    if z < 0:
        return 1
    return 2 if z <= K else 3


def grade(rows):
    """四區勝率、單調性、|z| 中位、下緣觸發率。"""
    z = np.array([r["z"] for r in rows])
    buckets = [[] for _ in range(4)]
    for r in rows:
        buckets[zone4(r["z"])].append(r["fwd"])
    v = [win(np.array(b)) if len(b) > 30 else np.nan for b in buckets]
    mono = all(v[i] >= v[i + 1] - 0.3 for i in range(3)) if not np.isnan(v).any() else False
    return {"zones": v, "mono": mono, "absz": float(np.median(abs(z))),
            "fire": 100 * float((z < -K).mean())}


def run():
    eps = load_eps_yahoo(UNIVERSE)
    f = min(min(d for d, _ in v) for v in eps.values())
    l = max(max(d for d, _ in v) for v in eps.values())
    px = load_prices(UNIVERSE, _shift(f, -400), _shift(l, 200))
    prep = {t: prepare(px[t], eps[t]) for t in UNIVERSE}
    print(f"\n共同觀測 {sum(len(s['obs']) for t in UNIVERSE for s in prep[t])} 天"
          f"（每一格都用完全同一批日子）")

    # ── 情緒衰減本身長什麼樣：財報後第 n 天相對財報前一週的平均漲幅
    print("\n【A】財報後的情緒衰減（全部 30 檔，相對公布前 5 天均價）")
    print(f"  {'第幾天':>8}{'平均漲幅':>12}{'每天新增':>12}")
    curve, prev = [], 0.0
    for day in (3, 5, 7, 10, 14, 21, 28, 35, 42, 60, 90):
        vals = []
        for t in UNIVERSE:
            for r, _ in eps[t]:
                pre = [c for d, c in _window(px[t], _shift(r, -7), _shift(r, -1))]
                at = _close_at(px[t], _shift(r, day))
                if len(pre) >= 3 and at:
                    vals.append(at / (sum(pre) / len(pre)) - 1)
        m = 100 * float(np.mean(vals))
        curve.append((day, m))
        print(f"  {day:>8}{m:>11.2f}%{(m - prev) / max(1, day - (curve[-2][0] if len(curve) > 1 else 0)):>11.3f}%")
        prev = m

    # ── 二維掃描
    good = {t: prep[t] for t in GOOD}
    print("\n【B】窗口 × 收縮：買進側 − 迴避側（14 檔好公司、獨立樣本、點估計）")
    print(f"  {'前/後':<10}" + "".join(f"{f'λ={x}':>9}" for x in LAMS) + f"{'|z|@最佳':>10}{'觸發':>8}")
    table, best = {}, []
    for b in BEFORES:
        for a in AFTERS:
            cells = []
            for lam in LAMS:
                rows = {t: thin(flatten(good[t], b, a, lam)) for t in GOOD}
                pool = [o for v in rows.values() for o in v]
                if len(pool) < 200:
                    cells.append(np.nan)
                    continue
                bs, av = split(pool)
                g = grade([o for t in GOOD for o in flatten(good[t], b, a, lam)])
                d = win(bs) - win(av)
                table[(b, a, lam)] = {"edge": d, **g}
                cells.append(d if g["mono"] else np.nan)
                if g["mono"]:
                    best.append((d, b, a, lam))
            key = max(((c, i) for i, c in enumerate(cells) if c == c), default=None)
            g = table.get((b, a, LAMS[key[1]])) if key else None
            print(f"  -{b:>2}/+{a:<5}" +
                  "".join(f"{c:>+9.1f}" if c == c else f"{'✗':>9}" for c in cells) +
                  (f"{g['absz']:>10.2f}{g['fire']:>7.1f}%" if g else ""))
    print("  ✗ = 四區不單調，這組參數的門檻是壞的")

    best.sort(reverse=True)
    print("\n【C】前 6 名（加上 95% 信賴區間，按股票 bootstrap）")
    print(f"  {'前/後/λ':<14}{'14 檔好公司':>24}{'全部 30 檔':>24}{'|z|':>7}{'觸發':>8}")
    top = []
    for d, b, a, lam in best[:6]:
        ci_g, _ = bootstrap({t: thin(flatten(prep[t], b, a, lam)) for t in GOOD})
        ci_all, _ = bootstrap({t: thin(flatten(prep[t], b, a, lam)) for t in UNIVERSE})
        g = table[(b, a, lam)]
        top.append((b, a, lam, ci_g, ci_all, g))
        print(f"  -{b}/+{a}/{lam:<8}"
              f"{f'{ci_g[1]:+.1f} [{ci_g[0]:+.1f},{ci_g[2]:+.1f}]':>22}"
              f"{f'{ci_all[1]:+.1f} [{ci_all[0]:+.1f},{ci_all[2]:+.1f}]':>22}"
              f"{g['absz']:>7.2f}{g['fire']:>7.1f}%")

    print("\n【D】現況對照（-7/+21，λ=1.0 就是今天 Fv3 整季回歸以外的樣子）")
    for lam in LAMS:
        g = table.get((7, 21, lam))
        if g:
            print(f"  -7/+21 λ={lam:<5} 優勢 {g['edge']:+.1f}　四區 "
                  f"{' '.join(f'{x:.1f}' for x in g['zones'])}　"
                  f"{'單調 ✅' if g['mono'] else '不單調 ❌'}　|z| {g['absz']:.2f}　"
                  f"觸發 {g['fire']:.1f}%")

    chart(curve, table, top)
    return table, top


def chart(curve, table, top):
    fig, ax = plt.subplots(2, 2, figsize=(15, 9))

    # ① 情緒衰減曲線
    d, m = zip(*curve)
    ax[0][0].plot(d, m, "o-", color="#1f77b4")
    for x in (21, 28):
        ax[0][0].axvline(x, ls="--", c="gray", lw=1)
    ax[0][0].annotate("current AFTER_DAYS = 21", (21, min(m)), fontsize=8, rotation=90)
    ax[0][0].set_xlabel("trading days after the earnings release")
    ax[0][0].set_ylabel("mean return vs the pre-release week (%)")
    ax[0][0].set_title("Post-earnings drift: when does it flatten out?\n"
                       "all 30 tickers, every quarter")

    # ② 熱圖：窗口 × λ 的優勢
    grid = np.full((len(BEFORES) * len(AFTERS), len(LAMS)), np.nan)
    labels = []
    for i, b in enumerate(BEFORES):
        for j, a in enumerate(AFTERS):
            labels.append(f"-{b}/+{a}")
            for k, lam in enumerate(LAMS):
                g = table.get((b, a, lam))
                if g and g["mono"]:
                    grid[i * len(AFTERS) + j][k] = g["edge"]
    im = ax[0][1].imshow(grid, aspect="auto", cmap="RdYlGn", vmin=-4, vmax=6)
    ax[0][1].set_xticks(range(len(LAMS)))
    ax[0][1].set_xticklabels([str(x) for x in LAMS])
    ax[0][1].set_yticks(range(len(labels)))
    ax[0][1].set_yticklabels(labels, fontsize=7)
    ax[0][1].set_xlabel("shrinkage lambda"); ax[0][1].set_ylabel("window (days before / after)")
    fig.colorbar(im, ax=ax[0][1], label="buy minus avoid (pp)")
    ax[0][1].set_title("Grey = the 4 zones are not monotone (broken threshold)\n"
                       "14 good companies")

    # ③ 前幾名的信賴區間
    names = [f"-{b}/+{a}\nlam={lam}" for b, a, lam, *_ in top]
    x = np.arange(len(top))
    for j, (key, c, lab) in enumerate(((3, "#2ca02c", "14 good cos"),
                                       (4, "#1f77b4", "all 30"))):
        med = [t[key][1] for t in top]
        lo = [t[key][1] - t[key][0] for t in top]
        hi = [t[key][2] - t[key][1] for t in top]
        ax[1][0].bar(x + j * .4, med, .4, yerr=[lo, hi], capsize=3, color=c, label=lab)
    ax[1][0].axhline(0, c="k", lw=1)
    ax[1][0].set_xticks(x + .2); ax[1][0].set_xticklabels(names, fontsize=7)
    ax[1][0].set_ylabel("buy minus avoid (pp)"); ax[1][0].legend(fontsize=8)
    ax[1][0].set_title("Top candidates, 95% CI by ticker bootstrap")

    # ④ AFTER_DAYS 對觸發率和 |z| 的影響（λ 固定 0.1）
    fire = [table[(7, a, 0.1)]["fire"] if (7, a, 0.1) in table else np.nan for a in AFTERS]
    absz = [table[(7, a, 0.1)]["absz"] if (7, a, 0.1) in table else np.nan for a in AFTERS]
    ax[1][1].plot(AFTERS, fire, "o-", color="#d62728", label="% of days below lower band")
    ax[1][1].axhspan(7, 13, color="#2ca02c", alpha=.12)
    a2 = ax[1][1].twinx()
    a2.plot(AFTERS, absz, "s--", color="#1f77b4", label="median |z|")
    ax[1][1].set_xlabel("AFTER_DAYS"); ax[1][1].set_ylabel("lower band fire rate (%)")
    a2.set_ylabel("median |z|")
    ax[1][1].legend(fontsize=8, loc="upper left"); a2.legend(fontsize=8, loc="upper right")
    ax[1][1].set_title("How often the band actually fires (lambda = 0.1)\n"
                       "green band = the healthy range")

    fig.tight_layout()
    fig.savefig("shots/window-stage9.png", dpi=110)
    print("\n圖：shots/window-stage9.png")


if __name__ == "__main__":
    run()
