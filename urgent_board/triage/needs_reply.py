"""답이 필요한가: 질문 어미·의문사·요청 표현(한국어/영어). 걸린 까닭 목록을 돌려준다(빈 목록 = 아님).
그 밖의 말로 쓴 글은 규칙이 못 읽으니 '질문 · 요청일 수 있음'(foreign)으로 돌려준다 — 할 일인지는 Claude 가 가린다(2026-10-17, rules.yaml foreign)."""
from __future__ import annotations
import re

from .preprocess import clean_text

SENTENCE_END = re.compile(r"(?<=[.!?？])\s+")      # 문장 나누기 — 질문이 메시지 맨 끝이 아닐 수 있다
HANGUL = re.compile(r"[가-힣ㄱ-ㅎㅏ-ㅣ]")
LATIN = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿĀ-ž]")
# 라틴 · 한글이 아닌 글자: 그리스 · 키릴 · 히브리 · 아랍 · 데바나가리 · 타이 · 가나 · 한자 · 아랍 표현형
OTHER = re.compile(r"[\u0370-\u03FF\u0400-\u052F\u0590-\u05FF\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\u0900-\u097F\u0E00-\u0E7F\u3040-\u30FF\u3400-\u4DBF\u4E00-\u9FFF\uFB50-\uFDFF\uFE70-\uFEFF]")
WORD = re.compile(r"[A-Za-z']+")


def is_foreign(text: str, rules) -> bool:
    """한국어도 영어도 아닌 글인가(rules.foreign 의 기준). 기준이 없으면 늘 아니다."""
    f = getattr(rules, "foreign", None) or {}
    if not f:
        return False
    h, o, lat = len(HANGUL.findall(text)), len(OTHER.findall(text)), len(LATIN.findall(text))
    letters = h + o + lat
    if letters < f.get("min_letters", 8) or h >= letters * f.get("hangul_share", 0.3):
        return False
    if o >= letters * f.get("other_script_share", 0.3):
        return True
    words = [w.lower() for w in WORD.findall(text)]
    if len(words) < f.get("min_words", 4):
        return False
    english = sum(1 for w in words if w in rules.foreign_en_words)
    return english < len(words) * f.get("english_share_min", 0.12)


def needs_reply(text: str, rules) -> list[str]:
    t = rules.courtesy.sub(" ", clean_text(text, rules)).strip()      # 인사말의 '부탁'은 요청이 아니다
    if not t:
        return []
    why: list[str] = []

    def add(reason: str) -> None:
        if reason not in why:
            why.append(reason)

    # 문장 끝을 보는 규칙은 문장마다("…할까요? 좌석이 오늘 풀립니다")
    for s in SENTENCE_END.split(t):
        if rules.q_end_ko.search(s):
            # '죠' 단독(동의)은 약한 신호
            add("q_end_soft" if s.rstrip("?？ ").endswith("죠") and "?" not in s and "？" not in s else "q_end")
        if rules.q_end_short_ko.search(s):
            add("q_end_short")
        if rules.q_en.search(s):
            add("q_en")
    if rules.q_wh_ko.search(t) and "?" in t:
        add("q_wh")
    if rules.q_indirect_ko.search(t):
        add("q_indirect")
    if rules.req_ko.search(t):
        add("request")
    if rules.req_en.search(t):
        add("request_en")
    if is_foreign(t, rules):
        add("foreign")
    order = ["q_end", "q_end_soft", "q_end_short", "q_wh", "q_indirect", "request", "q_en", "request_en", "foreign"]
    return sorted(why, key=order.index)
