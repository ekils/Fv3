"""第六階段：把我建議的算法實作出來，跟 REF、Fv3 在同一批日子上對打。

候選：
  REF   現在 ref 在用的（sqrt 價格 + 第幾筆 x + sqrt EPS 比率）
  FV3   現在 Fv3 在用的（原始價 + 整季窗口 + 日曆天）
  P0    建議版：log 價格 + 財報 4 週窗口 + 日曆天，斜率明確乘 0.1
  P0S   同 P0，但跳過財報後 5 天情緒期
  PD    同 P0，但不是收縮到 0，而是收縮到 5 年 EPS 成長斜率（第一階段的 D）

P0 和 PD 的差別就是「平的線要平向哪裡」：P0 平向水平，PD 平向長期盈餘成長。
"""

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from slope_backtest import (
    SETTLE_DAYS, _d, _shift, _window, eps_long_slope, load_prices,
    segments, slope_log, slope_ref,
)
from band_backtest import sigma_at, zone
from zone_backtest import ACTION, FWD, TECH, NEW, load_eps_yahoo
from confidence_backtest import LAGGARD, BUY, K, thin, split, win, bootstrap
from fv3_vs_ref import fv3_slope

UNIVERSE = TECH + NEW + LAGGARD
LAM = 0.1
CANDS = ("REF", "FV3", "P0", "P0S", "PD")


def centres(seg, closes, eps_rows_tail):
    """每個候選回傳 (斜率, 中心線函式 t→價格)。算不出來就 None。"""
    a, out = seg["anchor"], {}

    m = slope_ref(seg["full"], seg["eps"], a)
    out["REF"] = None if m is None else (m, lambda t, m=m: a + m * t)

    # log 價格 + 日曆天 + EPS 漂移，單位是「每天幾 %」
    for key, winrows in (("P0", seg["full"]), ("P0S", seg["settled"])):
        m = slope_log(winrows, seg["eps"], a)
        if m is None:
            out[key] = None
            continue
        s = LAM * m                                   # 收縮到 0（水平）
        out[key] = (s, lambda t, s=s: a * np.exp(s * t))

    m, long = slope_log(seg["settled"], seg["eps"], a), eps_long_slope(seg["eps"])
    if m is None or long is None:
        out["PD"] = None
    else:
        s = LAM * m + (1 - LAM) * long                # 收縮到 5 年盈餘斜率
        out["PD"] = (s, lambda t, s=s: a * np.exp(s * t))
    return out


def observations(closes, eps_rows) -> list[dict]:
    idx = {d: i for i, (d, _) in enumerate(closes)}
    out = []
    for j, seg in enumerate(segments(closes, eps_rows)):
        c = centres(seg, closes, eps_rows[j + 1:])
        sg = sigma_at(closes, [r for r, _ in eps_rows[j + 1:]], "std")
        if not sg or any(c[k] is None for k in CANDS if k != "FV3"):
            continue
        rel, end, a = seg["rel"], seg["next"], seg["anchor"]
        for d, price in _window(closes, rel, end):
            i = idx[d]
            if i + FWD >= len(closes):
                continue
            m3 = fv3_slope(closes, idx, rel, end, d, a, seg["eps"])
            if m3 is None:
                continue
            t = (_d(d) - _d(rel)).days
            row = {"date": d, "fwd": (closes[i + FWD][1] / price - 1) * 100,
                   "FV3": {"sign": "+" if m3 > 0 else "-",
                           "z": (price - (a + m3 * t)) / sg}}
            for k in CANDS:
                if k == "FV3":
                    continue
                m, f = c[k]
                row[k] = {"sign": "+" if m > 0 else "-", "z": (price - f(t)) / sg}
            out.append(row)
    return out


def flat(obs, k):
    return [{"date": o["date"], "fwd": o["fwd"], **o[k]} for o in obs]


def cells(obs):
    g = {}
    for o in obs:
        g.setdefault((o["sign"], zone(o["z"], K)), []).append(o["fwd"])
    return g


def table(title, obs):
    base = np.array([o["fwd"] for o in obs])
    bw = win(base)
    print(f"\n{title}（樣本 {len(obs)}、基準 {np.median(base):+.2f}% / {bw:.1f}%）")
    print(f"  {'斜率':<5}{'位置':<10}{'判斷':<10}{'中位':>9}{'勝率':>9}{'超額':>8}{'佔比':>8}")
    g = cells(obs)
    for sign in ("+", "-"):
        for z, name in (("below", "超出下緣"), ("lower", "中間偏下"),
                        ("upper", "中間偏上"), ("above", "超出上緣")):
            v = np.array(g.get((sign, z), []))
            if not len(v):
                print(f"  {sign:<5}{name:<10}{ACTION[(sign, z)]:<10}{'（無）':>9}")
                continue
            print(f"  {sign:<5}{name:<10}{ACTION[(sign, z)]:<10}"
                  f"{np.median(v):>8.2f}%{win(v):>8.1f}%{win(v) - bw:>7.1f}"
                  f"{100 * len(v) / len(base):>7.1f}%")


def run():
    eps = load_eps_yahoo(UNIVERSE)
    f = min(min(d for d, _ in v) for v in eps.values())
    l = max(max(d for d, _ in v) for v in eps.values())
    px = load_prices(UNIVERSE, _shift(f, -400), _shift(l, 200))

    raw = {t: observations(px[t], eps[t]) for t in UNIVERSE}
    n = sum(len(v) for v in raw.values())
    ind = {t: thin(v) for t, v in raw.items()}
    print(f"\n共同觀測 {n} 天（五種算法同一批日子）→ 獨立樣本 "
          f"{sum(len(v) for v in ind.values())} 筆")

    good = TECH + NEW
    for k in CANDS:
        table(f"{k} · 14 檔好公司", flat([o for t in good for o in raw[t]], k))

    print("\n" + "=" * 78)
    print("五方對決：買進側 − 迴避側 的上漲比率差（獨立樣本、95% CI 按股票 bootstrap）")
    print(f"  {'算法':<6}{'14 檔好公司':>26}{'16 檔落後股':>26}{'全部 30 檔':>26}")
    res = {}
    for k in CANDS:
        cells_ = []
        for gname, ts in (("good", good), ("lag", LAGGARD), ("all", UNIVERSE)):
            ci, _ = bootstrap({t: flat(ind[t], k) for t in ts})
            res[(k, gname)] = ci
            cells_.append(f"{ci[1]:+.1f} [{ci[0]:+.1f},{ci[2]:+.1f}]")
        print(f"  {k:<6}{cells_[0]:>22}{cells_[1]:>22}{cells_[2]:>22}")

    print("\n訊號特性（全部 30 檔）")
    print(f"  {'算法':<6}{'|z| 中位':>10}{'觸發下緣':>10}{'斜率為正':>10}")
    allo = [o for t in UNIVERSE for o in raw[t]]
    for k in CANDS:
        z = np.array([o[k]["z"] for o in allo])
        pos = 100 * np.mean([o[k]["sign"] == "+" for o in allo])
        print(f"  {k:<6}{np.median(abs(z)):>10.2f}"
              f"{100 * float((z < -K).mean()):>9.1f}%{pos:>9.1f}%")

    print("\n逐年（全部 30 檔、獨立樣本、買進側−迴避側）")
    print(f"  {'年':<6}" + "".join(f"{k:>8}" for k in CANDS))
    allind = [o for t in UNIVERSE for o in ind[t]]
    tally = {k: 0 for k in CANDS}
    yrs = sorted({o["date"][:4] for o in allind})
    for y in yrs:
        sub = [o for o in allind if o["date"][:4] == y]
        row = []
        for k in CANDS:
            b, a = split(flat(sub, k))
            d = win(b) - win(a) if len(b) > 4 and len(a) > 4 else np.nan
            tally[k] += d > 0
            row.append(f"{d:>+8.1f}" if d == d else f"{'--':>8}")
        print(f"  {y:<6}" + "".join(row))
    print(f"  {'有效':<6}" + "".join(f"{tally[k]:>8}" for k in CANDS) + f"   / {len(yrs)}")

    chart(raw, ind, res)
    return raw, ind, res


def chart(raw, ind, res):
    fig, ax = plt.subplots(2, 2, figsize=(15, 9))
    col = dict(zip(CANDS, ["#1f77b4", "#ff7f0e", "#2ca02c", "#17becf", "#9467bd"]))

    # ① 三組的優勢 + 信賴區間
    gs = ["good", "lag", "all"]
    x = np.arange(3); w = 0.8 / len(CANDS)
    for j, k in enumerate(CANDS):
        med = [res[(k, g)][1] for g in gs]
        lo = [res[(k, g)][1] - res[(k, g)][0] for g in gs]
        hi = [res[(k, g)][2] - res[(k, g)][1] for g in gs]
        ax[0][0].bar(x + j * w, med, w, yerr=[lo, hi], capsize=3,
                     color=col[k], label=k)
    ax[0][0].axhline(0, c="k", lw=1)
    ax[0][0].set_xticks(x + 0.4)
    ax[0][0].set_xticklabels(["14 good cos", "16 laggards", "all 30"])
    ax[0][0].set_ylabel("buy minus avoid win rate (pp)"); ax[0][0].legend(fontsize=8)
    ax[0][0].set_title("Head to head, 5 algorithms\nindependent samples, 95% CI by ticker")

    # ② 四區勝率（14 檔好公司）
    good = [o for t in TECH + NEW for o in raw[t]]
    zs = ["below", "lower", "upper", "above"]
    for k in CANDS:
        fl = flat(good, k)
        g = {}
        for o in fl:
            g.setdefault(zone(o["z"], K), []).append(o["fwd"])
        ax[0][1].plot(range(4), [win(np.array(g[z])) if z in g else np.nan for z in zs],
                      "o-", color=col[k], label=k)
    ax[0][1].axhline(win(np.array([o["fwd"] for o in good])), ls="--", c="k", lw=1)
    ax[0][1].set_xticks(range(4))
    ax[0][1].set_xticklabels(["below\nlower", "lower\nhalf", "upper\nhalf", "above\nupper"])
    ax[0][1].set_ylabel("% positive 60d"); ax[0][1].legend(fontsize=8)
    ax[0][1].set_title("4 zones, 14 good companies\nsteeper downward slope = better threshold")

    # ③ z 分布
    allo = [o for t in UNIVERSE for o in raw[t]]
    bins = np.linspace(-4, 4, 80)
    for k in CANDS:
        ax[1][0].hist([o[k]["z"] for o in allo], bins, histtype="step", lw=1.8,
                      color=col[k], label=k, density=True)
    for s in (-K, K):
        ax[1][0].axvline(s, ls="--", c="k", lw=1)
    ax[1][0].legend(fontsize=8); ax[1][0].set_xlabel("z = (price - centre) / sigma")
    ax[1][0].set_title("Deviation from the centre line\nnarrow peak = line chases price")

    # ④ 逐年
    allind = [o for t in UNIVERSE for o in ind[t]]
    yrs = sorted({o["date"][:4] for o in allind})
    for k in CANDS:
        v = []
        for y in yrs:
            sub = [o for o in allind if o["date"][:4] == y]
            b, a = split(flat(sub, k))
            v.append(win(b) - win(a) if len(b) > 4 and len(a) > 4 else np.nan)
        ax[1][1].plot(yrs, v, "o-", ms=3, color=col[k], label=k, lw=1.2)
    ax[1][1].axhline(0, c="k", lw=1)
    ax[1][1].tick_params(axis="x", rotation=90)
    ax[1][1].set_ylabel("buy minus avoid (pp)"); ax[1][1].legend(fontsize=8)
    ax[1][1].set_title("Year by year")

    fig.tight_layout()
    fig.savefig("shots/proposal-stage6.png", dpi=110)
    print("\n圖：shots/proposal-stage6.png")


if __name__ == "__main__":
    run()
