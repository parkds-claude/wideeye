"""전처리: URL 제거·인용 절단·공백 정리, 잡음(스티커·추임새)·자동 발송 메일 거르기."""
from __future__ import annotations
import re

from ..models import PLACEHOLDER_EMOTICON, Event, Source

URL = re.compile(r"https?://\S+")
SUBJECT_PREFIX = re.compile(r"^(?:\s*(?:re|fw|fwd|회신|답장|전달)\s*[:：]\s*)+", re.I)


def clean_text(text: str, rules) -> str:
    t = text or ""
    m = rules.quote_cut.search(t)
    if m and m.start() > 0:
        t = t[:m.start()]
    t = URL.sub(" ", t)
    return re.sub(r"\s+", " ", t).strip()


def is_noise(ev: Event, rules) -> bool:
    t = (ev.text or "").strip()
    if not t or t == PLACEHOLDER_EMOTICON:  # 빈 글·스티커. 첨부·미디어만 있는 것은 잡음이 아니다(도면·견적서 파일 한 장)
        return True
    return bool(rules.noise_only.match(t))


def is_auto(ev: Event, rules, replied_senders: set[str]) -> bool:
    """자동 발송·대량 메일. 단 내가 답한 적 있는 주소는 제외하지 않는다(발주처가 info@ 로 보내는 경우)."""
    if ev.source not in (Source.GMAIL, Source.NAVER):
        return False
    addr = (ev.sender_id or "").lower()
    if addr in replied_senders:
        return False
    if rules.auto_sender.search(addr):
        return True
    if "@" in addr and rules.junk_auto_domain.search(addr.split("@", 1)[1]):      # 알림 · 홍보용 하위 주소(2026-10-17)
        return True
    raw = ev.raw or {}
    if rules.auto_subject.search(raw.get("subject", "")):
        return True
    hdr = {k.lower(): v for k, v in (raw.get("headers") or {}).items()}
    if "list-unsubscribe" in hdr or "list-id" in hdr:
        return True
    if hdr.get("auto-submitted", "no").lower() != "no":
        return True
    if re.search(r"\b(bulk|list|junk|auto_reply)\b", hdr.get("precedence", ""), re.I):
        return True
    return False


def sms_auto(ev: Event, rules, replied_senders: set[str] = frozenset()) -> bool:
    """문자가 기업의 자동 발송인가(2026-10-17): 한국 대표번호 · 유선(휴대폰 · 070 이 아님)이나 앞부분이 숫자뿐인 주소(15880000@… — 카드 승인 ·
    기업 메시지)이고, 맥 연락처에도 없고(있으면 문자 수집기가 이름을 붙인다), 내가 그 번호에 문자를 보낸 적도 없다(replied_senders — 저장된
    최근 7일 안). 해외 번호(+82 가 아닌 +…)와 사람의 이메일 주소(Apple ID)는 자동 발송으로 보지 않는다(독립 검토 2026-10-17 — 해외에서 오는 사람의 연락)."""
    sid = (ev.sender_id or "").strip()
    if not sid or ev.is_from_me or sid.lower() in replied_senders or (ev.sender_name or "").strip() not in ("", sid):
        return False
    if "@" in sid:
        return sid.split("@", 1)[0].isdigit()
    if sid.startswith("+") and not sid.startswith("+82"):
        return False
    return not rules.junk_sms_person.match(re.sub(r"\D", "", sid))


def junk_reason(ev: Event, rules, replied_senders: set[str]) -> str:
    """스팸·광고·자동 발송이면 까닭(ad · bulk · notice · auto), 사람이 나에게 보낸 것이면 "".

    광고 표식과 Gmail 의 광고 분류는 누구에게서 왔든 뺀다. 그 밖(자동 알림·대량 발송·자동 발송 주소)은
    내가 메일을 보낸 적 있는 상대면 빼지 않는다 — Gmail 이 거래처 메일을 '업데이트'로 분류하거나 담당자가 info@ 로 보내는 경우."""
    raw = ev.raw or {}
    text = f"{raw.get('subject', '')} {ev.text or ''}"
    if rules.junk_ad_text.search(text):
        return "ad"
    if rules.junk_auto_text.search(text):
        return "auto"
    if ev.source == Source.IMESSAGE and not ev.is_group and sms_auto(ev, rules, replied_senders):
        return "auto"
    if ev.source not in (Source.GMAIL, Source.NAVER):
        return ""
    reasons = [rules.junk_labels[x] for x in (raw.get("labels") or []) if x in rules.junk_labels]
    if "ad" in reasons:
        return "ad"
    if (ev.sender_id or "").lower() in replied_senders:
        return ""
    if reasons:
        return reasons[0]
    hdr = {k.lower(): v for k, v in (raw.get("headers") or {}).items()}
    if "list-unsubscribe" in hdr or "list-id" in hdr or re.search(r"\b(bulk|list|junk)\b", hdr.get("precedence", ""), re.I):
        return "bulk"
    return "auto" if is_auto(ev, rules, replied_senders) else ""


def norm_subject(subject: str) -> str:
    """Re:/Fw: 머리말을 떼고 소문자로 — 같은 메일이 여러 계정에 온 것·같은 대화를 하나로 묶는 데 쓴다."""
    return re.sub(r"\s+", " ", SUBJECT_PREFIX.sub("", subject or "")).strip().lower()
