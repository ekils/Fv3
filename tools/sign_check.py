"""第七階段：斜率的正負到底有沒有帶資訊？

使用者的設計初衷：斜率為負 = 這次 EPS 比之前低 + 財報後市場反應是負的。
所以要問三件事：
  1. 負斜率真的對應到「EPS 變差」嗎？（符號有沒有講到它該講的事）
  2. 控制住帶子位置之後，符號還有沒有額外資訊？（不是只是重複帶子）
  3. PD 是不是把這個維度殺掉了？（正斜率佔比）
"""

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from slope_backtest import (
    _d, _shift, _window, eps_long_slope, load_prices, segments, slope_log, slope_ref,
)
from band_backtest import sigma_at, zone
from zone_backtest import FWD, TECH, NEW, load_eps_yahoo
from confidence_backtest import LAGGARD, K, win, bootstrap

UNIVERSE = TECH + NEW + LAGGARD
LAM = 0.1


def observations(closes, eps_rows) -> list[dict]:
    idx = {d: i for i, (d, _) in enumerate(closes)}
    out = []
    for j, seg in enumerate(segments(closes, eps_rows)):
        a, eps = seg["anchor"], seg["eps"]
        m_ref = slope_ref(seg["full"], eps, a)
        sg = sigma_at(closes, [r for r, _ in eps_rows[j + 1:]], "std")
        if m_ref is None or not sg or len(eps) < 8:
            continue
        m_w, long = slope_log(seg["settled"], eps, a), eps_long_slope(eps)
        pd_sign = None if (m_w is None or long is None) else \
            ("+" if LAM * m_w + (1 - LAM) * long > 0 else "-")

        # 這一季的盈餘到底有沒有變差：滾動一年 EPS 的年增率
        now, ago = sum(eps[0:4]), sum(eps[4:8])
        eps_yoy = (now / ago - 1) * 100 if ago > 0 else None
        # 財報後 3 週相對前 1 週的價格反應
        pre = [c for d, c in seg["full"] if d < seg["rel"]]
        post = [c for d, c in seg["full"] if d >= seg["rel"]]
        react = (np.mean(post) / np.mean(pre) - 1) * 100 if pre and post else None

        for d, price in _window(closes, seg["rel"], seg["next"]):
            i = idx[d]
            if i + FWD >= len(closes):
                continue
            t = (_d(d) - _d(seg["rel"])).days
            out.append({
                "date": d, "fwd": (closes[i + FWD][1] / price - 1) * 100,
                "sign": "+" if m_ref > 0 else "-",
                "pd_sign": pd_sign,
                "zone": zone((price - (a + m_ref * t)) / sg, K),
                "eps_yoy": eps_yoy, "react": react,
            })
    return out


def run():
    eps = load_eps_yahoo(UNIVERSE)
    f = min(min(d for d, _ in v) for v in eps.values())
    l = max(max(d for d, _ in v) for v in eps.values())
    px = load_prices(UNIVERSE, _shift(f, -400), _shift(l, 200))
    raw = {t: observations(px[t], eps[t]) for t in UNIVERSE}
    allo = [o for t in UNIVERSE for o in raw[t]]
    print(f"\n觀測 {len(allo)} 天")

    # ── 問題 1：負斜率真的對應到「EPS 變差 + 反應差」嗎？
    print("\n【1】REF 斜率的正負，跟它宣稱代表的東西對不對得上")
    print(f"  {'斜率':<6}{'天數':>8}{'盈餘年增率中位':>16}{'盈餘衰退佔比':>14}"
          f"{'財報後價格反應':>16}{'反應為負佔比':>14}")
    for s in ("+", "-"):
        v = [o for o in allo if o["sign"] == s]
        y = np.array([o["eps_yoy"] for o in v if o["eps_yoy"] is not None])
        r = np.array([o["react"] for o in v if o["react"] is not None])
        print(f"  {s:<6}{len(v):>8}{np.median(y):>15.1f}%{100 * (y < 0).mean():>13.1f}%"
              f"{np.median(r):>15.2f}%{100 * (r < 0).mean():>13.1f}%")

    # ── 問題 2：控制住帶子位置後，符號還有沒有額外資訊？
    print("\n【2】同一個帶子位置下，正負斜率的差別（14 檔好公司）")
    good = [o for t in TECH + NEW for o in raw[t]]
    bw = win(np.array([o["fwd"] for o in good]))
    print(f"  基準勝率 {bw:.1f}%")
    print(f"  {'帶子位置':<12}{'斜率+勝率':>12}{'斜率-勝率':>12}{'差(−減+)':>12}"
          f"{'+天數':>9}{'-天數':>9}")
    for z, name in (("below", "超出下緣"), ("lower", "中間偏下"),
                    ("upper", "中間偏上"), ("above", "超出上緣")):
        a = np.array([o["fwd"] for o in good if o["zone"] == z and o["sign"] == "+"])
        b = np.array([o["fwd"] for o in good if o["zone"] == z and o["sign"] == "-"])
        if not len(a) or not len(b):
            continue
        print(f"  {name:<12}{win(a):>11.1f}%{win(b):>11.1f}%"
              f"{win(b) - win(a):>11.1f}{len(a):>9}{len(b):>9}")

    # ── 問題 3：PD 把這個維度殺掉了嗎？
    print("\n【3】PD（收縮到 5 年盈餘斜率）對符號做了什麼")
    v = [o for o in allo if o["pd_sign"]]
    for key, label in (("sign", "REF 斜率"), ("pd_sign", "PD 斜率")):
        pos = 100 * np.mean([o[key] == "+" for o in v])
        neg = [o for o in v if o[key] == "-"]
        y = np.array([o["eps_yoy"] for o in neg if o["eps_yoy"] is not None])
        print(f"  {label:<10} 正斜率佔比 {pos:>5.1f}%　"
              f"負斜率的日子裡盈餘真的衰退的比率 "
              f"{100 * (y < 0).mean() if len(y) else float('nan'):>5.1f}%")
    flip = 100 * np.mean([o["sign"] != o["pd_sign"] for o in v])
    print(f"  兩者符號不一致 {flip:.1f}% 的日子")

    chart(raw, allo, good)
    return raw


def chart(raw, allo, good):
    fig, ax = plt.subplots(2, 2, figsize=(15, 9))

    # ① 符號 vs EPS 年增率分布
    for s, c, lab in (("+", "#2ca02c", "slope +"), ("-", "#d62728", "slope -")):
        y = [o["eps_yoy"] for o in allo if o["sign"] == s and o["eps_yoy"] is not None]
        ax[0][0].hist(np.clip(y, -80, 120), bins=60, alpha=.55, color=c,
                      label=f"{lab} (median {np.median(y):+.1f}%)", density=True)
    ax[0][0].axvline(0, c="k", lw=1.5)
    ax[0][0].set_xlabel("TTM EPS year-over-year growth (%)")
    ax[0][0].legend(fontsize=9)
    ax[0][0].set_title("Does a negative slope mean earnings got worse?\n"
                       "if the design works, red sits left of green")

    # ② 符號 vs 財報後價格反應
    for s, c, lab in (("+", "#2ca02c", "slope +"), ("-", "#d62728", "slope -")):
        r = [o["react"] for o in allo if o["sign"] == s and o["react"] is not None]
        ax[0][1].hist(np.clip(r, -25, 25), bins=60, alpha=.55, color=c,
                      label=f"{lab} (median {np.median(r):+.2f}%)", density=True)
    ax[0][1].axvline(0, c="k", lw=1.5)
    ax[0][1].set_xlabel("post-earnings 3w mean vs pre-earnings 1w mean (%)")
    ax[0][1].legend(fontsize=9)
    ax[0][1].set_title("Does a negative slope mean the market reacted badly?")

    # ③ 控制帶子位置後的符號效果
    zs = [("below", "below\nlower"), ("lower", "lower\nhalf"),
          ("upper", "upper\nhalf"), ("above", "above\nupper")]
    bw = win(np.array([o["fwd"] for o in good]))
    x = np.arange(4)
    for j, (s, c) in enumerate((("+", "#2ca02c"), ("-", "#d62728"))):
        v = [win(np.array([o["fwd"] for o in good
                           if o["zone"] == z and o["sign"] == s])) for z, _ in zs]
        b = ax[1][0].bar(x + j * .4, v, .4, color=c, label=f"slope {s}")
        ax[1][0].bar_label(b, fmt="%.1f%%", fontsize=8)
    ax[1][0].axhline(bw, ls="--", c="k", lw=1, label=f"baseline {bw:.1f}%")
    ax[1][0].set_xticks(x + .2); ax[1][0].set_xticklabels([l for _, l in zs])
    ax[1][0].set_ylim(50, 80); ax[1][0].legend(fontsize=8)
    ax[1][0].set_title("Sign still matters after controlling for band position?\n"
                       "14 good companies")

    # ④ PD 把符號壓成什麼樣
    v = [o for o in allo if o["pd_sign"]]
    names = ["REF", "PD"]
    pos = [100 * np.mean([o[k] == "+" for o in v]) for k in ("sign", "pd_sign")]
    b = ax[1][1].bar(names, pos, color=["#1f77b4", "#9467bd"], width=.5)
    ax[1][1].bar_label(b, fmt="%.1f%%")
    ax[1][1].axhline(50, ls="--", c="k", lw=1)
    ax[1][1].set_ylim(0, 100); ax[1][1].set_ylabel("% of days with positive slope")
    ax[1][1].set_title("PD forces the slope positive\n"
                       "the 'this quarter got worse' signal is erased")

    fig.tight_layout()
    fig.savefig("shots/sign-stage7.png", dpi=110)
    print("\n圖：shots/sign-stage7.png")


if __name__ == "__main__":
    run()
