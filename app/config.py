"""業務邏輯設定。環境相關的敏感資訊放 .env，這裡只放可進版控的規則。"""

MOVE_THRESHOLD_PCT = 3.0

# 美股 16:00 ET 收盤 ≈ 20:00 UTC。之後發布的新聞歸屬到下一個日曆日。
MARKET_CLOSE_UTC_HOUR = 20

CACHE_TTL_SECONDS = 15 * 60

# 新聞搜尋天數的範圍，前端是一根拉桿。上限 14 是因為 Finnhub 免費 tier 本來就只回約兩週。
MIN_NEWS_DAYS = 1
MAX_NEWS_DAYS = 14
# 抓幾天的新聞。畫面上不給選，就是這個數字 —— 要改就改這裡
DEFAULT_NEWS_DAYS = 10

# 走勢圖的時間軸，跟「新聞抓幾天」是兩回事，各走各的 API。
# key = 前端傳的值，value = (中文標籤, yfinance period, yfinance interval)
CHART_RANGES = {
    "1D": ("1 天", "1d", "5m"),
    "5D": ("5 天", "5d", "30m"),
    "1M": ("1 個月", "1mo", "1d"),
    "6M": ("6 個月", "6mo", "1d"),
    "YTD": ("年初至今", "ytd", "1d"),
    "1Y": ("1 年", "1y", "1d"),
    "5Y": ("5 年", "5y", "1wk"),
}
# 一打開先看今天。走勢圖、對照卡、恐慌指數都吃這一個值，不准各自寫死
DEFAULT_CHART_RANGE = "1D"

# VIX 的盤後時段開得比個股早。看「當日」時只抓一天，會變成 VIX 已經有隔夜資料、
# 個股還停在上一個交易日，兩邊完全沒有重疊。所以 VIX 那條背景線多抓幾天。
VIX_PERIOD_OVERRIDE = {"1D": "5d"}

HTTP_TIMEOUT_SECONDS = 20.0

# 標題情緒判斷用。只看關鍵字，不做語意分析 —— 會誤判，所以 UI 必須標示「機器猜的」。
POSITIVE_KEYWORDS = (
    "beat", "beats", "surge", "surges", "soar", "soars", "jump", "jumps", "rally",
    "rallies", "gain", "gains", "rise", "rises", "record high", "upgrade", "upgraded",
    "raises", "raised", "outperform", "buy rating", "strong", "growth", "profit",
    "wins", "win", "awarded", "expands", "approval", "approved", "partnership", "bullish",
)

NEGATIVE_KEYWORDS = (
    "miss", "misses", "plunge", "plunges", "sink", "sinks", "tumble", "tumbles",
    "slump", "slumps", "drop", "drops", "fall", "falls", "slide", "slides",
    "downgrade", "downgraded", "cuts", "cut", "lowered", "underperform", "sell rating",
    "weak", "loss", "losses", "lawsuit", "sues", "probe", "investigation", "recall",
    "layoff", "layoffs", "warning", "warns", "halt", "delay", "delays", "bearish",
    "short seller", "fraud", "decline", "declines",
)

# yfinance 的 sector 就是 TradingView Heatmap 分組用的那套 GICS 大類，直接對照成中文。
SECTOR_LABELS = {
    "Financial Services": "金融",
    "Technology": "科技",
    "Healthcare": "醫療保健",
    "Consumer Cyclical": "非必需消費",
    "Consumer Defensive": "必需消費",
    "Energy": "能源",
    "Industrials": "工業",
    "Basic Materials": "原物料",
    "Utilities": "公用事業",
    "Real Estate": "房地產",
    "Communication Services": "通訊服務",
}

# 行事曆：只放「有固定規則、算得出來」的日子。規則寫在 app/calendar.py:_RULES。
# scope = "all" 代表全市場都受影響；填產業名則只有該產業標紅。
CALENDAR_RULES = (
    {"rule": "first-friday", "name": "非農就業報告", "scope": "all",
     "note": "每月第一個星期五 08:30 ET，利率預期的主要變數"},
    {"rule": "weekly-wed", "name": "EIA 原油庫存週報", "scope": "Energy",
     "note": "每週三 10:30 ET，油價當天常大動"},
    {"rule": "third-friday", "name": "選擇權到期日", "scope": "all",
     "note": "每月第三個星期五，尾盤成交量與波動會放大"},
)

# FOMC 沒有免費 API（Finnhub 的經濟行事曆是付費的），也沒有規則可以推算。
# 要用就自己去 federalreserve.gov/monetarypolicy/fomccalendars.htm 抄回來填這裡。
# 空的時候 UI 會誠實說「沒有資料」，不會假裝有。
FOMC_DATES = ()

# 異動日要跟誰比：大盤一律用 SPY，產業用同一套 GICS 分類對應的 SPDR 類股 ETF。
BENCHMARK_SYMBOL = "SPY"
# 這個順序就是「哪個板塊在贏大盤」畫面上由上到下的順序，切換天數不會重排 ——
# 每換一個區間就跳一次位置，根本追不到自己在看的那一條。要比大小看長條，不是看名次。
SECTOR_ETF = {
    "Technology": "XLK",
    "Communication Services": "XLC",
    "Energy": "XLE",
    "Healthcare": "XLV",
    "Consumer Defensive": "XLP",
    "Industrials": "XLI",
    "Consumer Cyclical": "XLY",
    "Financial Services": "XLF",
    "Basic Materials": "XLB",
    "Real Estate": "XLRE",
    "Utilities": "XLU",
}

# 個股扣掉產業之後還剩多少，才算「真的是這家公司自己的事」。
ALPHA_NOISE_PCT = 1.5

# 每個產業「股價通常被什麼推著走」。這是產業通則，不是個股分析 —— UI 必須標示清楚。
SECTOR_DRIVERS = {
    "Financial Services": (
        "利率（升息通常讓銀行賺更多利差，但保險公司的債券部位會虧）",
        "信用循環（景氣差 → 呆帳、理賠增加）",
        "重大天災（產險公司的理賠直接吃掉獲利）",
        "金融監管與資本要求的變動",
    ),
    "Technology": (
        "利率（成長股的估值對利率最敏感，升息就殺估值）",
        "企業 IT 與雲端支出的景氣循環",
        "半導體供需與晶片價格",
        "出口管制、關稅等地緣政治限制",
    ),
    "Healthcare": (
        "藥證審查結果（FDA 過或不過，股價直接跳）",
        "專利到期與學名藥競爭",
        "醫保給付政策與藥價管制",
        "臨床試驗數據公布",
    ),
    "Consumer Cyclical": (
        "消費者信心與就業數據",
        "通膨侵蝕可支配所得",
        "利率（車貸、房貸、分期付款的成本）",
        "油價影響的運費與旅遊需求",
    ),
    "Consumer Defensive": (
        "原物料與農產品成本",
        "漲價能力（能不能把成本轉嫁給消費者）",
        "美元匯率（海外營收換回美元會縮水）",
        "景氣衰退時反而抗跌，是資金的避風港",
    ),
    "Energy": (
        "原油與天然氣價格（幾乎是決定性因素）",
        "OPEC+ 的減產或增產決議",
        "地緣政治衝突造成的供給中斷",
        "再生能源替代與碳排政策",
    ),
    "Industrials": (
        "製造業 PMI 與資本支出循環",
        "基礎建設與國防預算",
        "運費、鋼鐵等投入成本",
        "關稅與供應鏈重組",
    ),
    "Basic Materials": (
        "金屬與化工原料的現貨價格",
        "中國需求（全球最大原物料買家）",
        "美元匯率（原物料以美元計價，美元強則價跌）",
        "環保法規與採礦許可",
    ),
    "Utilities": (
        "利率（高股息股跟公債搶資金，升息就被賣）",
        "費率調整的政府核准結果",
        "燃料成本（天然氣、煤）",
        "極端氣候造成的用電量與設備損害",
    ),
    "Real Estate": (
        "利率與房貸利率（最直接的變數）",
        "空置率與租金成長",
        "遠距辦公造成的辦公室需求結構改變",
        "REITs 的股息殖利率 vs 公債殖利率",
    ),
    "Communication Services": (
        "廣告支出的景氣循環",
        "使用者成長與訂閱數",
        "反壟斷與資料隱私法規",
        "內容製作成本與版權競爭",
    ),
}

# FRED 總體經濟指標。id → (中文名, 單位, 算法)。
# 算法 "yoy" 只給「指數型」的月頻序列用 —— 指數的絕對值沒有意義，年增率才有。
FRED_SERIES = {
    "FEDFUNDS": ("聯邦基金利率", "%", "level"),
    "DGS10": ("10 年期公債殖利率", "%", "level"),
    "UNRATE": ("失業率", "%", "level"),
    "CPIAUCSL": ("CPI 通膨年增率", "%", "yoy"),
    "PPIACO": ("生產者物價年增率", "%", "yoy"),
    "INDPRO": ("工業生產年增率", "%", "yoy"),
    "RSAFS": ("零售銷售年增率", "%", "yoy"),
    "UMCSENT": ("消費者信心指數", "點", "level"),
    "MORTGAGE30US": ("30 年房貸利率", "%", "level"),
    "HOUST": ("新屋開工", "千戶", "level"),
    "DCOILWTICO": ("WTI 原油價格", "美元", "level"),
    "DTWEXBGS": ("美元指數", "點", "level"),
    "DRCCLACBS": ("信用卡壞帳率", "%", "level"),
}

# 指標動了，對公司是好還是壞。id → (往上算好(+1)還是壞(-1), 往上的意思, 往下的意思)。
# 這是通則版，適用於沒有特別註明的產業。
FRED_EFFECT = {
    "FEDFUNDS": (-1, "借錢變貴，公司擴張和消費者花錢都會縮手", "借錢變便宜，錢比較敢動"),
    "DGS10": (-1, "長天期利率高，未來的獲利折現後變不值錢，估值被壓", "長天期利率降，同樣的獲利可以撐起更高的估值"),
    "UNRATE": (-1, "失業變多，消費會先縮，壞帳會後到", "工作好找，消費撐得住"),
    "CPIAUCSL": (-1, "物價漲得快，成本上升而且逼得央行不敢降息", "通膨降溫，央行的手鬆開了"),
    "PPIACO": (-1, "原料和上游成本變貴，利潤率被吃掉", "上游成本降，利潤率有空間"),
    "INDPRO": (1, "工廠在增產，實體需求是真的", "工廠在減產，實體需求在退"),
    "RSAFS": (1, "民眾真的在花錢", "民眾把錢收起來了"),
    "UMCSENT": (1, "消費者對未來有信心，願意花大錢", "消費者變保守，先砍非必需的開銷"),
    "MORTGAGE30US": (-1, "房貸變貴，買房和裝修相關的需求會被壓下去", "房貸變便宜，房市相關需求回溫"),
    "HOUST": (1, "新房子開工變多，建材、家電、房貸都跟著有生意", "開工變少，整條房市鏈的生意都縮"),
    "DCOILWTICO": (-1, "油價高＝運輸和原料成本高，多數公司的成本被推上去", "油價降，成本壓力鬆開"),
    "DTWEXBGS": (-1, "美元強，海外賺的錢換回美元會縮水，出口也變難賣", "美元弱，海外收入換回來變多"),
    "DRCCLACBS": (-1, "還不出卡債的人變多，銀行要多提列損失", "壞帳在降，放款品質變好"),
}

# 少數指標對特定產業是反的 —— 升息壓垮多數公司，但銀行保險就是靠利差賺錢。
FRED_EFFECT_BY_SECTOR = {
    "Financial Services": {
        "FEDFUNDS": (1, "銀行保險靠利差賺錢，利率高＝同一筆錢賺更多", "利差被壓縮，存放款和保費投資的收益都變薄"),
        "DGS10": (1, "保險公司手上一堆債券，新錢買到的利息更高", "新買的債券利息變少，長期獲利被壓"),
    },
    "Energy": {
        "DCOILWTICO": (1, "油價就是這個產業的售價，漲價直接變利潤", "售價下跌，利潤直接跟著縮"),
    },
    "Basic Materials": {
        "PPIACO": (1, "原物料就是他們的產品，漲價等於漲售價", "產品價格在跌，利潤跟著縮"),
    },
}

# 每個產業看哪幾個指標，順序就是相關度：排第一的最貼近該產業的獲利。
SECTOR_FRED = {
    "Financial Services": ("FEDFUNDS", "DGS10", "DRCCLACBS", "UNRATE"),
    "Technology": ("DGS10", "FEDFUNDS", "INDPRO"),
    "Healthcare": ("CPIAUCSL", "UNRATE", "FEDFUNDS"),
    "Consumer Cyclical": ("UMCSENT", "RSAFS", "MORTGAGE30US", "CPIAUCSL"),
    "Consumer Defensive": ("CPIAUCSL", "PPIACO", "DTWEXBGS"),
    "Energy": ("DCOILWTICO", "INDPRO", "DTWEXBGS"),
    "Industrials": ("INDPRO", "PPIACO", "FEDFUNDS"),
    "Basic Materials": ("PPIACO", "DTWEXBGS", "INDPRO"),
    "Utilities": ("DGS10", "FEDFUNDS", "CPIAUCSL"),
    "Real Estate": ("MORTGAGE30US", "DGS10", "HOUST"),
    "Communication Services": ("UMCSENT", "RSAFS", "FEDFUNDS"),
}

# 產業不明時的預設：全市場都在看的三個。
DEFAULT_FRED = ("FEDFUNDS", "CPIAUCSL", "UNRATE")

# 指標要往回看多久：走勢圖的長度，以及「最近變化」的比較基準。
FRED_HISTORY_DAYS = 365 * 3
FRED_CHANGE_DAYS = 182
FRED_SPARK_POINTS = 36

ANALYST_KEYWORDS = (
    "price target",
    "target price",
    "upgrade",
    "downgrade",
    "initiated coverage",
    "analyst",
    "rating",
    "outperform",
    "overweight",
    "underweight",
    "reiterate",
)


# ── 注意力溫度計（每日新聞則數）─────────────────────────────────
# 必須一天一個請求：Finnhub 單次回應約 250 筆封頂，用週為單位抓，熱門股的尖峰日會被截掉。
ATTENTION_DAYS = 14
ATTENTION_SPIKE_RATIO = 2.0

# ── 恐慌指數 ──────────────────────────────────────────────────
# CBOE 波動率指數。yfinance 的代號有 ^ 開頭，跟一般股票代號不同格式。
VIX_SYMBOL = "^VIX"

# ── 估值 ───────────────────────────────────────────────────────
VALUATION_YEARS = 5
# 拉桿可以選的回看年數。價格一次抓滿 VALUATION_YEARS，換區間只是在同一份資料上裁切。
VALUATION_MIN_YEARS = 1
VALUATION_MAX_YEARS = VALUATION_YEARS
VALUATION_SPARK_POINTS = 60
# 本益比 = 股價 ÷ 盈餘。分數變小有兩種原因：股價跌（真的便宜），或盈餘暴衝（不一定）。
# 盈餘跑得比股價快很多的時候，「落在第幾 %」量到的是盈餘成長速度，不是貴或便宜。
# 兩個門檻都跨過才提醒：盈餘漲幅本身要夠大，而且要明顯快過股價。
PE_TRAP_EARNINGS_GROWTH = 100.0
PE_TRAP_LEAD = 1.5
# 往回抓幾筆財報公布日。要蓋滿 VALUATION_YEARS 還得多留幾季給滾動盈餘暖身。
EARNINGS_DATES_LIMIT = 80
# 「這個盈餘撐不撐得住」的四項檢查，容許多少雜訊才算真的有變化。
# 財報數字本來就會季季小幅晃動，門檻太低會把雜訊讀成趨勢。
QUALITY_GROWTH_FLAT = 2.0
QUALITY_MARGIN_FLAT = 0.5
# key → (要問的問題, 往上的意思, 往下的意思, 沒動的意思, 單位, 多少以內算沒動)
# 本益比低到底是便宜還是陷阱，差別只在「分母撐不撐得住」。這四題就是在問分母。
QUALITY_CHECKS = {
    "growth": ("盈餘還在往上嗎？",
               "近一年賺的錢比前一年多，分母還在長大",
               "近一年賺的錢比前一年少 —— 分母在縮，本益比會自己彈回去",
               "近一年跟前一年幾乎一樣，盈餘停在原地", "%", QUALITY_GROWTH_FLAT),
    "accel": ("成長在加速還是減速？",
              "成長率比上一季更快，動能還在加強",
              "成長率比上一季慢 —— 盈餘到頂之前，通常先減速再轉負",
              "成長率跟上一季差不多，維持同一個速度", "個百分點", QUALITY_GROWTH_FLAT),
    "revenue": ("營收還在成長嗎？",
                "東西真的賣得更多，盈餘背後有生意撐著",
                "營收在縮 —— 那盈餘是省出來的，同一筆成本省不了第二次",
                "營收原地踏步，接下來只能靠省成本", "%", QUALITY_GROWTH_FLAT),
    "margin": ("賺回來的錢留得住嗎？",
               "淨利率變高，每賣一塊錢留下來的比去年多",
               "淨利率被壓縮，成本或競爭正在吃掉利潤",
               "淨利率跟去年差不多", "個百分點", QUALITY_MARGIN_FLAT),
}

# ── 股價軌道（合理區間帶）─────────────────────────────────────
# 每次財報公布日前後各取一段股價，當這一季軌道的錨點。
TREND_BEFORE_DAYS = 7
TREND_AFTER_DAYS = 21
# 帶寬取最近幾季的窗口均價來算。太少會被單一季的意外撐爆。
TREND_SIGMA_QUARTERS = 5
# 盈餘成長對軌道斜率的影響力。0 就是純看股價慣性，1 就是跟股價趨勢等重。
TREND_EPS_WEIGHT = 0.1
# 最後一段要畫到下一次財報公布日。Yahoo 沒有排定日期時才用這個天數估（一季＋公布落差）。
TREND_NEXT_DAYS = 106

# ── 公司回購 ──────────────────────────────────────────────────
# Yahoo 現金流量表裡的欄位名。負數代表現金流出，也就是花錢買回自己的股票。
BUYBACK_ROW = "Repurchase Of Capital Stock"
BUYBACK_YEARS = 5
# 回購殖利率 = 近四季回購金額 ÷ 目前市值。跟配息殖利率同一個尺度，可以直接比。
# 股數年減多少才算「真的在縮股本」。低於這個數就只是在抵銷發給員工的股票。
BUYBACK_REAL_SHRINK = 1.0
# 算配息殖利率要抓幾筆歷史配息。只要夠推出「一年配幾次」就好 —— 用最近一筆乘上次數，
# 而不是把過去一年加總：Yahoo 的配息序列偶爾缺一筆，加總會直接少報一整季。
DIVIDEND_SAMPLE = 6

# ── 資金流向：錢正在從哪個板塊搬到哪個板塊 ─────────────────────
# 相對強度 = 板塊價格 ÷ 大盤價格。整袋錢變大變小會被分子分母一起吃掉，
# 剩下的才是「錢在板塊之間搬家」。看漲跌幅沒有用 —— 整袋變大的時候大家都漲。
# 最長要看一年（252 個交易日），一年的日曆天不夠 —— 週末假日會把可用的交易日吃掉約三成
ROTATION_HISTORY_DAYS = 520
ROTATION_WINDOWS = (("1月", 21), ("3月", 63), ("6月", 126), ("12月", 252))
# 相對強度要看幾天的變化，才算「正在變強／變弱」。使用者可以自己挑。
ROTATION_RANGES = (
    ("5 天", 5), ("半個月", 10), ("1 個月", 21),
    ("3 個月", 63), ("半年", 126), ("1 年", 252),
)
ROTATION_SLOPE_DAYS = 21
# 相對強度變動小於這個 %，是雜訊，不標方向
ROTATION_NOISE_PCT = 1.0
# 只有這麼少的板塊在贏大盤，就是「錢集中在少數地方」的盤，輪動邏輯會失效
ROTATION_NARROW_MAX = 3

# 風險胃納：比值上升＝錢願意冒險，下降＝錢在躲。
# 三個要一起看 —— 它們互相打架的時候，那個矛盾本身就是訊息。
RISK_RATIOS = (
    {"up": "XLY", "down": "XLP", "name": "想買名牌 vs 只買衛生紙",
     "on": "大家敢花錢買非必需品", "off": "大家縮回去只買生活必需品"},
    {"up": "XLK", "down": "XLU", "name": "科技 vs 電力公司",
     "on": "錢在追成長", "off": "錢躲進穩定配息的公用事業"},
    {"up": "HYG", "down": "TLT", "name": "垃圾債 vs 政府公債",
     "on": "債市願意借錢給體質差的公司", "off": "債市在逃回最安全的政府公債"},
    {"up": "SPHB", "down": "SPLV", "name": "大波動股 vs 牛皮股",
     "on": "錢願意抱住上下亂跳的股票", "off": "錢換去抱不太會動的股票"},
    {"up": "IBIT", "down": "GLD", "name": "比特幣 vs 黃金",
     "on": "不信任現金的錢跑去最投機的地方", "off": "不信任現金的錢躲回黃金"},
)
RISK_EXTRA_SYMBOLS = ("HYG", "TLT", "SPHB", "SPLV", "IBIT", "GLD")
