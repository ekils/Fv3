import pytest

from app.ask import _quarter, _text, brief_prompt, facts, turns
from app.config import (ASK_BAD, ASK_BRIEF_SECTIONS, ASK_GOOD, ASK_HIGHLIGHTS,
                        ASK_SESSION_ROUNDS, ASK_WATCH)

ALL_KEYS = ASK_GOOD + ASK_BAD + ASK_WATCH

PROFILE = {"name": "Alphabet Inc", "industry": "Media"}
SURPRISES = [
    {"period": "2026-06-30", "year": 2026, "quarter": 2, "actual": 2.85,
     "estimate": 2.98, "surprise": -0.13, "surprise_pct": -4.36},
    {"period": "2026-03-31", "year": 2026, "quarter": 1, "actual": 2.62,
     "estimate": 2.71, "surprise": -0.09, "surprise_pct": -3.15},
]


def msg(text, notes=()):
    return {"output": [{"type": "reasoning"}, {"type": "web_search_call"},
                       {"type": "message",
                        "content": [{"text": text, "annotations": list(notes)}]}]}


def test_the_facts_block_carries_the_numbers_the_model_must_not_invent():
    """摘要的地基。這段沒帶到的東西，模型就只能靠搜尋或記憶 —— 那正是會出錯的地方。"""
    text = facts("GOOGL", PROFILE, SURPRISES, [{"date": "2026-10-27"}])
    for must in ("GOOGL", "Alphabet Inc", "Media", "2.85", "2.98", "2026-10-27"):
        assert must in text
    assert "落後 4.36%" in text


def test_a_company_with_no_earnings_history_still_gets_a_facts_block():
    """新上市的公司沒有財報歷史。那不是錯誤，是「這一段先空著」。"""
    text = facts("NEWCO", {"name": "New Co"}, [], [])
    assert "NEWCO" in text and "每股盈餘" not in text


def test_the_search_keyword_points_at_the_quarter_that_already_happened():
    # 拿還沒公布的那一季去搜，搜到的是預測文章不是逐字稿
    assert _quarter(SURPRISES) == "Q2 2026"
    assert _quarter([]) == "latest"


def test_the_brief_asks_for_every_section_we_promised():
    """設定裡加了一節、prompt 卻沒問，畫面上就會靜默少一段。"""
    p = brief_prompt("GOOGL", "Alphabet Inc", "Q2 2026")
    assert "GOOGL Q2 2026 earnings call transcript" in p
    for title, _ in ASK_BRIEF_SECTIONS:
        assert f"### {title}" in p


def test_the_facts_are_sent_once_not_stapled_to_every_question():
    """事實只當開場白送一次。每句都貼一份的話，十輪下來同一段會被重送十次。"""
    t = turns("我是事實", [], "問題一")
    assert t[0]["content"].endswith("我是事實")
    assert t[-1] == {"role": "user", "content": "問題一"}
    assert len(t) == 3


def test_only_the_last_ten_rounds_survive():
    """記憶要有上限，不然對話越長越慢越貴，而且模型會被十輪前的話題黏住。"""
    history = [{"role": r, "content": f"{r}{i}"}
               for i in range(30) for r in ("user", "assistant")]
    t = turns("事實", history, "最新問題")
    assert len(t) == 2 + ASK_SESSION_ROUNDS * 2 + 1
    assert t[-1]["content"] == "最新問題"
    assert t[2]["content"] == history[-ASK_SESSION_ROUNDS * 2]["content"]


def test_the_answer_is_pulled_out_of_the_noise_around_it():
    """output 裡混著 reasoning 跟 web_search_call。只有 message 是要給人看的。"""
    answer, cites = _text(msg("講完了", [{"url": "https://a.com", "title": "A"}]))
    assert answer == "講完了"
    assert cites == [{"title": "A", "url": "https://a.com"}]


def test_the_same_source_cited_five_times_is_listed_once():
    _, cites = _text(msg("x", [{"url": "https://a.com", "title": "A"}] * 5))
    assert len(cites) == 1


def test_the_highlight_list_has_no_duplicates():
    """同一個詞列兩次，正規表示式就會多一個分支 —— 標出來的結果一樣，但清單會越養越亂。"""
    assert len(ALL_KEYS) == len(set(ALL_KEYS))


def test_no_word_is_green_and_red_at_the_same_time():
    """同一個詞掛在兩組裡，顏色就變成「誰先被寫進那張表誰贏」——
    那是檔案裡的排列順序在決定畫面，不是規則在決定畫面。"""
    for a, b in (("好", ASK_GOOD), ("壞", ASK_BAD)):
        assert not (set(b) & set(ASK_WATCH)), f"{a}消息不能同時是中性"
    assert not (set(ASK_GOOD) & set(ASK_BAD))


def test_the_three_groups_are_what_the_browser_gets():
    """前端拿 key 直接當 CSS class 用。鍵名改了畫面就會整片變黑，而且不會報錯。"""
    assert ASK_HIGHLIGHTS == {"good": ASK_GOOD, "bad": ASK_BAD, "watch": ASK_WATCH}


def test_every_forced_tone_word_is_coloured_and_pointing_the_right_way():
    """第一節強制模型四選一，那四個答案就是整份摘要最該一眼看到的字。
    而且方向不能標反 —— 「明顯轉壞」標成綠色比不標色還糟。"""
    for word in ("明顯樂觀", "謹慎樂觀"):
        assert word in ASK_GOOD
    for word in ("保守", "明顯轉壞"):
        assert word in ASK_BAD


def test_a_longer_keyword_is_never_eaten_by_a_shorter_one_inside_it():
    """「樂觀」是「明顯樂觀」的一部分。前端照長度排序來擋這件事，
    這裡確認清單裡真的存在這種包含關係 —— 存在，所以那個排序不能拿掉。"""
    nested = [(a, b) for a in ALL_KEYS for b in ALL_KEYS if a != b and a in b]
    assert nested, "沒有互相包含的詞了，前端那段長度排序就可以刪掉"


def test_words_that_appear_in_every_sentence_are_kept_out_of_the_list():
    """「年增」每句都有，而且本身不帶方向 —— 標了只會把整段染紅。
    全部都紅等於全部都不紅，這條測試就是擋「清單越加越長」。"""
    for noise in ("年增", "年減", "營收", "公司", "成長"):
        assert noise not in ALL_KEYS


def test_an_empty_answer_raises_instead_of_showing_a_blank_card():
    """空白的回答比錯誤訊息更難查 —— 使用者只會看到一張空卡片，不知道發生什麼事。"""
    with pytest.raises(RuntimeError):
        _text({"status": "incomplete", "output": [{"type": "reasoning"}]})
