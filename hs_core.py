"""Stdlib-only bounded helpers; no document storage, network, parsing or billing.
Upload magic is preflight, not full parser validation. Enforce MAX_PAGES in parser.
Source metadata does not independently prove authority, freshness or legal effect.
"""
from __future__ import annotations

import difflib
import hashlib
import html
import math
import re
import threading
import time
from collections.abc import Callable, MutableMapping
from datetime import date
from urllib.parse import urlsplit

MAX_BYTES = 10 * 1024 * 1024
MAX_PAGES = 12
MAX_CHARS = 35000
PII_NOTICE = "개인정보 가림은 정규식 기반 보조 기능입니다. OCR 오류, 비표준 표기, 문맥 속 이름·주소·계좌번호 등은 남을 수 있습니다. 원문과 가림 결과를 직접 확인하고 필요한 개인정보를 추가로 삭제한 후 전송하세요."
DISCLAIMERS = {
    "한국어": "본 결과는 법률 자문이 아닌 참고용 AI 분석입니다. 정확성과 최신성을 보장하지 않으며, 계약 전 원문과 공식 자료를 확인하고 필요한 경우 전문가에게 상담하세요.",
    "English": "This is an AI analysis for reference, not legal advice. Accuracy and currency are not guaranteed. Check the original contract and official sources, and consult a qualified professional when needed before signing.",
    "日本語": "本結果は参考用のAI分析であり、法律上の助言ではありません。正確性や最新性は保証されません。契約前に原文と公的資料を確認し、必要に応じて専門家に相談してください。",
    "中文": "本结果为参考性AI分析，不构成法律意见，不保证准确性或时效性。签约前请核对合同原文和官方资料，必要时咨询专业人士。",
}
_ALIASES = {"ko": "한국어", "KO": "한국어", "Korean": "한국어", "en": "English", "EN": "English", "영어": "English", "ja": "日本語", "JA": "日本語", "일본어": "日本語", "zh": "中文", "ZH": "中文", "중국어": "中文"}


def _language(language: str) -> str:
    if not isinstance(language, str):
        raise ValueError("지원하는 언어를 선택해 주세요.")
    result = _ALIASES.get(language, language)
    if result not in DISCLAIMERS:
        raise ValueError("지원하는 언어를 선택해 주세요.")
    return result


def disclaimer(language: str = "한국어") -> str:
    return DISCLAIMERS[_language(language)]


def _text(text: str) -> str:
    if not isinstance(text, str):
        raise ValueError("텍스트 형식이 올바르지 않습니다.")
    if len(text) > MAX_CHARS:
        raise ValueError("텍스트가 35,000자를 초과합니다. 문서를 나누어 다시 시도해 주세요.")
    return text


def validate_upload(name: str, data: bytes) -> str:
    if not isinstance(name, str) or not name or len(name) > 255 or any(c in name for c in "\x00\r\n"):
        raise ValueError("파일 이름이 올바르지 않습니다.")
    if not isinstance(data, bytes) or not data:
        raise ValueError("빈 파일이거나 파일 형식이 올바르지 않습니다.")
    if len(data) > MAX_BYTES:
        raise ValueError("파일이 10MB를 초과합니다. 더 작은 파일을 올려 주세요.")
    extension = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    kind = {"pdf": "pdf", "png": "png", "jpg": "jpg", "jpeg": "jpg"}.get(extension)
    if kind is None:
        raise ValueError("PDF, PNG, JPG 파일만 지원합니다.")
    good = {
        "pdf": bool(re.match(br"%PDF-(?:1\.[0-7]|2\.0)(?:\r|\n|\s)", data)),
        "png": data.startswith(b"\x89PNG\r\n\x1a\n"),
        "jpg": len(data) >= 4 and data.startswith(b"\xff\xd8\xff") and data[3] not in (0, 255),
    }[kind]
    if not good:
        raise ValueError("확장자와 파일 내용이 일치하지 않거나 손상된 파일입니다.")
    return kind


_ID = re.compile(r"(?<!\d)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])[ -]?[1-8]\d{6}(?!\d)")
_PHONE = re.compile(r"(?<![\w+])(?:\+82[ .-]*(?:\(0\)[ .-]*)?(?:0?1[016789]|0?2|0?[3-6][1-5]|0?70)[ .-]*\d{3,4}[ .-]*\d{4}|0(?:1[016789]|2|[3-6][1-5]|70)[ .-]*\d{3,4}[ .-]*\d{4})(?!\d)")
_EMAIL = re.compile(r"(?<![\w.+-])[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)+")
_ACCOUNT = re.compile(r"((?:계좌\s*번호|입금\s*계좌|은행\s*계좌|계좌)(?:\s*[:：=]\s*|\s+)(?:[가-힣A-Za-z]{1,20}\s+)?)([0-9][0-9 -]{4,32}[0-9])(?!\d)")
_NAMES = re.compile(r"(?<![가-힣A-Za-z])((?:임대인|임차인)(?:\s*(?:성명|이름))?|성명|이름)(\s*[:：=]\s*|[ \t]+|\s*\(\s*)([가-힣]{2,5}(?![가-힣])|[A-Z][a-zA-Z'-]{1,24}(?:[ \t]+[A-Z][a-zA-Z'-]{1,24}){0,3})")
_NOT_NAMES = frozenset("보증금 월세 관리비 계약 계약기간 주소 연락처 입금 확인 정보 반환 임차인 임대인 주식회사 대표 동의 책임 부담 서명 의무 수선 수리 계좌 요구 납부 해지 특약 성명 이름 삭제".split())


def redact_pii(text: str) -> str:
    """Label-based names/accounts; preserve unlabelled dates, money and addresses."""
    text = _text(text)
    text = _ID.sub("[신분번호 삭제]", text)
    text = _PHONE.sub("[전화번호 삭제]", text)
    text = _EMAIL.sub("[이메일 삭제]", text)

    def account(match: re.Match) -> str:
        suffix = text[match.end():match.end() + 10]
        if re.match(r"\s*(?:원|만원|억원|억|만)", suffix):
            return match.group(0)
        return match.group(1) + "[계좌번호 삭제]"

    text = _ACCOUNT.sub(account, text)
    text = _NAMES.sub(lambda m: m.group(0) if m.group(3) in _NOT_NAMES else m.group(1) + m.group(2) + "[이름 삭제]", text)
    return _text(text)


def document_key(data: bytes, language: str) -> str:
    """SHA256 content-and-language identity. No bytes are retained."""
    if not isinstance(data, bytes) or not data or len(data) > MAX_BYTES:
        raise ValueError("문서 크기 또는 형식이 올바르지 않습니다.")
    digest = hashlib.sha256()
    digest.update(_language(language).encode("utf-8"))
    digest.update(b"\x00")
    digest.update(data)
    return digest.hexdigest()


def _safe_url(value: object) -> bool:
    if not isinstance(value, str) or not value or len(value) > 2048:
        return False
    if any(ord(c) <= 32 or ord(c) == 127 for c in value) or any(c in value for c in '<>"\\'):
        return False
    try:
        parts = urlsplit(value)
        return (parts.scheme == "https" and bool(parts.hostname) and "." in parts.hostname and parts.username is None and parts.password is None and parts.port in (None, 443))
    except ValueError:
        return False


def _valid_source(source: object) -> bool:
    if not isinstance(source, dict):
        return False
    if not all(isinstance(source.get(k), str) and 0 < len(source[k]) <= 2048 for k in ("id", "title", "url", "reviewed_at", "kind")):
        return False
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", source["id"]):
        return False
    if source["kind"] not in ("official-guidance", "official-service") or not _safe_url(source["url"]):
        return False
    try:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", source["reviewed_at"]):
            return False
        date.fromisoformat(source["reviewed_at"])
    except ValueError:
        return False
    return True


_TERMS = (
    ("원상복구", "원상회복", "restoration", "restore", "原状回復", "恢复原状"),
    ("수선", "수리", "하자", "repair", "repairs", "maintenance", "修繕", "维修"),
    ("중도해지", "중도 해지", "해지", "위약금", "termination", "terminate", "penalty", "解約", "解约"),
    ("보증금", "반환", "deposit", "押金", "保証金"),
    ("관리비", "공과금", "utility", "utilities", "management fee", "管理費", "物业费"),
    ("확정일자", "전입신고", "대항력", "우선변제", "registration", "住民登録", "登记"),
    ("외국인", "체류", "foreigner", "visa", "外国人", "居留"),
    ("월세", "차임", "rent", "賃料", "租金"),
    ("전대", "sublet", "sublease", "転貸", "转租"),
    ("중개", "중개보수", "broker", "brokerage", "仲介", "中介"),
)
_GENERAL = ("일반", "general", "기본", "주택임대차", "임대차계약", "housing lease")


def _contains(text: str, term: str) -> bool:
    return bool(re.search(r"(?<![a-z])" + re.escape(term) + r"(?![a-z])", text)) if term.isascii() else term in text


def select_sources(text: str, sources: list[dict], limit: int = 6) -> list[dict]:
    """Rank clause terms, fall back to general guidance; exclude invalid/duplicate IDs."""
    text = _text(text).casefold()
    if not isinstance(sources, list) or len(sources) > 200 or type(limit) is not int or not 0 <= limit <= 20:
        raise ValueError("참고 자료 목록 또는 개수 제한이 올바르지 않습니다.")
    groups = [g for g in _TERMS if any(_contains(text, term) for term in g)]
    ranked, seen = [], set()
    for index, source in enumerate(sources):
        if not _valid_source(source) or source["id"] in seen:
            continue
        keywords = source.get("keywords", [])
        if not isinstance(keywords, list) or len(keywords) > 100 or not all(isinstance(k, str) and len(k) <= 100 for k in keywords):
            continue
        chunks = [source["title"]] + keywords
        for key in ("text", "content", "summary", "description"):
            if key in source:
                if not isinstance(source[key], str) or len(source[key]) > MAX_CHARS:
                    break
                chunks.append(source[key])
        else:
            body = " ".join(chunks).casefold()
            if len(body) > MAX_CHARS:
                continue
            seen.add(source["id"])
            score = sum(10 + sum(2 for term in group if _contains(body, term)) for group in groups if any(_contains(body, term) for term in group))
            score += sum(4 for k in keywords if k and _contains(text, k.casefold()))
            general = source.get("general") is True or any(term in body for term in _GENERAL)
            ranked.append((score, general, index, source))
    specific = [r for r in ranked if r[0] > 0]
    candidates = specific if specific else ranked
    candidates.sort(key=lambda r: (-r[0], -int(r[1]), r[2]))
    return [dict(r[3]) for r in candidates[:limit]]


def diff_contracts(before: str, after: str) -> str:
    before, after = _text(before), _text(after)
    if before == after:
        return ""
    result = "\n".join(difflib.unified_diff(before.splitlines(), after.splitlines(), fromfile="before", tofile="after", lineterm=""))
    if not result:
        result = "--- before\n+++ after\n@@ line endings changed @@"
    return _text(result)


_DOCUMENT_KEYS = ("pages", "draft_text", "report", "chat", "confirmed", "fields", "warnings", "filename", "analysis_language", "source_ids", "usage_last", "report_text", "ocr_pending", "analysis_time")


def reset_document_state(state: MutableMapping, key: str) -> bool:
    """Identity is stored under document_key; only document/editor keys are cleared."""
    if not isinstance(state, MutableMapping) or not isinstance(key, str) or not re.fullmatch(r"[a-f0-9]{64}", key):
        raise ValueError("문서 상태 또는 식별자가 올바르지 않습니다.")
    if state.get("document_key") == key:
        return False
    for item in list(state):
        if item in _DOCUMENT_KEYS or (isinstance(item, str) and item.startswith("hs_doc_")):
            state.pop(item, None)
    state["document_key"] = key
    return True


_LABELS = {
    "한국어": ("요약", "계약 정보", "주의 조항", "누락·불명확한 내용", "확인 목록", "한계", "참고 자료", "페이지", "누락 페이지", "원문", "이유", "확인 질문", "출처", "검토일"),
    "English": ("Summary", "Contract fields", "Clauses to review", "Missing or unclear", "Checklist", "Limitations", "Sources", "Pages", "Missing pages", "Quote", "Reason", "Questions", "Sources", "Reviewed"),
    "日本語": ("要約", "契約情報", "注意条項", "不足・不明確な点", "確認事項", "限界", "参考資料", "ページ", "欠落ページ", "原文", "理由", "確認する質問", "出典", "確認日"),
    "中文": ("摘要", "合同信息", "需注意的条款", "缺失或不明确内容", "核对清单", "局限性", "参考资料", "页数", "缺失页", "原文", "原因", "待确认问题", "来源", "审核日期"),
}
_LEVELS = {
    "한국어": {"low": "낮음", "medium": "보통", "high": "높음", "unknown": "확인 불가"},
    "English": {"low": "Low", "medium": "Medium", "high": "High", "unknown": "Unknown"},
    "日本語": {"low": "低", "medium": "中", "high": "高", "unknown": "不明"},
    "中文": {"low": "低", "medium": "中", "high": "高", "unknown": "未知"},
}


def _escape(value: str) -> str:
    value = re.sub(r"[\x00-\x1f\x7f]", " ", value)
    value = html.escape(value, quote=True)
    value = re.sub(r"([\\`*_{}\[\]()#+.!|~-])", r"\\\1", value)
    return value.replace(":", "&#58;").replace("@", "&#64;")


def _validate_report(report: dict) -> None:
    error = "분석 보고서 형식 또는 제한이 올바르지 않습니다. 다시 확인해 주세요."
    if not isinstance(report, dict):
        raise ValueError(error)
    count = 0

    def string(value: object) -> None:
        nonlocal count
        if not isinstance(value, str) or len(value) > MAX_CHARS:
            raise ValueError(error)
        count += len(value)
        if count > MAX_CHARS:
            raise ValueError(error)

    def strings(value: object) -> None:
        if not isinstance(value, list) or len(value) > 100:
            raise ValueError(error)
        for item in value:
            string(item)

    required = {"summary", "fields", "risks", "missing", "checklist", "limitations", "sources", "pages_total", "pages_missing"}
    if not required <= report.keys():
        raise ValueError(error)
    pages = report["pages_total"]
    if type(pages) is not int or not 1 <= pages <= MAX_PAGES:
        raise ValueError(error)
    missing_pages = report["pages_missing"]
    if not isinstance(missing_pages, list) or len(missing_pages) > MAX_PAGES or any(type(p) is not int or not 1 <= p <= pages for p in missing_pages) or len(set(missing_pages)) != len(missing_pages):
        raise ValueError(error)
    string(report["summary"])
    fields = report["fields"]
    if not isinstance(fields, dict) or len(fields) > 100:
        raise ValueError(error)
    for key, value in fields.items():
        string(key)
        string(value)
    for key in ("missing", "checklist", "limitations"):
        strings(report[key])
    sources = report["sources"]
    if not isinstance(sources, list) or len(sources) > 100 or not all(_valid_source(s) for s in sources):
        raise ValueError(error)
    source_ids = {s["id"] for s in sources}
    if len(source_ids) != len(sources):
        raise ValueError(error)
    for source in sources:
        for key in ("id", "title", "url", "reviewed_at", "kind"):
            string(source[key])
    risks = report["risks"]
    if not isinstance(risks, list) or len(risks) > 100:
        raise ValueError(error)
    for risk in risks:
        if not isinstance(risk, dict) or not {"title", "level", "quote", "page", "reason", "questions", "source_ids"} <= risk.keys():
            raise ValueError(error)
        if not isinstance(risk["level"], str) or risk["level"] not in _LEVELS["English"] or type(risk["page"]) is not int or not 1 <= risk["page"] <= pages:
            raise ValueError(error)
        for key in ("title", "quote", "reason"):
            string(risk[key])
        strings(risk["questions"])
        strings(risk["source_ids"])
        if any(sid not in source_ids for sid in risk["source_ids"]):
            raise ValueError(error)


def report_to_markdown(report: dict, language: str = "한국어") -> str:
    """Only validated known fields; untrusted Markdown/HTML is escaped.

    Source URLs are escaped plain text, not executable links. Oversize output
    raises ValueError, never silently truncates. Unknown extra fields are ignored.
    """
    language = _language(language)
    _validate_report(report)
    labels = _LABELS[language]
    lines = ["# HomeSafe AI", "", "## " + labels[0], _escape(report["summary"]), "", f"{labels[7]}: {report['pages_total']}", labels[8] + ": " + (", ".join(str(p) for p in report["pages_missing"]) or "-"), "", "## " + labels[1]]
    lines.extend("- " + _escape(key) + ": " + _escape(value) for key, value in report["fields"].items())
    lines.extend(["", "## " + labels[2]])
    for risk in report["risks"]:
        lines.extend(["", "### " + _escape(risk["title"]), _LEVELS[language][risk["level"]] + f" | {labels[7]}: {risk['page']}", labels[9] + ": " + _escape(risk["quote"]), labels[10] + ": " + _escape(risk["reason"]), labels[11] + ":"])
        lines.extend("- " + _escape(q) for q in risk["questions"])
        lines.append(labels[12] + ": " + ", ".join(_escape(sid) for sid in risk["source_ids"]))
    for position, key in ((3, "missing"), (4, "checklist"), (5, "limitations")):
        lines.extend(["", "## " + labels[position]])
        lines.extend("- " + _escape(item) for item in report[key])
    lines.extend(["", "## " + labels[6]])
    for source in report["sources"]:
        lines.append("- " + _escape(source["id"]) + ": " + _escape(source["title"]) + " | " + _escape(source["url"]) + " | " + labels[13] + ": " + _escape(source["reviewed_at"]) + " | " + _escape(source["kind"]))
    lines.extend(["", disclaimer(language)])
    return _text("\n".join(lines))


class ProcessBudget:
    """Process-local UTC-daily budget; cooldown is per session; no refunds.

    Retains only hashed session IDs, counters and timestamps. Multiple worker
    processes require a shared budget outside this class. No raw docs/PII stored.
    """

    def __init__(self, max_calls: int = 40, cooldown_seconds: float = 0.0, *, clock: Callable[[], float] | None = None):
        if type(max_calls) is not int or not 0 <= max_calls <= 1000000:
            raise ValueError("호출 제한이 올바르지 않습니다.")
        if isinstance(cooldown_seconds, bool) or not isinstance(cooldown_seconds, (int, float)) or not math.isfinite(cooldown_seconds) or cooldown_seconds < 0:
            raise ValueError("대기 시간이 올바르지 않습니다.")
        if clock is not None and not callable(clock):
            raise ValueError("시간 설정이 올바르지 않습니다.")
        self.max_calls = max_calls
        self.cooldown_seconds = float(cooldown_seconds)
        self._clock = clock if clock is not None else time.time
        self._lock = threading.Lock()
        self._day = None
        self._total = 0
        self._sessions: dict[str, tuple[int, float]] = {}

    def _refresh(self, now: float) -> None:
        if not isinstance(now, (int, float)) or not math.isfinite(now):
            raise ValueError("시간 설정이 올바르지 않습니다.")
        day = int(now // 86400)
        if day != self._day:
            self._day = day
            self._total = 0
            self._sessions.clear()

    def reserve(self, session_id: str, max_session_calls: int = 8) -> bool:
        if not isinstance(session_id, str) or not session_id or len(session_id) > 256:
            raise ValueError("세션 식별자가 올바르지 않습니다.")
        if type(max_session_calls) is not int or not 0 <= max_session_calls <= 1000000:
            raise ValueError("세션 호출 제한이 올바르지 않습니다.")
        key = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
        with self._lock:
            now = self._clock()
            self._refresh(now)
            used, previous = self._sessions.get(key, (0, -math.inf))
            if self._total >= self.max_calls or used >= max_session_calls:
                return False
            if self.cooldown_seconds and now - previous < self.cooldown_seconds:
                return False
            self._sessions[key] = (used + 1, now)
            self._total += 1
            return True

    @property
    def used(self) -> int:
        with self._lock:
            self._refresh(self._clock())
            return self._total

    def session_calls(self, session_id: str) -> int:
        if not isinstance(session_id, str) or not session_id or len(session_id) > 256:
            raise ValueError("세션 식별자가 올바르지 않습니다.")
        key = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
        with self._lock:
            self._refresh(self._clock())
            return self._sessions.get(key, (0, 0.0))[0]
