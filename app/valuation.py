"""「現在的價格，跟這家公司自己的過去比」。

本益比 = 股價 ÷ 每股盈餘。這兩個數字都會動，所以股價漲了不代表變貴。
這裡做的唯一一件事是把漲幅拆開：多少來自公司真的多賺，多少來自市場願意多付。
沒有預測，全是除法。
"""

from datetime import date, timedelta

from statistics import median

from .config import (
    PE_TRAP_EARNINGS_GROWTH,
    PE_TRAP_LEAD,
    PRICE_VERDICT_BANDS,
    PRICE_VERDICT_TAIL,
    PRICE_VERDICT_TRAP,
    QUALITY_CHECKS,
    SUPPORT_CONCLUSIONS,
    SUPPORT_PROPPED_NOTE,
    SLICE_FLAT_EPS,
    SLICE_FLAT_SHARES,
    VALUATION_SPARK_POINTS,
    VALUATION_TABLE_ROWS,
)
from .series import downsample


def _released_after(releases: list[str], period: str) -> str | None:
    later = [d for d in releases if d > period]
    return later[0] if later else None


def known_eps(quarters: list[dict], releases: list[str]) -> list[tuple[str, float]]:
    """(市場知道這個數字的日期, 滾動一年每股盈餘)，由舊到新。

    連續四季相加＝滾動一年盈餘，所以每一季都會更新一次，本益比才是連續曲線
    而不是一年跳一次的台階。日期用真正的財報公布日 —— 季末那天沒人知道數字。
    """
    out = [(_released_after(releases, quarters[i]["period"]),
            sum(q["eps"] for q in quarters[i - 3:i + 1]))
           for i in range(3, len(quarters))]
    return sorted((d, v) for d, v in out if d and v > 0)


def _eps_at(known: list[tuple[str, float]], day: str) -> float | None:
    prior = [v for d, v in known if d <= day]
    return prior[-1] if prior else None


def _pct(now: float, before: float) -> float:
    return round((now / before - 1) * 100, 1)


def _delta(unit: str, now: float | None, before: float | None) -> float | None:
    """跟前一年比的變化。單位由 VALUATION_TABLE_ROWS 決定，這裡不自己猜。

    「率」一定要用相減。淨利率 7.3% → 4.0% 拿去相除會印成 -45%，讀起來像公司少賺四成半，
    但它只掉了 3.3 個百分點 —— 那是兩個百分比相除，同一個 % 符號被當成兩種意思用。
    """
    if now is None or before is None:
        return None
    if unit == "pp":
        return round(now - before, 1)
    # 去年是負的時候，除法的符號會跟直覺相反（虧 100 變賺 10 算出來是 -110%）——
    # 那種年度寧可留白，也不要給一個看起來像答案的垃圾數字
    return _pct(now, before) if before > 0 else None


def _margins(rev: dict, gross: dict, net: dict, shares: dict) -> list[dict]:
    """一個財年一列，新的在前。每一列都帶齊六個指標和它們跟前一年的變化。

    每一列的 key 都寫滿（沒資料就是 None），所以畫面不用分「這個欄位存不存在」和
    「這個欄位是不是空的」兩種情況 —— 只有一種情況：是不是 None。
    """
    rows = []
    for d in sorted(rev, reverse=True):
        if not rev[d]:
            continue
        n, s = net.get(d), shares.get(d)
        rows.append({
            "year": d[:4],
            "revenue": rev[d],
            "net": n,
            "gross_margin": round(gross[d] / rev[d] * 100, 1) if d in gross else None,
            "net_margin": round(n / rev[d] * 100, 1) if n is not None else None,
            "shares": s,
            "eps": round(n / s, 2) if n is not None and s else None,
        })
    for i, r in enumerate(rows):
        prev = rows[i + 1] if i + 1 < len(rows) else {}
        r["d"] = {key: _delta(unit, r[key], prev.get(key))
                  for key, _, _, unit in VALUATION_TABLE_ROWS}
    return rows


def slice_eps(margins: list[dict]) -> dict | None:
    """每股盈餘的變化，拆成「餅變多大」跟「切成幾份」。

    這兩個是不同的事，但畫面上只看得到相乘之後的結果。盈餘掉 47%、每股盈餘掉 52%，
    中間差的那 5 個百分點不是誤差，是被多發的股票吃掉的 —— 不拆開就看不到它。
    """
    if len(margins) < 2:
        return None
    now, prev = margins[0], margins[1]
    net, sh, eps = (now["d"][k] for k in ("net", "shares", "eps"))
    if net is None or sh is None or eps is None:
        return None
    verdict = ("flat" if abs(sh) < SLICE_FLAT_SHARES
               else "dilute" if sh > 0 else "buyback")
    # 好還是不好，看的是「餅」跟「你那一份」往同一個方向走還是分岔。
    # 分岔的兩種都不是好消息：一種是回購在撐，一種是稀釋在吃。
    if abs(eps) < SLICE_FLAT_EPS:
        conclusion = "flat"
    elif eps > 0:
        conclusion = "both_up" if net > 0 else "propped"
    else:
        conclusion = "both_down" if net < 0 else "diluted"
    return {
        "conclusion": conclusion,
        "from_year": prev["year"], "to_year": now["year"],
        "net": {"from": prev["net"], "to": now["net"], "pct": net},
        "shares": {"from": prev["shares"], "to": now["shares"], "pct": sh},
        "eps": {"from": prev["eps"], "to": now["eps"], "pct": eps},
        # 股數害每股盈餘多掉（或多漲）了幾個百分點。正負號就是它幫忙還是扯後腿
        "gap": round(eps - net, 1),
        "verdict": verdict,
    }


def support_verdict(split: dict, quality: list[dict], sliced: dict | None) -> dict:
    """第二區的整區結論。兩個軸交叉：這波誰撐的（漲幅拆解）× 現在還撐不撐得住（四題體檢）。

    這是三個小節裡唯一一個跨小節的判斷。不跨的話，「過去靠本業、現在已經在縮」
    會被那兩句各自的小結講成好消息 —— 兩句都沒說錯，但合起來的意思沒人講。
    """
    earned = split["earnings"] > split["multiple"]
    # ok is False 才是紅燈。None 是「資料不足」或「沒動」，那不是壞消息，不准算進來
    bad = sum(1 for r in quality if r["ok"] is False)
    key = f"{'earned' if earned else 'mood'}_{'solid' if bad == 0 else 'slipping'}"
    face, text = SUPPORT_CONCLUSIONS[key]
    note = SUPPORT_PROPPED_NOTE if sliced and sliced["conclusion"] == "propped" else ""
    return {"key": key, "face": face, "bad": bad, "by": "本業" if earned else "情緒",
            "earnings": split["earnings"], "multiple": split["multiple"],
            "text": text.format(bad=bad), "note": note}


def _spark_point(s: dict) -> dict:
    px, eps = round(s["pe"] * s["eps"], 2), round(s["eps"], 3)
    return {"d": s["date"], "px": px, "eps": eps, "pe": round(px / eps, 1)}


def _pe_trap(earnings: float, price: float, years: int) -> dict | None:
    """盈餘暴衝造成的低本益比，跟股價下跌造成的低本益比，在百分位上長得一模一樣。

    分不出來的話，賺錢賺到高峰的公司會被讀成「便宜到不買是白痴」—— 而那通常是最危險的時候。
    """
    grew, rose = 1 + earnings / 100, 1 + price / 100
    if earnings < PE_TRAP_EARNINGS_GROWTH or grew < rose * PE_TRAP_LEAD:
        return None
    return {"years": years, "earnings_x": round(grew, 1), "price_x": round(rose, 1)}


def price_verdict(pct: int, years: int, trap: dict | None) -> dict:
    """「現在貴不貴」的一句話結論。看的只有一個數字：本益比落在自己這幾年的第幾 %。

    有盈餘暴衝陷阱時直接換一句 —— 那種情況百分位量到的是盈餘的成長速度，不是貴或便宜，
    照著印「偏便宜」比不印還糟。
    """
    face, label, text = next((f, l, t) for lo, f, l, t in PRICE_VERDICT_BANDS if pct >= lo)
    if trap:
        face, label, text = PRICE_VERDICT_TRAP
    return {"face": face, "label": label, "pct": pct, "years": years,
            "text": text, "tail": PRICE_VERDICT_TAIL}


def _ttm_yoy(known: list[tuple[str, float]], back: int) -> float | None:
    """滾動一年盈餘的年增率。back=0 是最新一季，back=1 是上一季。

    一季一筆，往回數四筆就是一年前的同一個位置。
    """
    i = len(known) - 1 - back
    return _pct(known[i][1], known[i - 4][1]) if i - 4 >= 0 else None


def _check(key: str, value: float | None, basis: str) -> dict:
    """一項檢查的結論。沒資料就說沒資料，不猜；沒動就不給方向，不硬湊。

    basis 一定要講：不寫出「拿哪兩段比」，看的人沒辦法判斷這個數字答的是什麼問題。
    """
    ask, up, down, still, unit, flat = QUALITY_CHECKS[key]
    if value is None:
        return {"key": key, "ask": ask, "value": None, "unit": unit, "basis": basis,
                "ok": None, "text": "資料不足，這一題答不了"}
    moved = abs(value) >= flat
    return {"key": key, "ask": ask, "value": value, "unit": unit, "basis": basis,
            "ok": value > 0 if moved else None,
            "text": (up if value > 0 else down) if moved else still}


def _quarter(known: list[tuple[str, float]], back: int) -> str:
    i = len(known) - 1 - back
    return known[i][0] if 0 <= i < len(known) else "？"


def _fy(margins: list[dict], i: int) -> str:
    return margins[i]["year"] if len(margins) > i else "？"


def earnings_quality(known: list[tuple[str, float]], margins: list[dict]) -> list[dict]:
    """本益比的分母撐不撐得住。四題，全是除法，沒有任何預測。

    一律**跟一年前的同一個位置比**（同期比），不是跟上一季比 —— 大多數公司有淡旺季，
    拿 Q4 比 Q3 量到的是季節，不是體質。而且永遠看最新的一段，
    跟畫面上「回看幾年」無關：那根拉桿管的是本益比區間有多寬，不是公司現在的體質。
    """
    now, prev = _ttm_yoy(known, 0), _ttm_yoy(known, 1)
    return [
        _check("growth", now,
               f"{_quarter(known, 0)} 公布的近一年　vs　{_quarter(known, 4)} 公布的近一年"),
        _check("accel", None if now is None or prev is None else round(now - prev, 1),
               f"最新這季的年增率　vs　上一季（{_quarter(known, 1)} 公布）的年增率"),
        _check("revenue", margins[0]["d"]["revenue"] if margins else None,
               f"{_fy(margins, 0)} 財年　vs　{_fy(margins, 1)} 財年"),
        _check("margin", margins[0]["d"]["net_margin"] if margins else None,
               f"{_fy(margins, 0)} 財年　vs　{_fy(margins, 1)} 財年"),
    ]


def _fair(pes: list[float], eps_now: float, price_now: float) -> dict:
    """盈餘不動的話，本益比回到區間的低／中／高，股價各會落在哪。

    給三個點不給一個點 —— 只給中位數會被當成目標價，而這只是除法不是預測。
    中位數不用平均，才不會被那幾天的極端值拉走。
    """
    at = lambda pe: {"pe": round(pe, 1), "price": round(pe * eps_now, 2),
                     "gap": _pct(pe * eps_now, price_now)}
    return {"now": round(price_now, 2), "eps": round(eps_now, 2),
            "low": at(min(pes)), "mid": at(median(pes)), "high": at(max(pes))}


def build_valuation(fin: dict, years: int) -> dict:
    """years 決定回看多久。本益比區間和漲跌拆解看的是同一段時間，不會各講各的。"""
    known = known_eps(fin["quarters"], fin["releases"])
    series = [{"date": c["date"], "pe": c["close"] / e, "eps": e}
              for c in fin["closes"]
              if (e := _eps_at(known, c["date"]))]
    if len(series) < 2:
        raise RuntimeError("這家公司沒有可用的每股盈餘（可能在虧損），算不出本益比")

    start = (date.fromisoformat(series[-1]["date"]) - timedelta(days=round(365.25 * years))).isoformat()
    window = [s for s in series if s["date"] >= start] or series[-2:]

    pes = [s["pe"] for s in window]
    lo, hi, now = min(pes), max(pes), pes[-1]
    # 高低點是哪一天發生的。不標日期，「1 年跟 5 年區間一模一樣」看起來就像拉桿壞了，
    # 其實是兩個極端都落在最近這一年裡。
    cold = min(window, key=lambda s: s["pe"])["date"]
    hot = max(window, key=lambda s: s["pe"])["date"]
    a, b = window[0], window[-1]
    split = {
        "price": _pct(b["pe"] * b["eps"], a["pe"] * a["eps"]),
        "earnings": _pct(b["eps"], a["eps"]),
        "multiple": _pct(b["pe"], a["pe"]),
        "eps_from": round(a["eps"], 2),
        "eps_to": round(b["eps"], 2),
    }

    margins = _margins(fin["revenue"], fin["gross"], fin["net"], fin.get("shares", {}))
    pct = round((now - lo) / (hi - lo) * 100) if hi > lo else 50
    trap = _pe_trap(split["earnings"], split["price"], years)
    quality = earnings_quality(known, margins)
    sliced = slice_eps(margins)
    return {
        "years": years,
        "from": a["date"],
        "to": b["date"],
        "quality": quality,
        "fair": _fair(pes, b["eps"], b["pe"] * b["eps"]),
        "pe": {"low": round(lo, 1), "high": round(hi, 1), "now": round(now, 1),
               "low_at": cold, "high_at": hot,
               "pct": pct,
               "trap": trap,
               # 「所以現在到底貴不貴」的一句話。上面那堆數字都在講過程，這裡答結果
               "verdict": price_verdict(pct, years, trap),
               # 每個點都帶日期、當天股價、當時的滾動盈餘，滑過去才答得出「當時為什麼是這個價」。
               # 本益比由四捨五入後的股價與盈餘算回來，畫面上那道除法才按得出同一個答案。
               "spark": [_spark_point(s) for s in downsample(window, VALUATION_SPARK_POINTS)]},
        "split": split,
        "margins": margins,
        "slice": sliced,
        # 第二區三個小節唯一跨小節的判斷：誰撐的 × 還撐不撐得住
        "support": support_verdict(split, quality, sliced),
        "eps_points": len(known),
    }
