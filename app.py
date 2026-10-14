"""HomeSafe AI: consent-first rental contract understanding, free public beta."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import re
import uuid
from datetime import datetime, timezone
import streamlit as st
from hs_core import (MAX_BYTES, MAX_PAGES, MAX_CHARS, PII_NOTICE, ProcessBudget,
    validate_upload, redact_pii, document_key, reset_document_state,
    select_sources, diff_contracts, report_to_markdown, disclaimer)
from hs_ai import AIService, SafeError
from hs_report import report_to_pdf
from hs_documents import extract_contract_files

VERSION = "0.3.1"
BRAND = "집계약 노트"
ROOT = Path(__file__).resolve().parent
LANGUAGES = ["한국어", "English", "日本語", "中文"]
SAMPLE = """주택 임대차 계약서 (합성 예시, 실제 계약이 아닙니다)
소재지: 서울특별시 예시구 예시로 12, 301호
보증금: 10,000,000원
월세: 600,000원 (매월 5일 납부)
관리비: 월 80,000원. 관리비 포함 항목은 별도 협의한다.
계약 기간: 2026년 11월 1일부터 2027년 10월 31일까지
입주일: 2026년 11월 1일
특약 1. 임차인은 중도 퇴거 시 새로운 임차인의 입주일까지 월세를 부담한다.
특약 2. 퇴거 시 전문 청소비 200,000원을 임차인이 부담한다.
특약 3. 모든 시설의 수리비는 사유와 관계없이 임차인이 부담한다.
보증금 반환 시점: 계약서에 기재되지 않음.
"""

st.set_page_config(page_title="집계약 노트 | 임대차 계약 읽기", layout="wide", initial_sidebar_state="expanded")
st.markdown("""<style>
.block-container {max-width:1060px; padding-top:2.6rem; padding-bottom:3rem;}
[data-testid="stAppViewContainer"] {background:#faf9f6; color:#263d34;}
[data-testid="stSidebar"] {background:#f0f2ed; border-right:1px solid #dce2d9;}
h1,h2,h3 {letter-spacing:-0.045em; color:#233d32;}
h1 {font-size:2.8rem !important; line-height:1.25 !important;}
p {line-height:1.75;}
.stButton>button, .stDownloadButton>button {border-radius:6px; min-height:44px;}
button[kind="primary"] {background:#315b46; border-color:#315b46; color:#fff;}
button[kind="primary"]:hover {background:#244635; border-color:#244635; color:#fff;}
button:focus-visible, a:focus-visible {outline:3px solid #648c76 !important; outline-offset:3px;}
[data-baseweb="tab-list"] {gap:1.5rem; border-bottom:1px solid #dce2d9;}
[data-baseweb="tab"] {min-height:48px;}
[data-testid="stTextArea"] textarea {line-height:1.65;}
.note-intro {border-left:3px solid #6e8b6c; padding:0.1rem 0 0.1rem 1.2rem; margin:0.3rem 0 1.6rem;}
.note-intro p {margin:0; color:#4c6056; font-size:1.02rem;}
.note-kicker {font-size:0.84rem; letter-spacing:0.03em; color:#567061; margin-bottom:0.6rem;}
@media (max-width:640px) {
 .block-container {padding:1.5rem 1rem 2rem;}
 h1 {font-size:2rem !important;}
 [data-baseweb="tab-list"] {gap:0.6rem;}
 [data-baseweb="tab"] {font-size:0.88rem;}
}
@media (prefers-color-scheme:dark) {
 [data-testid="stAppViewContainer"] {background:#1b2520; color:#ecf0e9;}
 [data-testid="stSidebar"] {background:#202e26; border-color:#3e5145;}
 h1,h2,h3 {color:#ecf0e9;}
 .note-intro p,.note-kicker {color:#c2d4c7;}
}
</style>""", unsafe_allow_html=True)

def setting(name, default=""):
    # Cloud Secrets is the operator-editable source of truth. A stale inherited
    # environment value must not shadow a newly saved secret.
    try:
        if name in st.secrets:
            return st.secrets[name]
    except Exception:
        pass
    return os.getenv(name, default)

def bounded_setting(name, default, maximum):
    try:
        return max(1, min(maximum, int(setting(name, default))))
    except (ValueError, TypeError):
        return default

@st.cache_resource
def process_budget(limit):
    return ProcessBudget(max_calls=limit)

@st.cache_data
def load_sources():
    data = json.loads((ROOT / "sources.json").read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("참고 자료 설정을 확인해야 합니다.")
    # Reuse core validation and never execute a legacy serialized index.
    return select_sources("", data, limit=6)

if "session_id" not in st.session_state:
    st.session_state.session_id = str(uuid.uuid4())
if "upload_revision" not in st.session_state:
    st.session_state.upload_revision = 0

with st.sidebar:
    st.markdown("### 집계약 노트")
    st.caption("집 계약, 모르는 부분을 남기지 않도록.")
    language = st.selectbox("결과 언어 / Report language", LANGUAGES)
    st.divider()
    st.caption("무료로 이용하는 베타 서비스")
    st.caption("결과 언어만 바뀝니다. 화면 안내는 한국어입니다.")
    st.caption("10MB · 최대 12페이지 · 최대 35,000자")
    if st.button("나의 문서·결과 지우기", use_container_width=True):
        reset_document_state(st.session_state, hashlib.sha256(uuid.uuid4().bytes).hexdigest())
        st.session_state.manual_input = ""
        st.session_state.compare_before = ""
        st.session_state.compare_after = ""
        st.session_state.upload_revision += 1
        for k in ["compare_result", "compare_key", "consent_ai", "consent_ocr"]:
            st.session_state.pop(k, None)
        st.rerun()
    st.caption("삭제 시 이 세션의 문서와 결과를 제거합니다. AI 제공자에게 이미 전송된 데이터의 처리에는 해당 제공자 정책이 적용됩니다.")
    st.caption(f"v{VERSION}")

st.markdown('<div class="note-kicker">처음 하는 집 계약을 위한 읽기 도구</div>', unsafe_allow_html=True)
st.title("서명하기 전, 한 번 더 읽어보세요.")
st.markdown('<div class="note-intro"><p>관리비에는 무엇이 포함될까요? 수리비는 누가 낼까요?<br>계약서에서 확인할 내용과 집주인에게 물어볼 질문을 정리해 드립니다.</p></div>', unsafe_allow_html=True)
st.caption("계약서 넣기 → 원문·금액 확인 → 확인할 내용과 질문 정리")
st.info("계약 내용을 이해하는 참고 도구입니다. 법률 자문이나 전세사기 판정은 하지 않으며, 등기·소유권·채무·시세 확인과 보증금 안전 보장은 제공하지 않습니다.")

try:
    sources = load_sources()
except Exception:
    st.error("참고 자료를 불러오지 못했습니다. 오류 코드: SOURCE-CONFIG")
    st.stop()
configured_api_key = setting("OPENAI_API_KEY")
api_key = configured_api_key.strip() if isinstance(configured_api_key, str) else ""
model = setting("HOMESAFE_MODEL", "gpt-4.1-mini")
budget = process_budget(bounded_setting("HOMESAFE_DAILY_CALL_LIMIT", 40, 300))
ai = AIService(api_key, model, budget, st.session_state.session_id,
               bounded_setting("HOMESAFE_SESSION_CALL_LIMIT", 8, 20)) if api_key else None

def show_error(error):
    if isinstance(error, (SafeError, ValueError)):
        st.error(str(error))
    else:
        st.error("문서를 읽지 못했습니다. PDF가 암호로 잠겨 있는지, 사진의 글씨가 선명한지 확인해 주세요. 계속 실패하면 텍스트를 직접 입력해 주세요. 오류 코드: DOCUMENT-PROCESS")

def combined_text(pages):
    return "\n\n".join(f"[계약서 {p['page']}페이지]\n{p['text']}" for p in pages)

def read_files(files, ocr_allowed):
    uploads = [{"name":f.name,"data":f.getvalue()} for f in files]
    return extract_contract_files(uploads, ocr=ai.ocr if ai else None, ocr_allowed=ocr_allowed)

def prefill(text, label):
    m = re.search(re.escape(label) + r"\s*[:：]\s*([^\n]{1,120})", text)
    return m.group(1) if m else "확인되지 않음"

review_tab, compare_tab, info_tab = st.tabs(["계약서 읽기", "바뀐 내용 비교", "이용 안내"])
with review_tab:
    st.subheader("1. 계약서를 넣어주세요")
    st.caption("처음이라면 예시 계약서로 순서를 살펴보세요. 실제 개인정보가 없는 가상 문서입니다.")
    mode = st.radio("입력 방법", ["텍스트 직접 입력", "PDF · 사진 업로드"], horizontal=True)
    if mode == "텍스트 직접 입력":
        if st.button("예시 계약서로 시작하기"):
            st.session_state.manual_input = SAMPLE
        st.caption("텍스트 읽기에는 외부 AI를 호출하지 않습니다. 분석은 원문 확인과 별도 동의 후 시작합니다.")
        manual = st.text_area("계약서 내용", key="manual_input", height=210,
                              max_chars=MAX_CHARS, placeholder="계약서 본문과 특약을 붙여 넣어주세요. 이름·주민등록번호·계좌번호는 먼저 가려주세요.")
        current_key = document_key(hashlib.sha256(b"text\x00" + manual.encode("utf-8")).digest(), language)
        files = []
    else:
        st.caption("PDF·JPG·PNG를 지원합니다. 전체 10MB, 최대 12파일·12페이지까지 읽습니다. 사진·스캔은 아래 별도 동의가 필요합니다.")
        files = st.file_uploader("계약서 PDF 또는 사진 (전체 10MB 이하)", type=["pdf", "png", "jpg", "jpeg"],
                                accept_multiple_files=True, max_upload_size=10, key=f"uploads_{st.session_state.upload_revision}")
        if len(files) > MAX_PAGES or sum(f.size for f in files) > MAX_BYTES:
            reset_document_state(st.session_state, hashlib.sha256(b"over-limit").hexdigest())
            st.error("전체 파일은 10MB 이하, 최대 12개로 업로드하세요.")
            st.stop()
        ordered = b"".join(len(f.getvalue()).to_bytes(8,"big") + f.getvalue() for f in files)
        current_key = document_key(hashlib.sha256(b"files\x00" + ordered).digest(), language)
        if files:
            st.caption("선택한 순서대로 문서 페이지를 읽습니다. 전체 계약서와 특약을 빠뜨리지 마세요.")
    if reset_document_state(st.session_state, current_key):
        st.session_state.pop("consent_ocr", None)
    with st.expander("개인정보와 AI 전송 안내", expanded=False):
        st.write("텍스트 PDF는 서버 메모리에서 먼저 추출합니다. 로컬 마스킹은 완전하지 않으므로 전송 전 추출된 내용을 직접 확인하세요.")
        st.write(PII_NOTICE)
        st.write("사진·스캔 OCR을 허용하면 개인정보가 포함될 수 있는 페이지 원본 이미지가 OpenAI에 전송됩니다. 이름·신분번호·계좌번호를 가린 사본을 권장합니다.")
        st.write("AI 분석 시 확인한 계약 텍스트와 질문이 OpenAI에 전송됩니다. 이 앱은 계약 원문을 파일·DB·분석 로그에 저장하지 않지만, 브라우저 세션과 서버 메모리에 처리 중 데이터가 있습니다.")
        st.link_button("OpenAI 데이터 처리 정책 확인", "https://platform.openai.com/docs/guides/your-data")
    ocr_allowed = False
    if mode == "PDF · 사진 업로드":
        st.caption("텍스트를 선택할 수 있는 PDF는 먼저 AI 없이 읽습니다. 사진·스캔은 원본 이미지를 OpenAI에 보내 글씨를 읽습니다. 민감한 정보는 업로드 전에 가려주세요.")
        ocr_allowed = st.checkbox("사진·스캔 페이지 원본의 OpenAI 전송을 허용합니다 (OCR에만 필요)", key="consent_ocr", disabled=ai is None)
    if ai is None:
        st.info("AI 연결이 설정되지 않았습니다. 문서 입력·텍스트 PDF 추출·수정본 비교는 사용할 수 있습니다.")
    if st.button("계약서 내용 읽기", type="primary", disabled=(not manual.strip() if mode == "텍스트 직접 입력" else not files)):
        for k in list(st.session_state):
            if k in ["pages", "report", "chat", "confirmed", "warnings"] or k.startswith("hs_doc_"):
                st.session_state.pop(k, None)
        try:
            with st.spinner("문서 내용을 확인하고 있습니다."):
                if mode == "텍스트 직접 입력":
                    if len(manual) > MAX_CHARS:
                        raise ValueError("입력은 35,000자 이하로 작성하세요.")
                    pages, warnings = [{"page":1,"text":redact_pii(manual)}], []
                else:
                    pages, warnings = read_files(files, ocr_allowed)
                st.session_state.pages = pages
                st.session_state.warnings = warnings
                st.session_state.analysis_language = language
                st.session_state.chat = []
        except Exception as error:
            show_error(error)

    if st.session_state.get("pages"):
        st.subheader("2. 원문과 금액을 확인해주세요")
        for warning in st.session_state.get("warnings", []):
            st.warning(warning)
        st.caption("AI가 볼 내용입니다. 남아 있는 개인정보를 가리고, 빠진 조항·잘못 읽은 금액·날짜를 수정하세요. 확인 불가 부분은 분석 전에 해결해 주세요.")
        st.caption("자동으로 채워진 값은 참고용입니다. ‘확인되지 않음’은 항목을 찾지 못했다는 뜻이며, 조건이 없다는 뜻은 아닙니다.")
        text = combined_text(st.session_state.pages)
        with st.form("confirm_contract"):
            edited_pages = []
            for p in st.session_state.pages:
                edited = st.text_area(f"계약서 {p['page']}페이지", p['text'], height=180, key=f"hs_doc_page_{p['page']}", max_chars=MAX_CHARS)
                edited_pages.append({"page":p["page"],"text":edited})
            c1,c2,c3 = st.columns(3)
            fields = {}
            fields["보증금"] = c1.text_input("확인한 보증금", prefill(text,"보증금"), key="hs_doc_deposit", max_chars=300)
            fields["월세"] = c2.text_input("확인한 월세", prefill(text,"월세"), key="hs_doc_rent", max_chars=300)
            fields["관리비"] = c3.text_input("확인한 관리비", prefill(text,"관리비"), key="hs_doc_fee", max_chars=300)
            fields["계약 기간"] = st.text_input("확인한 계약 기간", prefill(text,"계약 기간"), key="hs_doc_duration", max_chars=500)
            fields["입주일"] = st.text_input("확인한 입주일", prefill(text,"입주일"), key="hs_doc_movein", max_chars=300)
            fields["해지 조건"] = st.text_input("확인한 해지 조건 (없으면 확인되지 않음)", prefill(text,"해지 조건"), key="hs_doc_termination", max_chars=1000)
            checked = st.checkbox("전체 조항과 금액·날짜를 원문과 비교했고, 남아 있는 개인정보를 확인했습니다.", key="hs_doc_checked")
            consent = st.checkbox("확인한 텍스트와 이후 질문을 OpenAI에 전송하여 참고용 분석을 받는 데 동의합니다.", key="hs_doc_ai_consent")
            submit = st.form_submit_button("확인한 내용으로 분석하기", type="primary", disabled=ai is None)
        if submit:
            st.session_state.pop("report", None)
            st.session_state.chat = []
            try:
                if not checked or not consent:
                    raise ValueError("원문 확인과 AI 전송 동의가 모두 필요합니다.")
                if any(not p['text'].strip() for p in edited_pages) or sum(len(p['text']) for p in edited_pages) > MAX_CHARS:
                    raise ValueError("빈 페이지 또는 처리 한도를 초과한 입력을 확인하세요.")
                if any("[확인 불가]" in p['text'] for p in edited_pages):
                    raise ValueError("[확인 불가] 부분을 원문과 비교해 수정하거나, 직접 확인할 수 없는 문서는 전문 상담을 이용하세요.")
                safe_pages = [{"page":p['page'],"text":redact_pii(p['text'])} for p in edited_pages]
                safe_fields = {k:redact_pii(v) for k,v in fields.items()}
                with st.spinner("조항별 안내 자료를 찾고 원문·출처를 검증하고 있습니다."):
                    report = ai.analyze(safe_pages, language, sources, safe_fields)
                st.session_state.pages = safe_pages
                st.session_state.fields = safe_fields
                st.session_state.report = report
                st.session_state.confirmed = True
                st.session_state.analysis_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
                st.session_state.usage_last = ai.metrics.copy()
            except Exception as error:
                show_error(error)

    if st.session_state.get("report"):
        report = st.session_state.report
        st.subheader("3. 계약 전에 확인할 내용")
        st.caption("주의 수준은 확인의 우선순위이며 사기 확률·안전 등급이 아닙니다. " + st.session_state.get("analysis_time", ""))
        st.caption("계약 정보의 금액·기간·입주일·해지 조건은 사용자가 확인한 입력값입니다. 실제 지급·계약 사실을 검증한 값이 아닙니다.")
        st.markdown(report_to_markdown(report, language))
        with st.expander("분석에 사용한 공식 안내 원문"):
            for source in report["sources"]:
                st.link_button(source["title"], source["url"])
        try:
            markdown = report_to_markdown(report, language)
            pdf = report_to_pdf(report, language)
            c1,c2 = st.columns(2)
            c1.download_button("PDF 리포트 내려받기", pdf, "jipgyeyak-note-report.pdf", "application/pdf", use_container_width=True)
            c2.download_button("텍스트 리포트 내려받기", markdown, "jipgyeyak-note-report.md", "text/markdown", use_container_width=True)
        except Exception:
            st.warning("다운로드 파일을 만들지 못했습니다. 화면 결과를 먼저 확인하세요. 오류 코드: REPORT-EXPORT")
        st.subheader("아직 궁금한 점이 있나요?")
        st.caption("예: 관리비에 포함된 항목이 적혀 있나요? / 퇴거 청소비는 어떻게 확인하면 좋을까요?")
        st.caption("현재 확인한 계약서와 안내 자료만 참조합니다. 대화 전체 대신 현재 질문을 보내 비용과 데이터 전송을 줄입니다.")
        with st.form("question_form", clear_on_submit=True):
            question = st.text_input("질문 / Question", max_chars=1000, placeholder="내 특약 3번에서 어떤 점을 확인해야 하나요?")
            ask = st.form_submit_button("계약서 근거로 답변받기", disabled=ai is None)
        if ask:
            try:
                with st.spinner("현재 계약서의 관련 조항을 확인하고 있습니다."):
                    answer = ai.answer(question, st.session_state.pages, report, language, sources)
                chats = st.session_state.get("chat", [])
                chats.append({"question":redact_pii(question),"response":answer})
                st.session_state.chat = chats[-6:]
            except Exception as error:
                show_error(error)
        for chat in reversed(st.session_state.get("chat", [])):
            with st.container(border=True):
                st.text(chat["question"])
                st.text(chat["response"]["answer"])
                for evidence in chat["response"]["evidence"]:
                    st.caption(f"계약서 {evidence['page']}페이지")
                    st.text(evidence["quote"])
                for sid in chat["response"]["source_ids"]:
                    source = next((s for s in sources if s["id"]==sid),None)
                    if source:
                        st.link_button(source["title"], source["url"])
                st.caption(disclaimer(language))

with compare_tab:
    st.subheader("수정 전후, 무엇이 달라졌나요?")
    st.write("계약서 두 버전의 텍스트 차이를 확인합니다. AI 호출 없이 처리하며, 법적 유불리는 판단하지 않습니다.")
    left,right = st.columns(2)
    before = left.text_area("수정 전 계약 내용", height=260, max_chars=MAX_CHARS, key="compare_before")
    after = right.text_area("수정 후 계약 내용", height=260, max_chars=MAX_CHARS, key="compare_after")
    compare_key = hashlib.sha256((before + "\x00" + after).encode("utf-8")).hexdigest()
    if st.session_state.get("compare_key") != compare_key:
        st.session_state.pop("compare_result", None)
        st.session_state.compare_key = compare_key
    st.caption("이름·주민등록번호·계좌번호는 먼저 가려주세요. 내용은 외부 AI로 보내지 않습니다.")
    if st.button("변경 내용 비교"):
        try:
            st.session_state.compare_result = diff_contracts(redact_pii(before), redact_pii(after))
        except ValueError as error:
            show_error(error)
    if st.session_state.get("compare_result"):
        changes = st.session_state.compare_result
        removed = [line[1:] for line in changes.splitlines() if line.startswith("-") and not line.startswith("---")]
        added = [line[1:] for line in changes.splitlines() if line.startswith("+") and not line.startswith("+++")]
        if removed or added:
            st.write(f"빠진 내용 {len(removed)}줄 · 새로 들어간 내용 {len(added)}줄")
            old_col, new_col = st.columns(2)
            with old_col:
                st.markdown("**수정 전 내용**")
                st.text("\n".join(removed) or "삭제된 내용 없음")
            with new_col:
                st.markdown("**수정 후 내용**")
                st.text("\n".join(added) or "추가된 내용 없음")
        else:
            st.info("두 내용에서 텍스트 차이를 찾지 못했습니다. 자동 마스킹된 개인정보의 차이는 표시하지 않습니다.")
        with st.expander("줄 단위 차이 자세히 보기"):
            st.code(changes, language="diff")
        st.download_button("차이 내려받기", st.session_state.compare_result, "contract-changes.txt", "text/plain")

with info_tab:
    st.subheader("어떤 도움을 받을 수 있나요?")
    st.write("계약 조건 이해, 주의 조항 확인, 계약 전 질문 준비, 수정본 차이 확인을 돕습니다. 중요한 결정은 공식 기관 또는 적격 전문가에게 확인하세요.")
    st.markdown("#### 무료 베타")
    st.write("현재 분석·질문·PDF 다운로드·수정본 비교는 무료입니다. 과도한 요청을 막기 위해 세션·서버 이용 한도가 있습니다.")
    st.markdown("#### 기관 도입")
    st.write("대학 국제처·유학원에서 학생의 다국어 계약 이해와 상담 준비를 지원하는 용도로 도입을 상담할 수 있습니다.")
    contact = setting("HOMESAFE_CONTACT_EMAIL")
    operator = setting("HOMESAFE_OPERATOR_NAME")
    if isinstance(contact,str) and re.fullmatch(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",contact):
        st.link_button("기관 도입 문의", "mailto:"+contact+"?subject=HomeSafe%20AI%20institutional%20pilot")
        st.caption("계약 원문이나 신분번호는 문의 이메일에 첨부하지 마세요.")
    else:
        st.info("기관 도입 문의 창구를 준비 중입니다. 운영자 연락처가 설정되면 여기서 안내합니다.")
    if operator:
        st.text("운영자: " + str(operator))
    st.caption("지금은 결제나 유료 이용권 없이 사용할 수 있습니다.")
    st.markdown("#### 참고 자료와 적용 범위")
    st.write("전체 법령 DB가 아니라, 아래 공식 안내를 확인해 작성한 제한적인 요약 자료입니다. 자료 확인일은 법령의 최신성이나 개별 계약에 대한 적용 가능성을 보장하지 않습니다.")
    for source in sources:
        with st.expander(source["title"]):
            st.write(source.get("content", ""))
            st.caption(f"{source.get('agency','')} · 확인일 {source['reviewed_at']} · {source['kind']}")
            st.link_button("공식 원문 확인", source["url"])
    st.markdown("#### 개인정보·보관 안내")
    st.write("원문은 영구 저장하지 않습니다. 문서·결과 지우기로 현재 세션을 정리할 수 있습니다. 자동 마스킹은 완전하지 않으며, 사진 OCR에는 원본 전송이 필요합니다. 이미 외부 AI에 전송한 데이터는 제공자 정책의 적용을 받습니다.")
    st.write("베타 운영 중에는 이용 한도에 도달하거나 연결 문제로 분석이 잠시 중단될 수 있습니다. 이때도 텍스트 읽기와 수정본 비교는 이용할 수 있습니다.")

st.divider()
st.caption(f"{BRAND} v{VERSION} · {disclaimer(language)}")
