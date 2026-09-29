"""用真的瀏覽器跑一次完整流程，截圖存檔。curl 只看得到 HTML，看不到畫出來長怎樣。

    .venv/bin/python tools/smoke.py [SYMBOL] [PORT]
"""

import datetime as dt
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).parent.parent
SHOTS = ROOT / "shots"
load_dotenv(ROOT / ".env")

sys.path.insert(0, str(ROOT))
from app.config import RISK_RATIOS, SECTOR_ETF  # noqa: E402


def serve(port: int):
    proc = subprocess.Popen(
        [".venv/bin/uvicorn", "app.main:app", "--port", str(port)],
        cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )
    time.sleep(5)
    return proc


def check(page, name: str, selector: str) -> bool:
    ok = page.locator(selector).count() > 0
    print(f"  {'✅' if ok else '❌'} {name}  ({selector})")
    return ok


def etf_order(page) -> list[str]:
    return page.evaluate(
        "() => [...document.querySelectorAll('#rotBody .rot-row .etf')].map(e => e.textContent)")


def run(symbol: str, port: int) -> bool:
    SHOTS.mkdir(exist_ok=True)
    results = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1600, "height": 1200})
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)

        # 沒登入的話首頁會被轉去 /login，後面每一項都會失敗 —— 先確認這道門真的擋得住
        page.goto(f"http://127.0.0.1:{port}/")
        gated = page.url.endswith("/login") and page.locator("#pw").count() == 1
        print(f"  {'✅' if gated else '❌'} 沒登入會被擋到登入頁 ({page.url})")
        results.append(gated)

        # 背景每次隨機挑一種配色。圖沒載到只會是一片空白，naturalWidth 要真的量
        page.wait_for_function(
            "() => { const i = document.getElementById('bg'); return i.complete; }", timeout=10_000)
        scene = page.evaluate("""() => {
            const i = document.getElementById('bg');
            return { bg: document.body.dataset.bg, w: i.naturalWidth, h: i.naturalHeight,
                     src: i.currentSrc.split('/').pop() }; }""")
        painted = (scene["bg"] in ("purple", "amber", "sage")
                   and scene["src"] == f"login-bg-{scene['bg']}.png"
                   and scene["w"] > 1000 and scene["h"] > 400)
        print(f"  {'✅' if painted else '❌'} 登入背景隨機配色、底圖真的載到 {scene}")
        results.append(painted)

        page.fill("#pw", "nope")
        page.click("#go")
        page.wait_for_function("() => document.getElementById('err').textContent", timeout=10_000)
        refused = page.locator("#err").inner_text().strip() == "✕ 密碼不對"
        print(f"  {'✅' if refused else '❌'} 密碼錯會被擋下來，不會放行")
        results.append(refused)

        page.fill("#pw", os.environ["APP_PASSWORD"])
        page.click("#go")
        page.wait_for_selector("#sym", timeout=30_000)
        landed = page.url.endswith("/main")
        print(f"  {'✅' if landed else '❌'} 密碼對就進到主頁面 /main ({page.url})")
        results.append(landed)

        # 登入後再打一次根目錄，要自己轉去 /main，不是停在一個空白的 /
        page.goto(f"http://127.0.0.1:{port}/")
        page.wait_for_selector("#sym", timeout=30_000)
        rooted = page.url.endswith("/main")
        print(f"  {'✅' if rooted else '❌'} 登入後 / 會自動轉去 /main ({page.url})")
        results.append(rooted)
        # 上面故意打錯密碼，那個 401 是預期中的。登入流程已經用三個檢查釘死了，
        # 留著只會讓「主控台有沒有錯誤」這一項永遠是紅的
        errors.clear()

        page.fill("#sym", symbol)
        page.click("#go")
        page.wait_for_selector("#out .card", timeout=60_000)
        page.wait_for_timeout(1500)

        print(f"\n搜尋 {symbol} 之後：")
        results.append(check(page, "公司基本資料卡", "details.basics"))

        # 版面順序與左右配對。量的是實際座標，不是 DOM 順序 —— grid 會重排
        layout = page.evaluate("""() => {
            const box = s => { const e = document.querySelector(s);
                const r = e.getBoundingClientRect();
                return { top: Math.round(r.top + scrollY), left: Math.round(r.left) }; };
            const b = { 股價: box('#chartCard'), 對照: box('#compare .card'),
                        基本: box('details.basics'), 回購: box('#buyback .card'),
                        流程: box('#flowCard'),
                        新聞數: box('#summary .card'), 溫度: box('#attention .card'),
                        異動日: box('#out .card') };
            const pair = (a, c) => Math.abs(b[a].top - b[c].top) < 40 && b[a].left < b[c].left;
            const rows = ['流程', '股價', '基本', '新聞數', '異動日'];
            return { b, paired: pair('股價', '對照') && pair('基本', '回購')
                                 && pair('新聞數', '溫度'),
                     stacked: rows.every((k, i) => !i || b[rows[i - 1]].top < b[k].top) }; }""")
        laid = layout["paired"] and layout["stacked"]
        print(f"  {'✅' if laid else '❌'} 版面：流程圖 → 股價｜對照 → 基本｜回購 → 新聞數｜溫度 → 異動日")
        if not laid:
            print("     ", layout["b"])
        results.append(laid)

        # 每一列左右兩張卡要一樣高，不准一高一矮
        heights = page.evaluate("""() => {
            const h = s => Math.round(document.querySelector(s).getBoundingClientRect().height);
            return { 股價: h('#chartCard'), 對照: h('#compare .card'),
                     基本: h('details.basics'), 回購: h('#buyback .card'),
                     新聞數: h('#summary .card'), 溫度: h('#attention .card') }; }""")
        pairs = [("股價", "對照"), ("基本", "回購"), ("新聞數", "溫度")]
        even = all(abs(heights[a] - heights[b]) <= 2 for a, b in pairs)
        print(f"  {'✅' if even else '❌'} 每列左右兩張卡等高 {heights}")
        results.append(even)

        # 兩張長新聞卡預設收起來，要點才展開
        folds = page.evaluate(
            "() => [...document.querySelectorAll('#out details.fold')].map(d => d.open)")
        folded = folds == [False, False]
        print(f"  {'✅' if folded else '❌'} 異動日卡與其餘新聞卡預設收起 {folds}")
        results.append(folded)

        # 卡片要吃滿工具列右邊的空間，不准中間開一條大空溝
        gap = page.evaluate("""() => {
            const r = document.querySelector('#rail').getBoundingClientRect();
            const c = document.querySelector('#chartCard').getBoundingClientRect();
            return { gap: Math.round(c.left - r.right), width: Math.round(c.width) }; }""")
        tight = 0 <= gap["gap"] <= 32
        print(f"  {'✅' if tight else '❌'} 卡片離工具列 {gap['gap']}px、股價卡寬 {gap['width']}px")
        results.append(tight)

        # 回購卡：最上面那個「推力」必須真的是股數變化的鏡像，
        # 「公司一年還你」也必須真的等於回購＋配息 —— 畫面上的數字要能自己對得起來
        page.wait_for_selector("#buyback .card .bb-head b", timeout=60_000)
        bb = page.evaluate("""() => {
            const t = document.querySelector('#buyback .card').textContent.replace(/\\s+/g, ' ');
            const pts = document.querySelector('#buyback .bb-line polyline')
                .getAttribute('points').split(' ').length;
            const g = k => { const m = t.match(k); return m ? m[1] : null; };
            return { txt: t.trim().slice(0, 300), pts,
                     boost: +document.querySelector('#buyback .bb-head b').innerText.replace('%', ''),
                     total: +g(/公司一年還你 ([\\d.]+)%/),
                     back: +g(/回購 ([\\d.]+)%/),
                     div: +g(/配息 ([\\d.]+)%/),
                     spent: g(/近四季花了 ([\\d.]+ [億兆]) 美元/),
                     cap: g(/市值 ([\\d.]+ [億兆]) 美元/),
                     shrink: +g(/年變化 ([-+]?[\\d.]+)%/) }; }""")
        # 畫面上可能是「億」也可能是「兆」，換算回同一個單位再驗
        def amount(text):
            n, unit = text.split()
            return float(n) * (1e12 if unit == "兆" else 1e8)

        derived = amount(bb["spent"]) / amount(bb["cap"]) * 100
        mirror = (1 / (1 + bb["shrink"] / 100) - 1) * 100
        ok = (abs(derived - bb["back"]) < 0.06 and bb["pts"] >= 4  # 殖利率至少要四季才算得出來
              and abs(mirror - bb["boost"]) < 0.11
              and abs(bb["back"] + bb["div"] - bb["total"]) < 0.02)
        print(f"  {'✅' if ok else '❌'} 回購卡數字自洽：股數年變化 {bb['shrink']}% → 推力 {bb['boost']}%"
              f"（應為 {mirror:.1f}%）｜還你 {bb['total']}% = 回購 {bb['back']}%"
              f"（{bb['spent']}/{bb['cap']}={derived:.2f}%）+ 配息 {bb['div']}%")
        print(f"      {bb['txt'][:170]}")
        results.append(ok)
        # 每年回購金額和年底股數必須並排。只給金額看不出那筆錢有沒有變成你的
        rows = page.evaluate("""() => [...document.querySelectorAll('#buyback .grid4:not(.hd)')].map(
            r => [...r.children].map(c => c.innerText))""")
        table = len(rows) >= 3 and all(len(r) == 4 and "億股" in r[2] for r in rows)
        print(f"  {'✅' if table else '❌'} 年度回購表有 {len(rows)} 列，金額與年底股數並排")
        for r in rows:
            print("     ", "  ".join(r))
        results.append(table)
        line = page.evaluate("""() => {
            const p = document.querySelector('#buyback .bb-line polyline');
            return { bars: document.querySelectorAll('#buyback .bb-bars').length,
                     pts: p ? p.getAttribute('points').split(' ').length : 0 }; }""")
        drawn = line["pts"] > 8 and line["bars"] == 0
        print(f"  {'✅' if drawn else '❌'} 股數走勢是折線（{line['pts']} 個點），長條圖已移除 {line}")
        results.append(drawn)
        page.locator("#buyback").screenshot(path=SHOTS / "8-buyback.png")

        results.append(check(page, "大盤／同業對照卡", "#compare .cmp-row"))
        results.append(check(page, "流程圖節點", "#flow g[id^='n-']"))
        # 基本資料卡預設就展開，不用點；順便確認畫面上已經沒有新聞天數拉桿了
        opened = page.evaluate("""() => ({
            open: document.querySelector('details.basics').open,
            slider: document.querySelectorAll('input[type=range]').length })""")
        basics_ok = opened["open"] and opened["slider"] == 0
        print(f"  {'✅' if basics_ok else '❌'} 基本資料卡預設展開、沒有新聞天數拉桿 {opened}")
        results.append(basics_ok)
        page.wait_for_selector(".macro .mrow", timeout=30_000)
        results.append(check(page, "FRED 總經指標", ".macro .mrow"))
        results.append(check(page, "指標走勢圖", ".macro svg.spark polyline"))
        for t in page.locator(".macro .mitem").all_inner_texts():
            print("     ", t.replace("\n", "  "))
        # 每個指標都要有一句「對這家公司好還是壞」，少一句就是方向表漏設定了
        whys = page.locator(".macro .mwhy").all_inner_texts()
        n_rows = page.locator(".macro .mrow").count()
        told = len(whys) == n_rows and all(
            w.strip()[0] in "✅❌➖" and "：" in w or "沒動" in w for w in whys)
        print(f"  {'✅' if told else '❌'} 每個總經指標都說明了正向／負面（{len(whys)}/{n_rows}）")
        results.append(told)
        results.append(check(page, "股價圖", "#chart svg"))
        results.append(check(page, "高低區間 bar", ".hilo .rbar .rdot"))
        for t in page.locator(".hilo .rbar").all_inner_texts():
            print("     ", t.replace("\n", "  "))
        bars = page.evaluate("""() => [...document.querySelectorAll('.rbar')].map(b => ({
            h: +b.getBoundingClientRect().height.toFixed(1),
            over: b.querySelector('.rtitle').scrollWidth > b.clientWidth,
            dot: getComputedStyle(b.querySelector('.rdot')).backgroundColor }))""")
        fit = not any(b["over"] for b in bars)
        print(f"  {'✅' if fit else '❌'} 區間 bar 文字沒溢出　高度 {[b['h'] for b in bars]}px")
        print("      圓點顏色:", [b["dot"] for b in bars])
        results.append(fit)

        page.wait_for_selector(".att-bars i:not(.att-grid)", timeout=40_000)
        results.append(check(page, "注意力溫度計", ".att-bars i:not(.att-grid)"))
        att = page.evaluate("""() => {
            const b = [...document.querySelectorAll('.att-bars i:not(.att-grid)')];
            return { n: b.length, spikes: b.filter(x => x.style.opacity === '1').length,
                     h: b.map(x => +getComputedStyle(x).height.replace('px','')).filter(v => v > 0).length }; }""")
        ok = att["n"] > 0 and att["h"] == att["n"]
        print(f"  {'✅' if ok else '❌'} 溫度計 {att['n']} 根柱子全部有高度，尖峰 {att['spikes']} 根")
        results.append(ok)
        # 縱軸：沒有刻度就看不出「幾則」
        ticks = page.locator(".att-yaxis span").all_inner_texts()
        has_axis = len(ticks) == 3 and ticks[-1] == "0" and int(ticks[0]) > 0
        print(f"  {'✅' if has_axis else '❌'} 溫度計有縱軸刻度 {ticks}")
        results.append(has_axis)
        # 位置：必須自成一張卡、排在新聞卡上面，而不是擠在股價圖下面
        pos = page.evaluate("""() => { const a = document.querySelector('#attention');
            const n = document.querySelector('#out details.fold');
            return { above: a.getBoundingClientRect().top < n.getBoundingClientRect().top,
                     in_chart: !!document.querySelector('#chartCard #attention') }; }""")
        placed = pos["above"] and not pos["in_chart"]
        print(f"  {'✅' if placed else '❌'} 溫度計排在新聞卡上面、不在股價卡裡")
        results.append(placed)
        # 兩張新聞卡：異動日、其餘新聞。同一則標題不准在兩張卡各出現一次
        news = page.evaluate("""() => {
            const cards = [...document.querySelectorAll('#out details.fold')];
            const t = [...document.querySelectorAll('#out .art .ttl')].map(x => x.textContent);
            return { titles: cards.map(c => c.querySelector('summary').textContent.trim()),
                     total: t.length, uniq: new Set(t).size }; }""")
        merged = (len(news["titles"]) == 2 and "異動日" in news["titles"][0]
                  and "其餘新聞" in news["titles"][1] and news["total"] == news["uniq"])
        print(f"  {'✅' if merged else '❌'} 新聞分成兩張可收合的卡、沒有重複標題 {news}")
        results.append(merged)
        # 說明要收合，而且不該把 API 額度那種技術細節寫給使用者看
        note = page.locator(".att-note").inner_text()
        folded = page.locator(".att-note:not([open])").count() == 1
        clean = not any(k in note for k in ("250", "60 次", "請求"))
        print(f"  {'✅' if folded else '❌'} 注意力說明預設是收合的")
        print(f"  {'✅' if clean else '❌'} 說明裡沒有技術細節")
        results += [folded, clean]

        print("\n估值彈窗：")
        page.wait_for_selector("#compare .cmp-head .valu-btn", timeout=60_000)
        # 兩個按鈕要在對照卡的標題列裡，而且在標題右邊
        order = page.evaluate("""() => {
            const b = document.querySelector('#compare .cmp-head .valu-btn');
            const h = document.querySelector('#compare .cmp-head h2');
            return !!b && !!h && b.getBoundingClientRect().left > h.getBoundingClientRect().left; }""")
        print(f"  {'✅' if order else '❌'} 估值按鈕在「大盤／同業對照」標題右邊")
        results.append(order)
        btn = page.evaluate("""() => { const s = getComputedStyle(
                document.querySelector('#compare .cmp-head .valu-btn'));
            return { color: s.color, border: s.borderTopColor }; }""")
        yellow = btn["color"] == "rgb(255, 212, 0)"
        print(f"  {'✅' if yellow else '❌'} 估值按鈕是霓虹黃 {btn['color']}（外框 {btn['border']}）")
        results.append(yellow)
        page.click("#compare .cmp-head .valu-btn")
        page.wait_for_selector("#valuBody .valu-split, #valuBody .err", timeout=60_000)
        if page.locator("#valuBody .err").count():
            print("  ⚠️ 估值算不出來（可能在虧損）:", page.locator("#valuBody .err").inner_text()[:120])
        else:
            results.append(check(page, "本益比區間條", "#valuBody .rbar .rdot"))
            h3 = page.evaluate("""() => [...document.querySelectorAll('#valuBody h3')].map(
                h => getComputedStyle(h).color)""")
            warm = len(h3) == 5 and all(c in ("rgb(255, 140, 26)", "rgb(179, 92, 0)") for c in h3)
            print(f"  {'✅' if warm else '❌'} 估值彈窗五個標題是橙橘色 {h3}")
            results.append(warm)

            # 「盈餘撐不撐得住」四題都要有答案 —— 少一題，低本益比就又變回沒頭沒尾的數字
            qa = page.evaluate("""() => [...document.querySelectorAll('#valuBody .qa-row')].map(r => ({
                mark: r.querySelector('.qm').textContent.trim(),
                ask: r.querySelector('.qk').textContent.trim(),
                val: r.querySelector('.qv').textContent.trim(),
                why: r.querySelector('.qt').childNodes[0].textContent.trim(),
                basis: r.querySelector('.qb').textContent.trim() }))""")
            answered = len(qa) == 4 and all(
                x["mark"] in "✅❌➖" and x["ask"].endswith("？") and x["why"]
                and "vs" in x["basis"] for x in qa)
            qa_rows = lambda: page.evaluate(
                "() => [...document.querySelectorAll('#valuBody .qa-row')]"
                ".map(r => r.textContent.replace(/\\s+/g, ' ').trim())")
            qa5 = qa_rows()
            print(f"  {'✅' if answered else '❌'} 盈餘體檢四題都有答案、都寫明拿哪兩段比")
            for x in qa:
                print(f"      {x['mark']} {x['ask']} {x['val']}　{x['why']}　[{x['basis']}]")
            results.append(answered)

            # 換「回看幾年」，體質四題不准跟著動；本益比高低點則要標出發生的日期
            page.click("#valuYears label:has(input[value='1'])")
            page.wait_for_selector("#valuBody .qa-row", timeout=60_000)
            page.wait_for_function(
                "() => document.querySelector('#valuBody h3')"
                "?.textContent.includes('這 1 年')",
                timeout=60_000)
            qa1 = qa_rows()
            stable = len(qa1) == 4 and qa1 == qa5
            print(f"  {'✅' if stable else '❌'} 切到 1 年後，體質四題原封不動")
            if not stable:
                print("      1年:", qa1[:1], "\n      5年:", qa5[:1])
            results.append(stable)
            dated = page.evaluate("""() => {
                const t = [...document.querySelectorAll('#valuBody .caveat')]
                    .find(c => c.textContent.includes('最低'));
                return t ? t.textContent.replace(/\s+/g, ' ').trim() : '缺'; }""")
            has_dates = len(re.findall(r"\d{4}-\d{2}-\d{2}", dated)) == 2
            print(f"  {'✅' if has_dates else '❌'} 本益比高低點有標日期：{dated[:90]}")
            results.append(has_dates)
            # 區間條的圓點比軌道高，會凸出來壓到下一行字 —— 量兩者的實際座標
            clear = page.evaluate("""() => {
                const dot = document.querySelector('#valuBody .rbar .rdot');
                const txt = [...document.querySelectorAll('#valuBody .caveat')]
                    .find(c => c.textContent.includes('最低'));
                const d = dot.getBoundingClientRect(), t = txt.getBoundingClientRect();
                return { gap: Math.round(t.top - d.bottom) }; }""")
            spaced = clear["gap"] >= 0
            print(f"  {'✅' if spaced else '❌'} 區間條圓點沒壓到下一行字（距離 {clear['gap']}px）")
            results.append(spaced)
            page.click("#valuYears label:has(input[value='5'])")
            page.wait_for_function(
                "() => document.querySelector('#valuBody h3')"
                "?.textContent.includes('這 5 年')",
                timeout=60_000)

            # 參考價給低／中／高三個，不給單一目標價；而且必須等於本益比 × 當前盈餘
            fair = page.evaluate("""() => {
                const cells = [...document.querySelectorAll('#valuBody .fair-cell')].map(c => ({
                    k: c.querySelector('.fk').textContent.trim(),
                    price: parseFloat(c.querySelector('.fv').textContent.replace('$', '')),
                    pe: parseFloat(c.textContent.match(/本益比 ([\d.]+)/)[1]) }));
                const hot = [...document.querySelectorAll('#valuBody .caveat b.hot')];
                const head = hot[0].closest('.caveat').textContent;
                return { cells, eps: parseFloat(head.match(/盈餘 \$([\d.]+)/)[1]),
                         yellow: hot.length === 2
                             && hot.every(b => getComputedStyle(b).color === 'rgb(255, 212, 0)') }; }""")
            priced = (len(fair["cells"]) == 3
                      and all(abs(c["pe"] * fair["eps"] - c["price"]) < c["price"] * 0.01
                              for c in fair["cells"])
                      and fair["cells"][0]["price"] < fair["cells"][2]["price"])
            print(f"  {'✅' if priced else '❌'} 參考價低/中/高三個且 = 本益比×盈餘({fair['eps']}) "
                  f"{[c['price'] for c in fair['cells']]}")
            results.append(priced)
            # 「最冷／最熱」講的是市場願不願意付錢，不是股價高低 —— 標題要自己說清楚
            named = all("市場" in c["k"] for c in fair["cells"])
            print(f"  {'✅' if named else '❌'} 三格標題講的是市場態度 {[c['k'] for c in fair['cells']]}")
            results.append(named)
            print(f"  {'✅' if fair['yellow'] else '❌'} 現價與盈餘兩個前提數字是霓虹黃")
            results.append(fair["yellow"])
            results.append(check(page, "漲幅拆解", "#valuBody .valu-split .valu-line"))
            results.append(check(page, "營收毛利", "#valuBody .valu-cell"))
            for t in page.locator("#valuBody .valu-line").all_inner_texts():
                print("     ", t.replace("\n", "  "))
            # 拆解的算術必須自洽：(1+獲利)×(1+倍數) 要等於 (1+總漲幅)
            nums = page.evaluate("""() => [...document.querySelectorAll('#valuBody .valu-line .vn')]
                .map(x => parseFloat(x.textContent))""")
            e, m, p = nums
            exact = abs((1 + e / 100) * (1 + m / 100) - (1 + p / 100)) < 0.01
            print(f"  {'✅' if exact else '❌'} 拆解自洽: (1{e:+.1f}%)×(1{m:+.1f}%) = 1{p:+.1f}%")
            results.append(exact)
            page.locator("#valuBody").screenshot(path=SHOTS / "4-valuation.png")
            # 本益比區間和漲跌拆解必須講同一段時間，不能一個五年一個一年
            spans = page.evaluate("""() => [...document.querySelectorAll('#valuBody h3')]
                .map(h => h.textContent.match(/這 (\\d) 年/)?.[1]).filter(Boolean)""")
            same = len(spans) == 2 and spans[0] == spans[1]
            print(f"  {'✅' if same else '❌'} 兩個區塊看同一段時間 {spans}")
            results.append(same)
            # 本益比折線要能滑動回推：滑到最左邊，讀數必須是那一天的真實股價與盈餘
            spark = page.locator("#peSpark svg")
            b = spark.bounding_box()
            spark.hover(position={"x": 4, "y": b["height"] / 2})
            page.wait_for_function(
                "() => !document.querySelector('#peRead').textContent.includes('滑動')", timeout=10_000)
            read = page.locator("#peRead").inner_text().replace("\n", " ")
            left_date = page.locator("#peSpark .pk-ends span").first.inner_text()
            nums = page.evaluate("""() => [...document.querySelectorAll('#peRead b')].map(x => x.textContent)""")
            px, eps, pe = float(nums[1].lstrip("$")), float(nums[2]), float(nums[3])
            consistent = nums[0] == left_date and abs(px / eps - pe) < 0.1
            print(f"  {'✅' if consistent else '❌'} 折線可回推：{read}")
            results.append(consistent)
            page.wait_for_timeout(200)  # 小圓點有 0.12s 淡入，太早量會量到中間值
            dot = page.evaluate("""() => { const d = document.querySelector('#peSpark .pk-dot');
                return { on: getComputedStyle(d).opacity === '1', top: d.style.top }; }""")
            print(f"  {'✅' if dot['on'] else '❌'} 游標小圓點有跟著出現 {dot}")
            results.append(dot["on"])

            # 盈餘漲得比股價快的公司，百分位會低到誤導人 —— 旁邊必須有那段警語，
            # 而且警語裡的兩個倍數要跟「股價漲跌是從哪來的」那一段算出來的一致
            trap = page.evaluate("""() => {
                const t = document.querySelector('#peTrap');
                const txt = document.querySelector('#valuBody').textContent;
                const g = k => { const m = txt.match(k); return m ? +m[1] : null; };
                return { shown: !!t, txt: t && t.textContent.replace(/\\s+/g, ' ').trim(),
                         earnings: g(/公司真的多賺\\s*([-+]?[\\d.]+)%/),
                         price: g(/股價總共\\s*([-+]?[\\d.]+)%/) }; }""")
            if trap["shown"]:
                xs = [float(x) for x in re.findall(r"([\d.]+) 倍", trap["txt"])]
                agrees = (abs(xs[0] - (1 + trap["earnings"] / 100)) < 0.06
                          and abs(xs[1] - (1 + trap["price"] / 100)) < 0.06 and xs[0] > xs[1])
                print(f"  {'✅' if agrees else '❌'} 盈餘暴衝警語數字自洽 {xs} "
                      f"vs 盈餘{trap['earnings']}% 股價{trap['price']}%")
            else:
                agrees = trap["earnings"] < 100 or trap["earnings"] < trap["price"] * 1.4
                print(f"  {'✅' if agrees else '❌'} 沒有盈餘暴衝，正確地不顯示警語 "
                      f"(盈餘{trap['earnings']}% 股價{trap['price']}%)")
            results.append(agrees)

            page.locator("#valuBody").screenshot(path=SHOTS / "4-valuation-hover.png")
            page.mouse.move(b["x"] + b["width"] / 2, b["y"] - 60)
            # 股價軌道：帶子要真的畫出來，而且畫面上的「差 N%」必須等於股價/中心線算出來的
            page.wait_for_selector("#trendChart svg path", timeout=60_000)
            band = page.evaluate("""() => {
                const svg = document.querySelector('#trendChart svg');
                const q = s => svg.querySelectorAll(s).length;
                const r = svg.getBoundingClientRect();
                return { bands: [...svg.querySelectorAll('path')]
                             .filter(x => x.getAttribute('fill') === 'var(--warning)').length,
                         dots: q('.tr-pt'), mid: q('.tr-mid'), marks: q('.trend-mark'),
                         future: q('.tr-future'), today: q('.tr-today'),
                         w: Math.round(r.width), h: Math.round(r.height) }; }""")
            # 每段兩層帶子、一條中心虛線、一條財報標記，所以 bands 必須是 mid 的兩倍
            drawn = (band["bands"] == band["mid"] * 2 and band["mid"] == band["marks"]
                     and band["dots"] > 50 and band["future"] == 2 and band["today"] == 1
                     and band["w"] > 600 and band["h"] > 200)
            print(f"  {'✅' if drawn else '❌'} 軌道帶子＋點圖＋預估段都畫出來了 {band}")
            results.append(drawn)

            # 版面：本益比 → 漲跌拆解 → 營收毛利 → 散點圖（散點圖在最下面）
            order = page.evaluate("""() => {
                const top = s => document.querySelector(s).getBoundingClientRect().top;
                const h = [...document.querySelectorAll('#valuBody h3')];
                const at = k => h.find(x => x.textContent.includes(k)).getBoundingClientRect().top;
                const txt = document.querySelector('#valuBody').textContent;
                return { spark: top('#peSpark'), split: at('股價漲跌是從哪來'),
                         rev: at('營收與利潤率'), chart: top('#trendChart'),
                         dropped: !txt.includes('沒有往未來外推') && !txt.includes('站在哪裡') }; }""")
            placed = (order["spark"] < order["split"] < order["rev"] < order["chart"]
                      and order["dropped"])
            print(f"  {'✅' if placed else '❌'} 散點圖排到最下面、兩句話已拿掉 {order}")
            results.append(placed)

            tnums = page.evaluate("""() => {
                const n = document.querySelector('#trendNow');
                return { txt: n.textContent.replace(/\\s+/g, ' ').trim(),
                         b: [...n.querySelectorAll('b')].map(x => x.textContent) }; }""")
            price, fair = (float(x.lstrip("$±")) for x in tnums["b"][:2])
            gap = float(re.search(r"差 ([-+]?[\d.]+)%", tnums["txt"]).group(1))
            honest = abs((price / fair - 1) * 100 - gap) < 0.15
            print(f"  {'✅' if honest else '❌'} 軌道判語自洽：{tnums['txt']}")
            page.locator("#trendChart").screenshot(path=SHOTS / "6-trend.png")
            results.append(honest)

            # x 軸要有日期刻度，而且由左至右遞增
            lab = page.evaluate("""() => {
                const t = [...document.querySelectorAll('#trendChart .tr-xlab')]
                    .map(e => ({ v: e.textContent, r: e.getBoundingClientRect() }))
                    .sort((a, b) => a.r.left - b.r.left);
                return { xs: t.map(o => o.v),
                         overlap: t.filter((o, i) => i && o.r.left < t[i - 1].r.right).length }; }""")
            xs = lab["xs"]
            # 標籤要是日期、由左至右遞增，而且量出來的框不准互相壓到
            dated = (len(xs) >= 4 and xs == sorted(xs) and lab["overlap"] == 0
                     and re.match(r"^財報 \d{4}-\d{2}-\d{2}$", xs[0]))
            print(f"  {'✅' if dated else '❌'} 軌道 x 軸標在財報日、沒重疊 {xs}")
            results.append(dated)

            # 白色虛線是「今天」不是財報日，沒標字的話最後一個股價點碰到它會被誤讀
            today_lab = page.evaluate(
                "() => { const e = document.querySelector('#trendChart .tr-today-lab');"
                " return e ? e.textContent : '缺'; }")
            marked = bool(re.match(r"^今天 \d{4}-\d{2}-\d{2}$", today_lab.strip()))
            print(f"  {'✅' if marked else '❌'} 今天那條白線有標字：{today_lab.strip()}")
            results.append(marked)

            # 滑過軌道圖要報出「那一天」與「那天的股價」，不能只有提示文字
            page.locator("#trendChart").scroll_into_view_if_needed()
            tb = page.locator("#trendChart").bounding_box()
            page.mouse.move(tb["x"] + tb["width"] * 0.6, tb["y"] + tb["height"] / 2)
            hov = page.locator("#trendRead").inner_text()
            hovered = bool(re.search(r"\d{4}-\d{2}-\d{2}.*\$[\d.]+", hov.replace("\n", " ")))
            print(f"  {'✅' if hovered else '❌'} 軌道 hover 顯示當天股價：{hov.strip()}")
            results.append(hovered)

            # 框選一段：基準日要退回框選的結尾，之後的股價不准再出現在區間裡
            before_to = page.evaluate(
                "() => document.querySelector('#valuBody .caveat').textContent")
            page.mouse.move(tb["x"] + tb["width"] * 0.35, tb["y"] + tb["height"] / 2)
            page.mouse.down()
            page.mouse.move(tb["x"] + tb["width"] * 0.8, tb["y"] + tb["height"] / 2, steps=12)
            page.mouse.up()
            page.wait_for_selector("#trendReset", timeout=60_000)
            asof = page.evaluate("""() => {
                const c = document.querySelector('#valuBody .caveat').textContent;
                const h = document.querySelector('#valuBody h3').textContent;
                const xs = [...document.querySelectorAll('#trendChart .tr-xlab')]
                    .map(e => e.textContent).sort();
                return { c: c.replace(/\\s+/g, ' ').trim(), h: h.trim(),
                         last: xs[xs.length - 1] }; }""")
            day = re.search(r"(\d{4}-\d{2}-\d{2})", asof["c"])
            # 標題要指名基準日；x 軸右端是下一次財報日，必須落在基準日之後 15～200 天
            ahead = (dt.date.fromisoformat(asof["last"].replace("財報 ", ""))
                     - dt.date.fromisoformat(day.group(1))).days if day else 0
            picked = bool(day) and day.group(1) in asof["h"] and 0 < ahead <= 200
            print(f"  {'✅' if picked else '❌'} 框選後基準日退回 {day and day.group(1)}"
                  f"，軌道往前畫到 {asof['last']}（{ahead} 天後的下次財報）")
            page.locator("#valuBody").screenshot(path=SHOTS / "7-trend-asof.png")
            results.append(picked)

            page.click("#trendReset")
            page.wait_for_function("""() => !document.querySelector('#trendReset')
                && document.querySelector('#trendChart svg')""", timeout=60_000)
            back = page.evaluate(
                "() => document.querySelector('#valuBody .caveat').textContent") == before_to
            print(f"  {'✅' if back else '❌'} 「回到今天」把基準日清掉了")
            results.append(back)

            # 回看幾年是勾選鈕：五個選項、預設勾 5 年
            picks = page.evaluate("""() => {
                const r = [...document.querySelectorAll('#valuYears input')];
                return { vals: r.map(x => x.value), on: r.filter(x => x.checked).map(x => x.value) }; }""")
            boxed = picks["vals"] == ["1", "2", "3", "4", "5"] and picks["on"] == ["5"]
            print(f"  {'✅' if boxed else '❌'} 回看幾年改成勾選鈕、預設 5 年 {picks}")
            results.append(boxed)

            # 勾 1 年，區間必須真的變窄（回看越短，看到的高低差不可能更大）
            wide = page.evaluate("""() => { const t = document.querySelector('#valuBody h3').textContent;
                const m = t.match(/([\\d.]+) – ([\\d.]+)/); return +m[2] - +m[1]; }""")
            page.click("#valuYears input[value='1'] + span")
            page.wait_for_function(
                "() => { const h = document.querySelector('#valuBody h3');"
                "return h && h.textContent.includes('這 1 年'); }", timeout=60_000)
            narrow = page.evaluate("""() => { const t = document.querySelector('#valuBody h3').textContent;
                const m = t.match(/([\\d.]+) – ([\\d.]+)/); return +m[2] - +m[1]; }""")
            shrank = narrow <= wide
            print(f"  {'✅' if shrank else '❌'} 勾 5 年→1 年，本益比區間寬度 {wide:.1f} → {narrow:.1f}")
            results.append(shrank)
            print("     ", page.locator("#valuBody h3").first.inner_text())
            page.locator("#valuBody").screenshot(path=SHOTS / "4-valuation-1y.png")
        page.keyboard.press("Escape")
        results.append(page.locator("#valuModal.hidden").count() == 1)

        # 流程圖填滿：SVG 畫的內容應該佔掉容器高度的大半，而不是縮在中間一條
        box = page.locator("#flow").bounding_box()
        ink = page.evaluate("() => { const b = document.querySelector('#flow').getBBox();"
                            "const vb = document.querySelector('#flow').viewBox.baseVal;"
                            "return (b.y + b.height - b.y) / vb.height; }")
        # 字放大後會不會撐破框框：量每個節點文字的實際寬度，跟 rect 比
        over = page.evaluate("""() => [...document.querySelectorAll("#flow g[id^='n-']")].map(g => {
            const w = g.querySelector('rect').width.baseVal.value;
            const t = Math.max(...[...g.querySelectorAll('text:not(.ic)')].map(x => x.getBBox().width));
            return { label: g.querySelector('.node-label').textContent, over: +(t / w).toFixed(2) };
        }).filter(x => x.over > 0.86)""")
        for o in over:
            print(f"  ❌ 節點文字超出框框 {o['over']:.0%}: {o['label']}")
        results.append(not over)
        print(f"  {'✅' if not over else '❌'} 節點文字都在框框內")

        fills = ink > 0.7
        print(f"  {'✅' if fills else '❌'} 流程圖填滿高度  佔 {ink:.0%}（容器 {box['height']:.0f}px）")
        results.append(fills)

        cards = page.locator(".cols > .card")
        h = [cards.nth(i).bounding_box()["height"] for i in range(cards.count())]
        same = len(set(round(x) for x in h)) == 1
        print(f"  {'✅' if same else '❌'} 兩欄等高  {h}")
        results.append(same)

        def compare_title() -> str:
            return page.locator("#compare h2").inner_text()

        rows = page.locator("#compare .cmp-row").all_inner_texts()
        print("  對照卡內容:", " | ".join(r.replace("\n", " ") for r in rows))
        print("  對照卡標題:", compare_title())
        # 判語只能出現一次就是最終版：中途拿大盤湊數的版本可能結論相反
        first = page.evaluate("() => document.querySelectorAll('#compare .cmp-row').length")
        settled = first == page.locator("#compare .cmp-row").count()
        print(f"  {'✅' if settled else '❌'} 對照卡沒有先出一個半成品版本（{first} 列）")
        results.append(settled)
        # 判語必須同時出現「這支」和比較對象的數字，只講一邊讀者還原不出方向
        vt = page.locator("#compare .mverdict").inner_text()
        mine = page.locator("#compare .cmp-me b").inner_text()
        import re as _re
        nums = [float(x) for x in _re.findall(r"[-+]\d+\.\d+", vt)]
        both = any(abs(n - float(mine.rstrip("%"))) < 0.01 for n in nums)
        # 判語是這張卡的結論，不能被 opacity 洗淡或退成灰色
        tone = page.evaluate("""() => { const v = document.querySelector('#compare .mverdict');
            return { c: getComputedStyle(v).color,
                     o: getComputedStyle(v.closest('.market')).opacity }; }""")
        orange = tone["c"] in ("rgb(255, 140, 26)", "rgb(179, 92, 0)") and tone["o"] == "1"
        print(f"  {'✅' if orange else '❌'} 判語是橙橘色、沒被洗淡 {tone}")
        results.append(orange)
        print(f"  {'✅' if both else '❌'} 判語有寫出這支自己的數字 {mine}")
        print("     ", vt)
        results.append(both)
        results.append(check(page, "區間備註", "#compare .caveat"))

        # 「贏／輸板塊幾個百分點」必須是表格裡自己的一欄，而且要跟兩個百分比對得起來
        gap = page.evaluate("""() => {
            const last = [...document.querySelectorAll('#compare .cmp-row')].pop();
            const n = [...last.querySelectorAll('.num')].map(x => x.innerText);
            return { mine: parseFloat(document.querySelector('#compare .cmp-me b').innerText),
                     peer: parseFloat(n[0]), text: n[1] }; }""")
        shown = float(_re.findall(r"[\d.]+", gap["text"])[0])
        diff = abs(gap["mine"] - gap["peer"])
        # 四捨五入到 0.0 就是平手 —— 不准拿捨進後的值去判勝負，會寫出「輸 0 pt」這種自相矛盾的話
        side = "平手" if round(diff, 1) == 0 else "贏" if gap["mine"] > gap["peer"] else "輸"
        # 「贏」後面不准接負數。方向寫在字裡，數字一律是正的
        consistent = (abs(shown - diff) < 0.11
                      and gap["text"].startswith(side) and "-" not in gap["text"])
        print(f"  {'✅' if consistent else '❌'} 對照列自洽：自己 {gap['mine']}% vs 對方 {gap['peer']}% → {gap['text']}")
        results.append(consistent)
        page.screenshot(path=SHOTS / "1-report.png", full_page=True)

        # 一打開預設就是「當日」，不是一個月
        picked = page.evaluate(
            "() => document.querySelector('#chartRanges input:checked').value")
        first = picked == "1D" and "1 天" in compare_title()
        print(f"  {'✅' if first else '❌'} 預設區間是當日（{picked}）")
        results.append(first)

        # 卡片標題一律橙橘色。這裡一次量四種標題，少改一種就會紅
        tones = page.evaluate("""() => {
            const at = s => { const e = document.querySelector(s);
                return e ? getComputedStyle(e).color : "缺"; };
            return { 基本資料: at('details.basics > summary'), 對照: at('#compare .cmp-head h2'),
                     回購: at('#buyback .card > h2'), 溫度計: at('#attention .card > h2'),
                     異動日: at('#out details.fold > summary') }; }""")
        ORANGE = ("rgb(255, 140, 26)", "rgb(179, 92, 0)")
        oranged = all(v in ORANGE for v in tones.values())
        print(f"  {'✅' if oranged else '❌'} 五個卡片標題都是橙橘色 {tones}")
        results.append(oranged)

        print("\n切換走勢圖區間，對照卡要跟著換：")
        before = compare_title()
        page.click("#chartRanges label:has(input[value='1Y'])")
        page.wait_for_function(
            "t => document.querySelector('#compare h2') && "
            "document.querySelector('#compare h2').innerText !== t", arg=before, timeout=60_000)
        after = compare_title()
        changed = "1 年" in after
        print(f"  {'✅' if changed else '❌'} 1D → 1Y")
        print("   ", before)
        print("   ", after)
        print("  ", " | ".join(r.replace("\n", " ") for r in page.locator("#compare .cmp-row").all_inner_texts()))
        results.append(changed)
        page.screenshot(path=SHOTS / "3-compare-1y.png")

        print("\n恐慌指數彈窗：")
        # 入口在對照卡標題列的估值按鈕旁邊，不在左邊工具列
        entry = page.evaluate("""() => {
            const b = document.querySelector('#btnVix');
            const v = document.querySelector('#compare .cmp-head .valu-btn');
            return { in_head: !!b.closest('#compare .cmp-head'), in_rail: !!b.closest('#railItems'),
                     right: b.getBoundingClientRect().left > v.getBoundingClientRect().left }; }""")
        moved = entry["in_head"] and not entry["in_rail"] and entry["right"]
        print(f"  {'✅' if moved else '❌'} 恐慌指數按鈕在估值按鈕右邊、已離開工具列 {entry}")
        results.append(moved)
        page.click("#btnVix")
        page.wait_for_selector("#vixChart svg path", timeout=60_000)
        paths = page.locator("#vixChart svg path").count()
        btns = page.locator("#vixRanges label").count()
        two = paths == 3 and btns == page.locator("#chartRanges label").count()
        print(f"  {'✅' if two else '❌'} VIX 色塊＋兩條線共 {paths} 個路徑、區間鈕 {btns} 顆（跟走勢圖同一組）")
        results.append(two)
        print("     ", page.locator(".vix-head").inner_text().replace("\n", "  "))
        print("     ", page.locator(".vix-verdict").inner_text())
        # 主從關係：股價是主角（粗、不透明），VIX 是背景（細、半透明、畫在最底下）
        weight = page.evaluate("""() => { const p = [...document.querySelectorAll('#vixChart svg path')];
            return { area: p[0].getAttribute('fill').startsWith('url('),
                     vix: +p[1].getAttribute('stroke-width'), price: +p[2].getAttribute('stroke-width'),
                     faded: +p[1].getAttribute('opacity') < 0.6 }; }""")
        hier = weight["area"] and weight["faded"] and weight["price"] > weight["vix"]
        print(f"  {'✅' if hier else '❌'} 股價在上、VIX 退成背景 {weight}")
        results.append(hier)
        # 量真正畫出來的尺寸。只看 viewBox 會被騙：座標系放大但長寬比沒變，圖一樣大
        box = page.evaluate("""() => { const r = document.querySelector('#vixChart svg').getBoundingClientRect();
            return [Math.round(r.width), Math.round(r.height)]; }""")
        big = box[0] >= 1100 and box[1] >= 320
        print(f"  {'✅' if big else '❌'} 圖實際畫出來 {box[0]}×{box[1]}")
        results.append(big)
        # 雙刻度：左邊股價、右邊 VIX。共用一個刻度的話其中一條會被壓成直線
        axes = page.evaluate("""() => { const t = [...document.querySelectorAll('#vixChart .axis-txt')];
            return { left: t.filter(x => x.textContent.startsWith('$')).length,
                     right: t.filter(x => x.classList.contains('vix-tick')).length }; }""")
        dual = axes["left"] == 4 and axes["right"] == 4
        print(f"  {'✅' if dual else '❌'} 左右各一組刻度 {axes}")
        results.append(dual)
        before_vix = page.locator(".vix-verdict").inner_text()
        page.click("#vixRanges label:has(input[value=\'1Y\'])")
        page.wait_for_function(
            "t => { const v = document.querySelector('.vix-verdict');"
            "return v && v.innerText !== t; }", arg=before_vix, timeout=60_000)
        print("      切 1Y →", page.locator(".vix-verdict").inner_text())

        # 滑鼠移到圖上要同時給出日期、股價、恐慌指數，兩條線各一顆點
        vbox = page.locator("#vixChart svg").bounding_box()
        page.mouse.move(vbox["x"] + vbox["width"] * 0.6, vbox["y"] + vbox["height"] * 0.5)
        page.wait_for_function("() => +document.querySelector('#tip').style.opacity === 1",
                               timeout=10_000)
        hov = page.evaluate("""() => ({
            tip: document.querySelector('#tip').innerText,
            dots: [...document.querySelectorAll('#vixChart svg circle')]
                .filter(c => c.getAttribute('opacity') === '1').length,
            over: document.querySelector('#tip').getBoundingClientRect().left })""")
        lines = [x for x in hov["tip"].split("\n") if x.strip()]
        interactive = (len(lines) == 3 and re.match(r"^\d{4}-\d{2}-\d{2}", lines[0])
                       and "$" in lines[1] and "VIX" in lines[2] and hov["dots"] == 2)
        print(f"  {'✅' if interactive else '❌'} 滑過恐慌指數圖有日期／股價／VIX {lines} 點{hov['dots']}顆")
        results.append(interactive)
        page.mouse.move(vbox["x"] - 40, vbox["y"] - 40)
        page.wait_for_function("() => +document.querySelector('#tip').style.opacity === 0",
                               timeout=10_000)
        page.locator("#vixModal .modal-box").screenshot(path=SHOTS / "5-vix.png")
        page.keyboard.press("Escape")
        results.append(page.locator("#vixModal.hidden").count() == 1)

        print("\n側邊欄：")
        # 預設就展開，而且不准蓋到內容：.wrap 的左邊界必須在工具列右緣之外
        rail = page.evaluate("""() => {
            const r = document.querySelector('#rail').getBoundingClientRect();
            const w = document.querySelector('.wrap').getBoundingClientRect();
            const s = getComputedStyle(document.querySelector('.wrap'));
            return { open: document.body.dataset.rail === "1", labels:
                       [...document.querySelectorAll('.rlabel')].every(e => e.offsetWidth > 0),
                     clear: w.left + parseFloat(s.paddingLeft) >= r.right,
                     railRight: Math.round(r.right),
                     contentLeft: Math.round(w.left + parseFloat(s.paddingLeft)) }; }""")
        opened = rail["open"] and rail["labels"] and rail["clear"]
        print(f"  {'✅' if opened else '❌'} 側邊欄預設展開且沒蓋到內容 {rail}")
        results.append(opened)
        results.append(check(page, "Heatmap 按鈕", "#btnHeat"))
        results.append(check(page, "行事曆按鈕", "#btnCal"))
        page.click("#btnCal")
        page.wait_for_selector(".calrow", timeout=30_000)
        results.append(check(page, "月曆格子", ".grid7 .cell"))
        results.append(check(page, "行事曆項目", ".calrow"))
        print("  首筆:", page.locator(".calrow").first.inner_text().replace("\n", " "))
        note = page.locator("#calBody .caveat").first.inner_text()
        got = "下一次財報日" in note
        print(f"  {'✅' if got else '❌'} 下次財報日: {note}")
        results.append(got)
        page.screenshot(path=SHOTS / "2-calendar.png")
        page.click("#calClose")

        print("\n資金流向彈窗：")
        results.append(check(page, "資金流向按鈕", "#btnRot"))
        page.click("#btnRot")
        page.wait_for_selector("#rotBody .rot-row, #rotBody .err", timeout=60_000)
        results.append(check(page, "板塊排行", "#rotBody .rot-row"))
        # 設定裡有幾個比值，畫面上就要有幾列。少一列代表某檔 ETF 的資料被靜默丟掉了
        n_risk = page.locator("#rotBody .rot-risk").count()
        full = n_risk == len(RISK_RATIOS)
        print(f"  {'✅' if full else '❌'} 風險胃納比值 {n_risk}/{len(RISK_RATIOS)} 列都畫出來")
        results.append(full)
        # 順序是固定的（config 的 SECTOR_ETF），不隨天數重排 —— 每換一次區間就跳位置，根本追不到
        order = etf_order(page)
        fixed = order == list(SECTOR_ETF.values())
        print(f"  {'✅' if fixed else '❌'} 11 個板塊照 SECTOR_ETF 的固定順序排 {order}")
        results.append(fixed)
        print("  判語:", page.locator("#rotBody .rot-verdict").inner_text())
        for t in page.locator("#rotBody .rot-risk").all_inner_texts():
            print("   ", t.replace("\n", "  "))
        titled = page.evaluate("""() => [...document.querySelectorAll('#rotBody h3')].map(
            h => getComputedStyle(h).color)""")
        yellow = len(titled) == 2 and all(c == "rgb(255, 212, 0)" for c in titled)
        print(f"  {'✅' if yellow else '❌'} 兩個標題是黃橘色 {titled}")
        results.append(yellow)
        page.screenshot(path=SHOTS / "9-rotation.png")

        # 滑過 ETF 代號要跳出它的成份股。代號本身看不出裡面裝什麼
        page.hover("#rotBody .rot-row .etf")
        page.wait_for_selector("#etfTip .hrow", timeout=60_000)
        tip = page.evaluate("""() => {
            const rows = [...document.querySelectorAll('#etfTip .hrow')].map(
                r => [...r.children].map(c => c.innerText));
            const b = document.querySelector('#etfTip').getBoundingClientRect();
            return { rows, head: document.querySelector('#etfTip h4').innerText,
                     inside: b.right <= innerWidth && b.bottom <= innerHeight }; }""")
        good = (len(tip["rows"]) == 10 and tip["inside"]
                and all(len(r) == 3 and r[2].endswith("%") for r in tip["rows"]))
        print(f"  {'✅' if good else '❌'} 滑過 ETF 代號跳出成份股：{tip['head']}｜"
              f"{'、'.join(r[0] + ' ' + r[2] for r in tip['rows'][:3])}")
        results.append(good)
        page.mouse.move(0, 0)
        page.wait_for_function(
            "() => document.querySelector('#etfTip').classList.contains('hidden')", timeout=10_000)
        print("  ✅ 滑鼠移開就收起來")
        results.append(True)

        # 換區間必須真的重算，不是換個標題
        opts = page.locator("#rotRanges label").all_inner_texts()
        before = page.locator("#rotBody .rot-row .num").first.inner_text()
        page.click("#rotRanges label:has(input[value='252'])")
        page.wait_for_function(
            "t => { const n = document.querySelector('#rotBody .rot-row .num'); "
            "return n && n.innerText !== t; }", arg=before, timeout=60_000)
        after = page.locator("#rotBody .rot-row .num").first.inner_text()
        label = page.locator("#rotBody .meta").first.inner_text()
        switched = "1 年" in label and "252" in label
        print(f"  {'✅' if switched else '❌'} 區間選單 {opts}：21 天 {before} → 1 年 {after}（第一列都是 XLK）")
        print("     ", label)
        results.append(switched)
        # 換了區間，板塊的上下順序不准動。這是這次改動要守住的東西
        still = etf_order(page) == order
        print(f"  {'✅' if still else '❌'} 換區間後板塊順序沒有跳動")
        results.append(still)
        page.click("#rotClose")

        if errors:
            print("\n❌ 瀏覽器主控台錯誤:")
            for e in errors[:10]:
                print("   ", e[:200])
        else:
            print("\n✅ 瀏覽器主控台沒有任何錯誤")
        results.append(not errors)
        browser.close()

    print(f"\n截圖: {SHOTS}/1-report.png, {SHOTS}/2-calendar.png")
    return all(results)


if __name__ == "__main__":
    symbol = sys.argv[1] if len(sys.argv) > 1 else "TSLA"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 8123
    proc = serve(port)
    try:
        ok = run(symbol, port)
    finally:
        proc.terminate()
        proc.wait()
        print("已關閉測試用伺服器")
    sys.exit(0 if ok else 1)
