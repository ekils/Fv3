"""第八階段：只換轉換函數，其他全部固定。

前面幾階段把「轉換、窗口、收縮」混在一起比，比不出是誰的功勞。
這裡窗口一律用財報前 1 週～後 3 週、x 一律用真實日曆天、
收縮倍數 λ 一律掃同一組，只換 sqrt / log / 原始價。

RAW_REF 是對照組：ref 現況（sqrt + x 用第幾筆 + 不做逆轉換），
用來看「單位錯誤」到底幫了多少忙。
"""

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from slope_backtest import (
    _d, _ols, _shift, _window, load_prices, segments, slope_ref,
)
from band_backtest import sigma_at, zone
from zone_backtest import FWD, TECH, NEW, load_eps_yahoo
from confidence_backtest import LAGGARD, BUY, K, thin, split, win, bootstrap

UNIVERSE = TECH + NEW + LAGGARD
LAMS = (1.0, 0.5, 0.25, 0.1, 0.05)
EPS_W = 0.1

# 轉換函數與它的逆。中心線一律是 inv(T(anchor) + λ*m*t)，
# 逆轉換不能省 —— 省掉就是 ref 現在犯的錯之一。
TF = {
    "sqrt": (np.sqrt, lambda v: np.where(v > 0, v, 0) ** 2),
    "log": (np.log, np.exp),
    "raw": (lambda v: v, lambda v: v),
}


def centre(win_rows, anchor, eps, name, lam):
    """回傳 t → 中心線股價；斜率在轉換後的空間配，再乘 λ 收縮，最後逆轉換。"""
    f, inv = TF[name]
    x = np.array([(_d(d) - _d(win_rows[0][0])).days for d, _ in win_rows], float)
    y = f(np.array([c for _, c in win_rows]))
    if len(set(x)) < 2 or not np.isfinite(y).all():
        return None
    m = _ols(x, y)

    # 盈餘項也要在同一個空間裡，否則又是單位不一致
    if len(eps) < 8:
        return None
    now, ago = sum(eps[0:4]), sum(eps[4:8])
    if not (now > 0 and ago > 0):
        return None
    growth = now / ago - 1
    drift = {"sqrt": f(anchor) * growth / 2, "log": growth,
             "raw": anchor * growth}[name] / 365.25

    s = lam * (m + EPS_W * drift) / (1 + EPS_W)
    a = f(anchor)
    return lambda t: float(inv(a + s * t)), s


def observations(closes, eps_rows):
    idx = {d: i for i, (d, _) in enumerate(closes)}
    out = []
    for j, seg in enumerate(segments(closes, eps_rows)):
        a, eps = seg["anchor"], seg["eps"]
        sg = sigma_at(closes, [r for r, _ in eps_rows[j + 1:]], "std")
        m_ref = slope_ref(seg["full"], eps, a)
        if not sg or m_ref is None:
            continue
        built = {}
        for name in TF:
            for lam in LAMS:
                c = centre(seg["full"], a, eps, name, lam)
                if c is None:
                    built = None
                    break
                built[(name, lam)] = c
            if built is None:
                break
        if built is None:
            continue
        for d, price in _window(closes, seg["rel"], seg["next"]):
            i = idx[d]
            if i + FWD >= len(closes):
                continue
            t = (_d(d) - _d(seg["rel"])).days
            row = {"date": d, "fwd": (closes[i + FWD][1] / price - 1) * 100,
                   "REF": {"sign": "+" if m_ref > 0 else "-",
                           "z": (price - (a + m_ref * t)) / sg}}
            for k, (fn, s) in built.items():
                row[f"{k[0]}@{k[1]}"] = {"sign": "+" if s > 0 else "-",
                                         "z": (price - fn(t)) / sg}
            out.append(row)
    return out


def flat(obs, k):
    return [{"date": o["date"], "fwd": o["fwd"], **o[k]} for o in obs]


def run():
    eps = load_eps_yahoo(UNIVERSE)
    f = min(min(d for d, _ in v) for v in eps.values())
    l = max(max(d for d, _ in v) for v in eps.values())
    px = load_prices(UNIVERSE, _shift(f, -400), _shift(l, 200))
    raw = {t: observations(px[t], eps[t]) for t in UNIVERSE}
    ind = {t: thin(v) for t, v in raw.items()}
    allo = [o for t in UNIVERSE for o in raw[t]]
    good = TECH + NEW
    print(f"\n觀測 {len(allo)} 天 → 獨立樣本 {sum(len(v) for v in ind.values())} 筆")

    keys = ["REF"] + [f"{n}@{l}" for n in TF for l in LAMS]

    print("\n斜率有多陡（每 90 天中心線移動幾 %，取絕對值中位數）")
    print(f"  {'算法':<12}{'90天移動':>10}{'|z|中位':>10}{'觸發下緣':>10}{'正斜率':>9}")
    for k in keys:
        z = np.array([o[k]["z"] for o in allo])
        pos = 100 * np.mean([o[k]["sign"] == "+" for o in allo])
        print(f"  {k:<12}{'—':>10}{np.median(abs(z)):>10.2f}"
              f"{100 * float((z < -K).mean()):>9.1f}%{pos:>8.1f}%")

    print("\n四區勝率（14 檔好公司，k=1.5）—— 單調下降才是好門檻")
    g14 = [o for t in good for o in raw[t]]
    base = win(np.array([o["fwd"] for o in g14]))
    print(f"  基準 {base:.1f}%")
    print(f"  {'算法':<12}{'超出下緣':>10}{'中間偏下':>10}{'中間偏上':>10}{'超出上緣':>10}{'單調?':>8}")
    mono = {}
    for k in keys:
        fl = flat(g14, k)
        b = {}
        for o in fl:
            b.setdefault(zone(o["z"], K), []).append(o["fwd"])
        v = [win(np.array(b[z])) if z in b else np.nan
             for z in ("below", "lower", "upper", "above")]
        ok = all(v[i] >= v[i + 1] - 0.3 for i in range(3))
        mono[k] = ok
        print(f"  {k:<12}" + "".join(f"{x:>9.1f}%" for x in v) +
              f"{'✅' if ok else '❌':>8}")

    print("\n買進側 − 迴避側 勝率差（獨立樣本、95% CI 按股票 bootstrap）")
    print(f"  {'算法':<12}{'14 檔好公司':>24}{'全部 30 檔':>24}")
    res = {}
    for k in keys:
        cells = []
        for gname, ts in (("good", good), ("all", UNIVERSE)):
            ci, _ = bootstrap({t: flat(ind[t], k) for t in ts})
            res[(k, gname)] = ci
            cells.append(f"{ci[1]:+.1f} [{ci[0]:+.1f},{ci[2]:+.1f}]")
        print(f"  {k:<12}{cells[0]:>22}{cells[1]:>22}")

    chart(raw, res, keys, mono)
    return raw, res


def chart(raw, res, keys, mono):
    fig, ax = plt.subplots(2, 2, figsize=(15, 9))
    col = {"sqrt": "#1f77b4", "log": "#ff7f0e", "raw": "#2ca02c"}

    # ① λ 掃描：三種轉換的優勢曲線（14 檔好公司）
    for n in TF:
        v = [res[(f"{n}@{l}", "good")][1] for l in LAMS]
        lo = [res[(f"{n}@{l}", "good")][0] for l in LAMS]
        hi = [res[(f"{n}@{l}", "good")][2] for l in LAMS]
        ax[0][0].plot(LAMS, v, "o-", color=col[n], label=n)
        ax[0][0].fill_between(LAMS, lo, hi, color=col[n], alpha=.12)
    r = res[("REF", "good")]
    ax[0][0].axhline(r[1], ls="--", c="k", label=f"REF as-is {r[1]:+.1f}")
    ax[0][0].axhline(0, c="k", lw=1)
    ax[0][0].set_xscale("log"); ax[0][0].set_xlabel("shrinkage lambda (1.0 = no shrinkage)")
    ax[0][0].set_ylabel("buy minus avoid (pp)"); ax[0][0].legend(fontsize=8)
    ax[0][0].set_title("Same window, same x-axis, only the transform differs\n"
                       "14 good companies, shaded = 95% CI")

    # ② 同上，全部 30 檔
    for n in TF:
        v = [res[(f"{n}@{l}", "all")][1] for l in LAMS]
        ax[0][1].plot(LAMS, v, "o-", color=col[n], label=n)
    r = res[("REF", "all")]
    ax[0][1].axhline(r[1], ls="--", c="k", label=f"REF as-is {r[1]:+.1f}")
    ax[0][1].axhline(0, c="k", lw=1)
    ax[0][1].set_xscale("log"); ax[0][1].set_xlabel("shrinkage lambda")
    ax[0][1].legend(fontsize=8); ax[0][1].set_title("all 30 tickers")

    # ③ 不收縮的時候有多陡：|z| 分布
    allo = [o for t in raw for o in raw[t]]
    bins = np.linspace(-5, 5, 90)
    for n in TF:
        ax[1][0].hist([o[f"{n}@1.0"]["z"] for o in allo], bins, histtype="step",
                      lw=1.8, color=col[n], label=f"{n} (no shrinkage)", density=True)
    ax[1][0].hist([o["REF"]["z"] for o in allo], bins, histtype="step", lw=1.8,
                  color="k", label="REF as-is", density=True)
    for s in (-K, K):
        ax[1][0].axvline(s, ls="--", c="gray", lw=1)
    ax[1][0].legend(fontsize=8); ax[1][0].set_xlabel("z")
    ax[1][0].set_title("Without shrinkage: how far the line runs from the price\n"
                       "fat tails = the line is too steep")

    # ④ 四區單調性
    g14 = [o for t in TECH + NEW for o in raw[t]]
    zs = ["below", "lower", "upper", "above"]
    for n in TF:
        for l, alpha in ((1.0, .35), (0.1, 1.0)):
            k = f"{n}@{l}"
            b = {}
            for o in flat(g14, k):
                b.setdefault(zone(o["z"], K), []).append(o["fwd"])
            ax[1][1].plot(range(4), [win(np.array(b[z])) if z in b else np.nan for z in zs],
                          "o-", color=col[n], alpha=alpha,
                          label=f"{n} lam={l}", lw=2 if l == 0.1 else 1)
    b = {}
    for o in flat(g14, "REF"):
        b.setdefault(zone(o["z"], K), []).append(o["fwd"])
    ax[1][1].plot(range(4), [win(np.array(b[z])) if z in b else np.nan for z in zs],
                  "s--", color="k", label="REF as-is", lw=2)
    ax[1][1].axhline(win(np.array([o["fwd"] for o in g14])), ls=":", c="gray")
    ax[1][1].set_xticks(range(4))
    ax[1][1].set_xticklabels(["below\nlower", "lower\nhalf", "upper\nhalf", "above\nupper"])
    ax[1][1].set_ylabel("% positive 60d"); ax[1][1].legend(fontsize=7)
    ax[1][1].set_title("4-zone monotonicity, 14 good companies")

    fig.tight_layout()
    fig.savefig("shots/transform-stage8.png", dpi=110)
    print("\n圖：shots/transform-stage8.png")


if __name__ == "__main__":
    run()
