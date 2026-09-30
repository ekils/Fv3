"""斜率回測：每一段用財報後的反應推下一次財報日的股價，看誰推得準。

這支腳本不碰 price_trend_local 一行程式碼，只讀它的 epsdata.pkl ——
EPS 用同一份，兩邊的差異才會純粹來自算法，不是來自資料。

第一階段只比中心線（斜率）。帶寬是第二階段的事，在 band_backtest.py。
"""

import pickle
import sys
from datetime import date, timedelta

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import yfinance as yf

EPS_PKL = ("/home/KGI_AI/cdib3935/DannyTest/Investment/price_trend_local"
           "/pages/data_files/epsdata.pkl")
TICKERS = ("AAPL", "COST", "DECK", "GD", "GOOGL", "LLY",
           "MA", "MCD", "META", "MSFT", "NVDA", "RL")

BEFORE_DAYS, AFTER_DAYS = 7, 21
SETTLE_DAYS = 5          # 情緒期：公布後這幾天不算進斜率
EPS_WEIGHT = 0.1         # 兩邊都用 0.1，這個不動
LONG_QUARTERS = 20       # 5 年
LAMBDAS = tuple(round(x, 2) for x in np.arange(0, 1.01, 0.1))


def _d(s: str) -> date:
    return date.fromisoformat(s)


def _shift(day: str, n: int) -> str:
    return (_d(day) + timedelta(days=n)).isoformat()


def load_eps() -> dict[str, list[tuple[str, float]]]:
    """每檔股票的 (財報公布日, 當季 EPS)，由新到舊 —— 跟 ref 的排法一致。"""
    df = pickle.load(open(EPS_PKL, "rb")).dropna(subset=["EPS"])
    out = {}
    for t in TICKERS:
        rows = df[df.Ticker == t].sort_values("Date", ascending=False)
        out[t] = [(d.date().isoformat(), float(e))
                  for d, e in zip(rows.Date, rows.EPS)]
    return out


def load_prices(tickers, start: str, end: str) -> dict[str, list[tuple[str, float]]]:
    raw = yf.download(list(tickers), start=start, end=end, progress=False,
                      auto_adjust=True)["Close"]
    return {t: [(i.date().isoformat(), float(v))
                for i, v in raw[t].items() if v == v]
            for t in tickers}


def _window(closes, lo: str, hi: str) -> list[tuple[str, float]]:
    return [(d, c) for d, c in closes if lo <= d <= hi]


def _close_at(closes, day: str) -> float | None:
    prior = [c for d, c in closes if d <= day]
    return prior[-1] if prior else None


def _ols(x, y) -> float:
    mx, my = np.mean(x), np.mean(y)
    var = float(np.sum((np.array(x) - mx) ** 2))
    return float(np.sum((np.array(x) - mx) * (np.array(y) - my)) / var) if var else 0.0


# ── 四種斜率 ────────────────────────────────────────────────────

def slope_ref(win, eps_newest_first, anchor) -> float:
    """ref 現況：對 sqrt(股價) 回歸、x 用「第幾筆」、EPS 用 sqrt 比率。"""
    prices = [c for _, c in win]
    grp = eps_newest_first[:6]
    if len(grp) < 6:
        return None
    arr = np.array(grp, dtype=float)
    sq = np.sqrt(arr) if (arr >= 0).all() else np.cbrt(arr)
    m1 = float(np.polyfit(np.arange(len(prices)), np.sqrt(prices), 1)[0])
    m2 = float((sq[0] - sq[3]) / sq[3]) if sq[3] else 0.0
    return (m1 + EPS_WEIGHT * m2) / (1 + EPS_WEIGHT)


def _log_price_slope(win) -> float:
    x = [(_d(d) - _d(win[0][0])).days for d, _ in win]
    return _ols(x, np.log([c for _, c in win]))


def _ttm_slope(eps) -> float | None:
    """滾動一年盈餘的年增率，換算成「每天幾 %」。"""
    if len(eps) < 8:
        return None
    now, ago = sum(eps[0:4]), sum(eps[4:8])
    return float(np.log(now / ago) / 365.25) if now > 0 and ago > 0 else None


def slope_log(win, eps, anchor) -> float | None:
    m1 = _log_price_slope(win)
    m2 = _ttm_slope(eps)
    return None if m2 is None else (m1 + EPS_WEIGHT * m2) / (1 + EPS_WEIGHT)


def eps_long_slope(eps) -> float | None:
    """5 年 20 季的 log(滾動一年盈餘) 回歸斜率，換算成「每天幾 %」。"""
    need = LONG_QUARTERS + 4
    if len(eps) < need:
        return None
    ttm = [sum(eps[k:k + 4]) for k in range(LONG_QUARTERS)]
    if min(ttm) <= 0:
        return None
    # eps 是由新到舊，翻成由舊到新才是時間軸
    return _ols(range(LONG_QUARTERS), np.log(ttm[::-1])) / 91.3


# ── 一段一個預測 ────────────────────────────────────────────────

def segments(closes, eps_rows):
    """(這次公布日, 下次公布日, 到期日的實際股價, 錨點, 各種斜率要的材料)。"""
    out = []
    for i in range(1, len(eps_rows)):
        rel, nxt = eps_rows[i][0], eps_rows[i - 1][0]
        full = _window(closes, _shift(rel, -BEFORE_DAYS), _shift(rel, AFTER_DAYS))
        settled = _window(closes, _shift(rel, SETTLE_DAYS), _shift(rel, AFTER_DAYS))
        actual = _close_at(closes, nxt)
        if len(full) < 8 or len(settled) < 5 or actual is None:
            continue
        horizon = (_d(nxt) - _d(rel)).days
        if not 40 <= horizon <= 160:
            continue
        out.append({
            "rel": rel, "next": nxt, "days": horizon, "actual": actual,
            "anchor": float(np.median([c for _, c in full])),
            "full": full, "settled": settled,
            "eps": [e for _, e in eps_rows[i:]],
        })
    return out


def predict(seg, variant: str, lam: float = 0.5) -> float | None:
    a, n = seg["anchor"], seg["days"]
    if variant == "Z":                      # 對照組：水平線
        return a
    if variant == "A":                      # ref 現況
        m = slope_ref(seg["full"], seg["eps"], a)
        return None if m is None else a + m * n
    if variant == "B":                      # log + 日曆天
        m = slope_log(seg["full"], seg["eps"], a)
        return None if m is None else a * np.exp(m * n)
    if variant == "C":                      # B + 跳過情緒期
        m = slope_log(seg["settled"], seg["eps"], a)
        return None if m is None else a * np.exp(m * n)
    if variant == "D":                      # C + 收縮到 5 年 EPS 斜率
        m, long = slope_log(seg["settled"], seg["eps"], a), eps_long_slope(seg["eps"])
        if m is None or long is None:
            return None
        return a * np.exp((lam * m + (1 - lam) * long) * n)
    raise ValueError(variant)


def score(rows: list[dict]) -> dict:
    err = [abs(r["pred"] / r["actual"] - 1) * 100 for r in rows]
    hit = [(r["pred"] - r["anchor"]) * (r["actual"] - r["anchor"]) > 0 for r in rows]
    return {"n": len(rows), "mae": float(np.median(err)),
            "p90": float(np.percentile(err, 90)),
            "hit": 100 * sum(hit) / len(hit)}


def run():
    eps_all = load_eps()
    first = min(min(d for d, _ in v) for v in eps_all.values())
    last = max(max(d for d, _ in v) for v in eps_all.values())
    print(f"EPS 涵蓋 {first} ~ {last}")
    px = load_prices(TICKERS, _shift(first, -400), _shift(last, 200))

    segs = {t: segments(px[t], eps_all[t]) for t in TICKERS}
    print("每檔可用季段：", {t: len(s) for t, s in segs.items()})

    # 共同樣本：只留「每一個變體都算得出來」的季段。
    # D 要 24 季 EPS 才算得動，樣本本來就比別人少一半 ——
    # 各自用各自的樣本去比，比到的是「誰挑到的題目比較簡單」，不是誰比較準。
    pool = []
    for t in TICKERS:
        for s in segs[t]:
            p = {v: predict(s, v) for v in ("Z", "A", "B", "C")}
            p |= {f"D{lam}": predict(s, "D", lam) for lam in LAMBDAS}
            if all(v and v > 0 for v in p.values()):
                pool.append({**s, "ticker": t, "p": p})
    print(f"共同樣本 {len(pool)} 段（全部 {sum(len(v) for v in segs.values())} 段）")

    pick = lambda key: (score([{**r, "pred": r["p"][key]} for r in pool]),
                        [{**r, "pred": r["p"][key]} for r in pool])
    results = {v: pick(v) for v in ("Z", "A", "B", "C")}
    sweep = {lam: pick(f"D{lam}") for lam in LAMBDAS}
    best = min(sweep, key=lambda k: sweep[k][0]["mae"])
    results[f"D(λ={best})"] = sweep[best]

    print(f"\n{'變體':<10}{'樣本':>6}{'誤差中位數':>12}{'第90百分位':>12}{'方向命中率':>12}")
    for k, (s, _) in results.items():
        print(f"{k:<10}{s['n']:>6}{s['mae']:>11.2f}%{s['p90']:>11.2f}%{s['hit']:>11.1f}%")

    print("\nλ 掃描（D，全樣本）：", "  ".join(
        f"{lam}:{sweep[lam][0]['mae']:.2f}%" for lam in sweep))

    # λ 用全樣本挑再回頭誇自己準，是作弊。按時間切一半：前半挑 λ，後半只驗證。
    cut = sorted(r["rel"] for r in pool)[len(pool) // 2]
    train = [r for r in pool if r["rel"] < cut]
    test = [r for r in pool if r["rel"] >= cut]
    tr = lambda rows, key: score([{**r, "pred": r["p"][key]} for r in rows])
    lam_tr = min(LAMBDAS, key=lambda l: tr(train, f"D{l}")["mae"])
    print(f"\n時間切分 {cut}：訓練 {len(train)} 段、驗證 {len(test)} 段")
    print(f"  前半挑出的 λ = {lam_tr}")
    for k in ("Z", "A", f"D{lam_tr}"):
        s = tr(test, k)
        print(f"  後半 {k:<6} 誤差中位數 {s['mae']:.2f}%　方向命中率 {s['hit']:.1f}%")
    chart(results, sweep, segs)
    return results, sweep, segs


def chart(results, sweep, segs):
    fig, ax = plt.subplots(2, 2, figsize=(14, 9))
    names = list(results)
    mae = [results[k][0]["mae"] for k in names]
    hit = [results[k][0]["hit"] for k in names]
    col = ["#888", "#d62728", "#ff7f0e", "#2ca02c", "#1f77b4"]

    b = ax[0][0].bar(names, mae, color=col)
    ax[0][0].bar_label(b, fmt="%.2f%%")
    ax[0][0].set_title("Stage 1: median absolute error vs next earnings date\n(lower is better)")
    ax[0][0].set_ylabel("median |pred/actual - 1|  (%)")

    b = ax[0][1].bar(names, hit, color=col)
    ax[0][1].bar_label(b, fmt="%.1f%%")
    ax[0][1].axhline(50, ls="--", c="k", lw=1)
    ax[0][1].set_title("Direction hit rate (up/down vs anchor)\n50% = coin flip")
    ax[0][1].set_ylim(0, 100)

    lams, lmae = list(sweep), [sweep[k][0]["mae"] for k in sweep]
    ax[1][0].plot(lams, lmae, "o-", c="#1f77b4")
    ax[1][0].axhline(results["C"][0]["mae"], ls="--", c="#2ca02c", label="C (no shrinkage)")
    ax[1][0].set_title("Variant D: shrinkage sweep\nlambda=0 pure 5y-EPS drift, 1 = pure 3-week window")
    ax[1][0].set_xlabel("lambda"); ax[1][0].set_ylabel("median error (%)"); ax[1][0].legend()

    # 每一檔都要留一格。某個變體在某檔沒樣本就留 nan，不然長度對不齊，
    # 而且缺的那一檔會被默默往左擠、對到別人的標籤上
    per = {k: [score(sub)["mae"] if (sub := [r for r in results[k][1] if r["ticker"] == t])
               else np.nan for t in TICKERS] for k in names}
    x = np.arange(len(TICKERS)); w = 0.8 / len(names)
    for j, k in enumerate(names):
        ax[1][1].bar(x + j * w, per[k], w, label=k, color=col[j])
    ax[1][1].set_xticks(x + 0.4); ax[1][1].set_xticklabels(TICKERS, rotation=45)
    ax[1][1].set_title("Median error by ticker"); ax[1][1].legend(fontsize=8)

    fig.tight_layout()
    fig.savefig("shots/slope-stage1.png", dpi=110)
    print("\n圖：shots/slope-stage1.png")


if __name__ == "__main__":
    run()
