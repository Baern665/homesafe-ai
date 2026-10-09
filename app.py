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

VERSION = "0.2.0"
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

st.set_page_config(page_title="HomeSafe AI | 계약 이해 도우미", layout="wide", initial_sidebar_state="expanded")
st.markdown("""<style>
.block-container {max-width:1120px; padding-top:2rem; padding-bottom:3rem;}
[data-testid="stMetric"] {background:var(--secondary-background-color); border-radius:12px; padding:14px;}
.stButton>button, .stDownloadButton>button {border-radius:9px;}
</style>""", unsafe_allow_html=True)

def setting(name, default=""):
    value = os.getenv(name)
    if value is not None:
        return value
    try:
        return st.secrets.get(name, default)
    except Exception:
        return default

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
    st.markdown("### HomeSafe AI")
    st.caption("계약 이해 · 원문 확인 · 질문 준비")
    language = st.selectbox("리포트 언어 / Report language", LANGUAGES)
    st.divider()
    st.caption("무료 베타 · 실제 결제는 받지 않습니다.")
    st.caption("10MB · 최대 12페이지 · 최대 35,000자")
    if st.button("나의 문서·결과 지우기", use_container_width=True):
        reset_document_state(st.session_state, hashlib.sha256(uuid.uuid4().bytes).hexdigest())
        st.session_state.manual_input = ""
        st.session_state.upload_revision += 1
        for k in ["compare_before", "compare_after", "compare_result", "consent_ai", "consent_ocr"]:
            st.session_state.pop(k, None)
        st.rerun()
    st.caption("삭제 시 이 세션의 문서와 결과를 제거합니다. AI 제공자에게 이미 전송된 데이터의 처리에는 해당 제공자 정책이 적용됩니다.")
    st.caption(f"v{VERSION}")

st.caption("FOR INTERNATIONAL STUDENTS & FIRST-TIME RENTERS")
st.title("계약 전, 이해부터.")
st.write("한국어 계약 원문을 확인하고, 원하는 언어로 주의 사항과 계약 전 질문을 정리하세요.")
a,b,c = st.columns(3)
a.metric("읽고 확인", "원문·금액")
b.metric("근거와 함께", "조항별 안내")
c.metric("다음 행동", "질문·체크리스트")
st.warning("법률 자문·전세사기 판정 서비스가 아닙니다. 등기·소유권·채무·시세는 확인하지 않으며 보증금의 안전을 보장하지 않습니다.")

try:
    sources = load_sources()
except Exception:
    st.error("참고 자료를 불러오지 못했습니다. 오류 코드: SOURCE-CONFIG")
    st.stop()
api_key = setting("OPENAI_API_KEY")
model = setting("HOMESAFE_MODEL", "gpt-4.1-mini")
budget = process_budget(bounded_setting("HOMESAFE_DAILY_CALL_LIMIT", 40, 300))
ai = AIService(api_key, model, budget, st.session_state.session_id,
               bounded_setting("HOMESAFE_SESSION_CALL_LIMIT", 8, 20)) if api_key else None

def show_error(error):
    if isinstance(error, (SafeError, ValueError)):
        st.error(str(error))
    else:
        st.error("처리를 완료하지 못했습니다. 문서 형식을 확인하세요. 오류 코드: DOCUMENT-PROCESS")

def combined_text(pages):
    return "\n\n".join(f"[계약서 {p['page']}페이지]\n{p['text']}" for p in pages)

def read_files(files, ocr_allowed):
    uploads = [{"name":f.name,"data":f.getvalue()} for f in files]
    return extract_contract_files(uploads, ocr=ai.ocr if ai else None, ocr_allowed=ocr_allowed)

def prefill(text, label):
    m = re.search(re.escape(label) + r"\s*[:：]\s*([^\n]{1,120})", text)
    return m.group(1) if m else "확인되지 않음"

review_tab, compare_tab, info_tab = st.tabs(["계약서 검토", "수정본 비교", "이용 안내 · 기관 도입"])
with review_tab:
    st.subheader("1. 계약서 준비")
    mode = st.radio("입력 방법", ["텍스트 직접 입력", "PDF · 사진 업로드"], horizontal=True)
    if mode == "텍스트 직접 입력":
        if st.button("합성 예시 계약서로 체험하기"):
            st.session_state.manual_input = SAMPLE
        manual = st.text_area("계약서 원문 / Contract text", key="manual_input", height=210,
                              max_chars=MAX_CHARS, placeholder="계약 내용을 붙여 넣으세요. 이름·식별번호·계좌번호는 먼저 가리는 것이 좋습니다.")
        current_key = document_key(hashlib.sha256(b"text\x00" + manual.encode("utf-8")).digest(), language)
        files = []
    else:
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
        st.subheader("2. 읽은 내용과 금액 확인")
        for warning in st.session_state.get("warnings", []):
            st.warning(warning)
        st.caption("AI가 볼 내용입니다. 남아 있는 개인정보를 가리고, 빠진 조항·잘못 읽은 금액·날짜를 수정하세요. 확인 불가 부분은 분석 전에 해결해 주세요.")
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
        st.subheader("3. 분석 결과")
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
            c1.download_button("PDF 리포트 내려받기", pdf, "homesafe-report.pdf", "application/pdf", use_container_width=True)
            c2.download_button("텍스트 리포트 내려받기", markdown, "homesafe-report.md", "text/markdown", use_container_width=True)
        except Exception:
            st.warning("다운로드 파일을 만들지 못했습니다. 화면 결과를 먼저 확인하세요. 오류 코드: REPORT-EXPORT")
        st.subheader("내 계약서에 질문하기")
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
    if st.button("변경 내용 비교"):
        try:
            st.session_state.compare_result = diff_contracts(redact_pii(before), redact_pii(after))
        except ValueError as error:
            show_error(error)
    if st.session_state.get("compare_result"):
        st.code(st.session_state.compare_result, language="diff")
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
    st.caption("실제 결제는 아직 제공하지 않습니다. 운영자 정보, 결제사업자 심사, 환불·개인정보 정책, 영구적인 결제 검증과 이용권 관리가 갖춰진 후 출시해야 합니다.")
    st.markdown("#### 참고 자료와 적용 범위")
    st.write("전체 법령 DB가 아니라, 아래 공식 안내를 확인해 작성한 제한적인 요약 자료입니다. 자료 확인일은 법령의 최신성이나 개별 계약에 대한 적용 가능성을 보장하지 않습니다.")
    for source in sources:
        with st.expander(source["title"]):
            st.write(source.get("content", ""))
            st.caption(f"{source.get('agency','')} · 확인일 {source['reviewed_at']} · {source['kind']}")
            st.link_button("공식 원문 확인", source["url"])
    st.markdown("#### 개인정보·보관 안내")
    st.write("원문은 영구 저장하지 않습니다. 문서·결과 지우기로 현재 세션을 정리할 수 있습니다. 자동 마스킹은 완전하지 않으며, 사진 OCR에는 원본 전송이 필요합니다. 이미 외부 AI에 전송한 데이터는 제공자 정책의 적용을 받습니다.")
    st.write("현재 이용 한도는 단일 서버 메모리 기준입니다. 재시작·다중 서버·익명 재접속을 완전히 제어하는 상용 결제·인증 시스템이 아닙니다.")

st.divider()
st.caption(f"HomeSafe AI v{VERSION} · {disclaimer(language)}")
