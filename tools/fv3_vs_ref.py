"""第五階段：Fv3 的算法 vs REF 的算法，同一組觀測、同一張八格表。

前兩階段測的是 REF（sqrt 價格、x 用第幾筆、4 週窗口）。
Fv3 是另一套（原始價格、真實日曆天、整季窗口），從來沒測過。

關鍵：Fv3 的斜率是「用它自己要畫的那一段去配」。要公平比，必須重現
使用者當天真正看到的那條線 —— 每一天只用到那一天為止的資料重配一次。
"""

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from slope_backtest import (
    AFTER_DAYS, BEFORE_DAYS, _d, _shift, _window, load_prices, segments, slope_ref,
)
from band_backtest import sigma_at, zone
from zone_backtest import ACTION, FWD, TECH, NEW, load_eps_yahoo
from confidence_backtest import LAGGARD, BUY, K, thin, split, win, bootstrap

UNIVERSE = TECH + NEW + LAGGARD


def fv3_slope(closes, idx, rel: str, end: str, today: str, anchor: float,
              eps: list[float]) -> float | None:
    """重現 app/trend.py 的 _segment()：整段回歸 + 換算成元／天的盈餘漂移。

    _fit_rows 的借天數邏輯照抄 —— 季初資料不夠時往前借，借到蓋住半段為止。
    只吃 today 以前的資料，不偷看未來。
    """
    rows = [(d, c) for d, c in closes if rel <= d <= min(end, today)]
    if len(rows) < 2:
        return None
    short = (_d(end) - _d(rel)).days // 2 - (_d(rows[-1][0]) - _d(rel)).days
    if short > 0:
        lo = _shift(rel, -short)
        rows = [(d, c) for d, c in closes if lo <= d <= min(end, today)]
    if len({d for d, _ in rows}) < 2:
        return None

    x = np.array([(_d(d) - _d(rows[0][0])).days for d, _ in rows], dtype=float)
    y = np.array([c for _, c in rows])
    var = float(((x - x.mean()) ** 2).sum())
    if not var:
        return None
    m_price = float(((x - x.mean()) * (y - y.mean())).sum() / var)

    if len(eps) < 5 or eps[4] == 0:
        return None
    drift = anchor * (eps[0] / eps[4] - 1) / 365.25
    return (m_price + 0.1 * drift) / 1.1


def observations(closes, eps_rows) -> list[dict]:
    """同一組交易日，兩種中心線各給一個 z。"""
    idx = {d: i for i, (d, _) in enumerate(closes)}
    out = []
    for j, seg in enumerate(segments(closes, eps_rows)):
        rel, end, a = seg["rel"], seg["next"], seg["anchor"]
        m_ref = slope_ref(seg["full"], seg["eps"], a)
        sg = sigma_at(closes, [r for r, _ in eps_rows[j + 1:]], "std")
        if m_ref is None or not sg:
            continue
        for d, price in _window(closes, rel, end):
            i = idx[d]
            if i + FWD >= len(closes):
                continue
            m_fv3 = fv3_slope(closes, idx, rel, end, d, a, seg["eps"])
            if m_fv3 is None:
                continue
            t = (_d(d) - _d(rel)).days
            out.append({
                "date": d, "fwd": (closes[i + FWD][1] / price - 1) * 100,
                "ref": {"sign": "+" if m_ref > 0 else "-",
                        "z": (price - (a + m_ref * t)) / sg},
                "fv3": {"sign": "+" if m_fv3 > 0 else "-",
                        "z": (price - (a + m_fv3 * t)) / sg},
            })
    return out


def flat(obs, which):
    return [{"date": o["date"], "fwd": o["fwd"], **o[which]} for o in obs]


def table(title, obs):
    base = np.array([o["fwd"] for o in obs])
    bw = win(base)
    print(f"\n{title}（k={K}、往後 {FWD} 交易日、樣本 {len(obs)}）")
    print(f"  全樣本基準：中位 {np.median(base):+.2f}%、上漲比率 {bw:.1f}%")
    print(f"  {'斜率':<5}{'帶子位置':<12}{'你的判斷':<10}"
          f"{'中位報酬':>10}{'上漲比率':>10}{'超額':>8}{'佔比':>8}")
    g = {}
    for o in obs:
        g.setdefault((o["sign"], zone(o["z"], K)), []).append(o["fwd"])
    for sign in ("+", "-"):
        for z, name in (("below", "超出下緣"), ("lower", "中間偏下"),
                        ("upper", "中間偏上"), ("above", "超出上緣")):
            v = np.array(g.get((sign, z), []))
            if not len(v):
                print(f"  {sign:<5}{name:<12}{ACTION[(sign, z)]:<10}{'（無樣本）':>10}")
                continue
            print(f"  {sign:<5}{name:<12}{ACTION[(sign, z)]:<10}"
                  f"{np.median(v):>9.2f}%{win(v):>9.1f}%{win(v) - bw:>7.1f}"
                  f"{100 * len(v) / len(base):>7.1f}%")


def run():
    eps = load_eps_yahoo(UNIVERSE)
    f = min(min(d for d, _ in v) for v in eps.values())
    l = max(max(d for d, _ in v) for v in eps.values())
    px = load_prices(UNIVERSE, _shift(f, -400), _shift(l, 200))

    raw = {t: observations(px[t], eps[t]) for t in UNIVERSE}
    print(f"\n共同觀測 {sum(len(v) for v in raw.values())} 天"
          f"（兩種算法用完全同一批日子）")

    ind = {t: thin(v) for t, v in raw.items()}
    print(f"獨立樣本 {sum(len(v) for v in ind.values())} 筆")

    for which, label in (("ref", "REF 算法（sqrt + 4 週窗口）"),
                         ("fv3", "Fv3 算法（原始價 + 整季窗口）")):
        for gname, ts in (("14 檔好公司", TECH + NEW), ("全部 30 檔", UNIVERSE)):
            table(f"{label} · {gname}", flat([o for t in ts for o in raw[t]], which))

    print("\n" + "=" * 74)
    print("正面對決（獨立樣本、買進側 vs 迴避側的上漲比率差）")
    print(f"  {'分組':<16}{'REF':>22}{'Fv3':>22}")
    res = {}
    for gname, ts in (("14 檔好公司", TECH + NEW), ("16 檔落後股", LAGGARD),
                      ("全部 30 檔", UNIVERSE)):
        cell = []
        for which in ("ref", "fv3"):
            per = {t: flat(ind[t], which) for t in ts}
            ci, _ = bootstrap(per)
            cell.append(ci)
            res[(gname, which)] = ci
        print(f"  {gname:<16}"
              f"{cell[0][1]:>+9.1f}pp [{cell[0][0]:+.1f},{cell[0][2]:+.1f}]"
              f"{cell[1][1]:>+9.1f}pp [{cell[1][0]:+.1f},{cell[1][2]:+.1f}]")

    print("\n  兩種算法的斜率正負有多常不一樣：")
    allo = [o for t in UNIVERSE for o in raw[t]]
    dis = sum(o["ref"]["sign"] != o["fv3"]["sign"] for o in allo)
    print(f"    {100 * dis / len(allo):.1f}% 的交易日方向相反")
    zr = np.array([o["ref"]["z"] for o in allo])
    zf = np.array([o["fv3"]["z"] for o in allo])
    print(f"    z 分數相關係數 {np.corrcoef(zr, zf)[0, 1]:.3f}")
    print(f"    |z| 中位數：REF {np.median(abs(zr)):.2f}、Fv3 {np.median(abs(zf)):.2f}"
          f"  ← 越小代表線越貼著股價跑、越難觸發訊號")
    for which, z in (("REF", zr), ("Fv3", zf)):
        print(f"    {which} 觸發下緣的日子佔比 {100 * float((z < -K).mean()):.1f}%")

    chart(raw, ind, res)
    return raw, ind, res


def chart(raw, ind, res):
    fig, ax = plt.subplots(2, 2, figsize=(15, 9))
    groups = ["14 檔好公司", "16 檔落後股", "全部 30 檔"]
    en = ["14 good cos", "16 laggards", "all 30"]

    # ① 正面對決 + 信賴區間
    x = np.arange(3)
    for j, (which, c) in enumerate((("ref", "#1f77b4"), ("fv3", "#ff7f0e"))):
        med = [res[(g, which)][1] for g in groups]
        lo = [res[(g, which)][1] - res[(g, which)][0] for g in groups]
        hi = [res[(g, which)][2] - res[(g, which)][1] for g in groups]
        ax[0][0].bar(x + j * .4, med, .4, yerr=[lo, hi], capsize=4,
                     color=c, label=which.upper())
    ax[0][0].axhline(0, c="k", lw=1)
    ax[0][0].set_xticks(x + .2); ax[0][0].set_xticklabels(en)
    ax[0][0].set_ylabel("buy minus avoid win rate (pp)"); ax[0][0].legend()
    ax[0][0].set_title("Head to head: REF vs Fv3 slope\nindependent samples, 95% CI by ticker bootstrap")

    # ② 八格上漲比率（14 檔好公司）
    good = [o for t in TECH + NEW for o in raw[t]]
    zs = ["below", "lower", "upper", "above"]
    lab = ["below\nlower", "lower\nhalf", "upper\nhalf", "above\nupper"]
    for j, (which, c) in enumerate((("ref", "#1f77b4"), ("fv3", "#ff7f0e"))):
        fl = flat(good, which)
        g = {}
        for o in fl:
            g.setdefault(zone(o["z"], K), []).append(o["fwd"])
        v = [win(np.array(g[z])) if z in g else np.nan for z in zs]
        b = ax[0][1].bar(np.arange(4) + j * .4, v, .4, color=c, label=which.upper())
        ax[0][1].bar_label(b, fmt="%.1f%%", fontsize=8)
    ax[0][1].axhline(win(np.array([o["fwd"] for o in good])), ls="--", c="k", lw=1)
    ax[0][1].set_xticks(np.arange(4) + .2); ax[0][1].set_xticklabels(lab)
    ax[0][1].set_ylim(0, 85); ax[0][1].legend()
    ax[0][1].set_title("4 zones, 14 good companies\n% of days with positive 60d return")

    # ③ z 分布：Fv3 的線貼著股價跑，偏離會被吃掉
    allo = [o for t in UNIVERSE for o in raw[t]]
    bins = np.linspace(-4, 4, 80)
    ax[1][0].hist([o["ref"]["z"] for o in allo], bins, alpha=.6,
                  color="#1f77b4", label="REF", density=True)
    ax[1][0].hist([o["fv3"]["z"] for o in allo], bins, alpha=.6,
                  color="#ff7f0e", label="Fv3", density=True)
    for s in (-K, K):
        ax[1][0].axvline(s, ls="--", c="k", lw=1)
    ax[1][0].legend(); ax[1][0].set_xlabel("z = (price - centre) / sigma")
    ax[1][0].set_title("How far price sits from the centre line\n"
                       "narrower = line chases price = fewer signals")

    # ④ 逐檔對決
    ts = list(UNIVERSE)
    d = {w: [] for w in ("ref", "fv3")}
    for t in ts:
        for w in ("ref", "fv3"):
            b, a = split(flat(ind[t], w))
            d[w].append(win(b) - win(a) if len(b) > 8 and len(a) > 8 else np.nan)
    xx = np.arange(len(ts))
    ax[1][1].bar(xx - .2, d["ref"], .4, color="#1f77b4", label="REF")
    ax[1][1].bar(xx + .2, d["fv3"], .4, color="#ff7f0e", label="Fv3")
    ax[1][1].axhline(0, c="k", lw=1)
    ax[1][1].set_xticks(xx); ax[1][1].set_xticklabels(ts, rotation=90, fontsize=7)
    ax[1][1].set_ylabel("buy minus avoid (pp)"); ax[1][1].legend()
    ax[1][1].set_title("Per ticker")

    fig.tight_layout()
    fig.savefig("shots/fv3-vs-ref-stage5.png", dpi=110)
    print("\n圖：shots/fv3-vs-ref-stage5.png")


if __name__ == "__main__":
    run()
