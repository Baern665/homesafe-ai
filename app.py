
import base64
import pymupdf
import streamlit as st

from dotenv import load_dotenv
from langchain_core.documents import Document
from langchain_core.messages import HumanMessage
from langchain_core.prompts import ChatPromptTemplate
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_community.vectorstores import FAISS
from langchain_openai import ChatOpenAI


# --------------------------------------------------
# 기본 설정
# --------------------------------------------------

PROJECT_NAME = "HomeSafe AI"
STUDENT_NAME = "황보종원"

load_dotenv()

st.set_page_config(
    page_title=PROJECT_NAME,
    page_icon="🏠",
    layout="centered",
    initial_sidebar_state="collapsed"
)


# --------------------------------------------------
# 화면 디자인
# --------------------------------------------------

st.markdown(
    """
    <style>
    .stApp {
        background:
            linear-gradient(
                180deg,
                #f5f8ff 0%,
                #ffffff 45%,
                #f8fafc 100%
            );
    }

    .block-container {
        max-width: 920px;
        padding-top: 2.2rem;
        padding-bottom: 4rem;
    }

    #MainMenu {
        visibility: hidden;
    }

    footer {
        visibility: hidden;
    }

    header {
        background: transparent;
    }

    .hero-box {
        padding: 2.4rem 2.2rem;
        border-radius: 24px;
        background:
            linear-gradient(
                135deg,
                #163b70 0%,
                #2458a6 55%,
                #3778d4 100%
            );
        color: white;
        box-shadow:
            0 18px 45px rgba(30, 74, 135, 0.20);
        margin-bottom: 1.8rem;
    }

    .hero-badge {
        display: inline-block;
        background: rgba(255, 255, 255, 0.16);
        border: 1px solid rgba(255, 255, 255, 0.28);
        border-radius: 999px;
        padding: 0.38rem 0.85rem;
        font-size: 0.82rem;
        margin-bottom: 1rem;
    }

    .hero-title {
        font-size: 2.35rem;
        font-weight: 800;
        line-height: 1.2;
        margin-bottom: 0.7rem;
    }

    .hero-description {
        font-size: 1.02rem;
        line-height: 1.75;
        color: rgba(255, 255, 255, 0.88);
        margin: 0;
    }

    .section-card {
        background: white;
        border: 1px solid #e3eaf4;
        border-radius: 20px;
        padding: 1.6rem;
        margin-bottom: 1.3rem;
        box-shadow:
            0 8px 24px rgba(29, 56, 92, 0.07);
    }

    .section-number {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        width: 30px;
        height: 30px;
        border-radius: 10px;
        background: #e8f0ff;
        color: #2458a6;
        font-weight: 800;
        margin-right: 0.45rem;
    }

    .section-title {
        font-size: 1.2rem;
        font-weight: 750;
        color: #172b4d;
    }

    .section-description {
        color: #64748b;
        font-size: 0.92rem;
        margin-top: 0.5rem;
        margin-bottom: 1rem;
        line-height: 1.65;
    }

    .feature-box {
        background: #f8fafc;
        border: 1px solid #e5eaf1;
        border-radius: 16px;
        padding: 1.15rem;
        min-height: 145px;
    }

    .feature-icon {
        font-size: 1.55rem;
        margin-bottom: 0.5rem;
    }

    .feature-title {
        color: #1e3557;
        font-size: 0.98rem;
        font-weight: 750;
        margin-bottom: 0.35rem;
    }

    .feature-text {
        color: #718096;
        font-size: 0.86rem;
        line-height: 1.55;
    }

    .notice-box {
        background: #fff8e8;
        border-left: 5px solid #f0b429;
        border-radius: 12px;
        padding: 1rem 1.1rem;
        color: #674d16;
        font-size: 0.88rem;
        line-height: 1.6;
        margin-top: 1rem;
    }

    .result-header {
        background:
            linear-gradient(
                135deg,
                #eef4ff,
                #f8fbff
            );
        border: 1px solid #d8e5fb;
        border-radius: 16px;
        padding: 1.2rem 1.3rem;
        margin-bottom: 1rem;
    }

    .result-title {
        color: #15365f;
        font-size: 1.25rem;
        font-weight: 800;
        margin-bottom: 0.25rem;
    }

    .result-text {
        color: #62738a;
        font-size: 0.9rem;
    }

    div[data-testid="stFileUploader"] {
        background: #f8fbff;
        border: 2px dashed #b9cce8;
        border-radius: 18px;
        padding: 0.8rem;
    }

    div[data-testid="stSelectbox"] > div {
        border-radius: 12px;
    }

    div[data-testid="stTextInput"] input {
        border-radius: 12px;
    }

    div.stButton > button {
        border-radius: 12px;
        min-height: 3rem;
        font-weight: 700;
    }

    div[data-testid="stDownloadButton"] > button {
        border-radius: 12px;
        min-height: 3rem;
        font-weight: 700;
    }

    .footer-box {
        margin-top: 2.5rem;
        padding-top: 1.5rem;
        border-top: 1px solid #e5eaf1;
        text-align: center;
        color: #94a3b8;
        font-size: 0.82rem;
        line-height: 1.7;
    }
    </style>
    """,
    unsafe_allow_html=True
)


# --------------------------------------------------
# PDF 텍스트 추출 함수
# --------------------------------------------------

def extract_uploaded_pdf_pages(uploaded_file):
    documents = []
    file_content = uploaded_file.getvalue()

    with pymupdf.open(
        stream=file_content,
        filetype="pdf"
    ) as pdf:

        for page_number, page in enumerate(pdf, start=1):
            text = page.get_text(
                "text",
                sort=True
            ).strip()

            if text:
                documents.append(
                    Document(
                        page_content=text,
                        metadata={
                            "source": uploaded_file.name,
                            "page": page_number
                        }
                    )
                )

    return documents


# --------------------------------------------------
# 이미지 텍스트 추출 함수
# --------------------------------------------------

def extract_uploaded_image_text(uploaded_file, llm):
    image_bytes = uploaded_file.getvalue()

    image_base64 = base64.b64encode(
        image_bytes
    ).decode("utf-8")

    mime_type = uploaded_file.type

    message = HumanMessage(
        content=[
            {
                "type": "text",
                "text": """
이 이미지는 주택 임대차 계약서입니다.

이미지에 보이는 계약서 내용을 텍스트로 정리하세요.

규칙:
1. 계약서의 항목과 조항을 가능한 원문 그대로 작성하세요.
2. 보증금, 월세, 관리비, 날짜, 주소, 계약 기간을 정확히 구분하세요.
3. 특약사항과 해지 조건을 빠뜨리지 마세요.
4. 읽기 어려운 부분은 추측하지 말고 '확인 불가'라고 작성하세요.
5. 아직 위험성을 분석하지 말고 계약서 내용만 추출하세요.
"""
            },
            {
                "type": "image_url",
                "image_url": {
                    "url": (
                        f"data:{mime_type};"
                        f"base64,{image_base64}"
                    )
                }
            }
        ]
    )

    response = llm.invoke([message])

    return response.content


# --------------------------------------------------
# 검색 문서 정리 함수
# --------------------------------------------------

def format_retrieved_documents(documents):
    formatted_text = []

    for doc in documents:
        source = doc.metadata.get(
            "source",
            "출처 없음"
        )

        page = doc.metadata.get(
            "page",
            "페이지 없음"
        )

        formatted_text.append(
            f"[출처: {source}, {page}페이지]\n"
            f"{doc.page_content}"
        )

    return "\n\n".join(formatted_text)


# --------------------------------------------------
# 모델과 VectorDB 불러오기
# --------------------------------------------------

@st.cache_resource(show_spinner=False)
def load_models():
    embedding_model = HuggingFaceEmbeddings(
        model_name=(
            "sentence-transformers/"
            "paraphrase-multilingual-MiniLM-L12-v2"
        ),
        model_kwargs={
            "device": "cpu"
        },
        encode_kwargs={
            "normalize_embeddings": True
        }
    )

    vectorstore = FAISS.load_local(
        "./vectorstore/housing_faiss",
        embedding_model,
        allow_dangerous_deserialization=True
    )

    llm = ChatOpenAI(
        model="gpt-4.1-mini",
        temperature=0
    )

    return vectorstore, llm


# --------------------------------------------------
# 계약서 분석 프롬프트
# --------------------------------------------------

contract_analysis_prompt = ChatPromptTemplate.from_messages([
    (
        "system",
        """
당신은 외국인 유학생과 사회초년생을 돕는
주거 계약서 분석 AI입니다.

제공된 계약서와 주거 안내 참고 자료를 비교하여 분석하세요.

단순 번역이 아니라 사용자가 계약 내용을 이해하고
위험을 확인할 수 있도록 설명하세요.

다음 형식을 반드시 지키세요.

## 1. 계약 핵심 정보
- 임대인:
- 임차인:
- 주소:
- 보증금:
- 월세:
- 관리비:
- 계약 기간:
- 입주일:
- 계약 해지 조건:

계약서에 없는 정보는
"확인되지 않음"이라고 작성하세요.

## 2. 주의가 필요한 조항
각 조항에 대해 다음 내용을 작성하세요.

- 위험도: 낮음 / 보통 / 높음
- 계약서 원문
- 주의해야 하는 이유
- 계약 전 확인할 질문

## 3. 누락되거나 불명확한 내용
추가 확인이 필요한 부분을 작성하세요.

## 4. 계약 전 확인 목록
사용자가 실제로 확인해야 할 내용을
체크리스트 형태로 작성하세요.

## 5. 종합 의견
계약을 진행하기 전에 가장 중요하게
확인해야 할 내용을 설명하세요.

## 6. 참고 문서
참고한 문서의 파일명과 페이지를 작성하세요.

마지막에 반드시 다음 문장을 표시하세요.

본 결과는 법률 자문이 아닌 참고용 AI 분석입니다.
"""
    ),
    (
        "human",
        """
답변 언어:
{language}

분석할 계약서:
{contract}

관련 주거 안내 참고 자료:
{context}
"""
    )
])


# --------------------------------------------------
# RAG 질문 프롬프트
# --------------------------------------------------

rag_prompt = ChatPromptTemplate.from_messages([
    (
        "system",
        """
당신은 외국인 유학생과 사회초년생을 돕는
주거 계약 안내 AI입니다.

반드시 제공된 참고 문서를 근거로 답변하세요.

답변 규칙:
1. 사용자가 선택한 언어로 답변합니다.
2. 어려운 용어를 쉽게 설명합니다.
3. 참고 문서에 없는 내용은 추측하지 않습니다.
4. 확실하지 않은 내용은 확인이 필요하다고 안내합니다.
5. 답변 마지막에 참고한 파일명과 페이지를 표시합니다.
6. 법률 자문이 아닌 참고 정보임을 안내합니다.
"""
    ),
    (
        "human",
        """
답변 언어:
{language}

사용자 질문:
{question}

검색된 참고 문서:
{context}
"""
    )
])


# --------------------------------------------------
# 상단 소개 영역
# --------------------------------------------------

st.markdown(
    """
    <div class="hero-box">
        <div class="hero-badge">
            LangChain · RAG · Multilingual AI
        </div>

        <div class="hero-title">
            🏠 HomeSafe AI
        </div>

        <p class="hero-description">
            외국인 유학생과 사회초년생을 위한
            다국어 주거 계약서 검토 서비스입니다.<br>
            계약서의 주요 조건을 정리하고,
            참고 문서를 근거로 주의가 필요한 조항을 안내합니다.
        </p>
    </div>
    """,
    unsafe_allow_html=True
)


# --------------------------------------------------
# 서비스 기능 설명
# --------------------------------------------------

feature_col1, feature_col2, feature_col3 = st.columns(3)

with feature_col1:
    st.markdown(
        """
        <div class="feature-box">
            <div class="feature-icon">📑</div>
            <div class="feature-title">
                계약 조건 자동 정리
            </div>
            <div class="feature-text">
                보증금, 월세, 계약 기간,
                관리비와 해지 조건을 빠르게 정리합니다.
            </div>
        </div>
        """,
        unsafe_allow_html=True
    )

with feature_col2:
    st.markdown(
        """
        <div class="feature-box">
            <div class="feature-icon">⚠️</div>
            <div class="feature-title">
                위험 조항 확인
            </div>
            <div class="feature-text">
                원상복구, 중도 해지, 수선 비용 등
                주의할 내용을 쉽게 설명합니다.
            </div>
        </div>
        """,
        unsafe_allow_html=True
    )

with feature_col3:
    st.markdown(
        """
        <div class="feature-box">
            <div class="feature-icon">🌍</div>
            <div class="feature-title">
                다국어 답변
            </div>
            <div class="feature-text">
                한국어, 영어, 일본어, 중국어 중
                원하는 언어로 결과를 확인할 수 있습니다.
            </div>
        </div>
        """,
        unsafe_allow_html=True
    )

st.write("")


# --------------------------------------------------
# 모델 준비
# --------------------------------------------------

try:
    with st.spinner(
        "AI 분석 환경을 준비하고 있습니다."
    ):
        vectorstore, llm = load_models()

except Exception as error:
    st.error(
        "AI 모델 또는 VectorDB를 불러오지 못했습니다."
    )
    st.code(str(error))
    st.stop()


# --------------------------------------------------
# 계약서 분석 입력 영역
# --------------------------------------------------

with st.container(border=True):
    st.markdown(
        """
        <span class="section-number">1</span>
        <span class="section-title">
            분석 설정
        </span>

        <div class="section-description">
            결과를 확인할 언어를 선택한 뒤,
            계약서 PDF 또는 촬영한 이미지를 업로드하세요.
        </div>
        """,
        unsafe_allow_html=True
    )

    language = st.selectbox(
        "답변 언어",
        [
            "한국어",
            "English",
            "日本語",
            "中文"
        ],
        label_visibility="collapsed"
    )

    uploaded_file = st.file_uploader(
        "계약서 파일 업로드",
        type=[
            "pdf",
            "png",
            "jpg",
            "jpeg"
        ],
        help=(
            "텍스트 PDF 또는 휴대폰으로 촬영한 "
            "PNG, JPG 이미지를 지원합니다."
        )
    )

    st.markdown(
        """
        <div class="notice-box">
            사진을 업로드할 때는 문서를 정면에서 촬영하고,
            글자가 선명하게 보이도록 해주세요.
            흐리거나 잘린 부분은 정확하게 분석되지 않을 수 있습니다.
        </div>
        """,
        unsafe_allow_html=True
    )


# --------------------------------------------------
# 업로드 파일 확인
# --------------------------------------------------

if uploaded_file is not None:
    file_extension = (
        uploaded_file.name
        .lower()
        .split(".")[-1]
    )

    st.success(
        f"업로드 완료: {uploaded_file.name}"
    )

    if file_extension in [
        "png",
        "jpg",
        "jpeg"
    ]:
        with st.expander(
            "업로드한 이미지 미리보기"
        ):
            st.image(
                uploaded_file,
                use_container_width=True
            )


# --------------------------------------------------
# 계약서 분석
# --------------------------------------------------

with st.container(border=True):
    st.markdown(
        """
        <span class="section-number">2</span>
        <span class="section-title">
            계약서 분석
        </span>

        <div class="section-description">
            업로드한 계약서와 주거 안내 자료를 비교하여
            핵심 조건과 주의사항을 분석합니다.
        </div>
        """,
        unsafe_allow_html=True
    )

    analyze_button = st.button(
        "계약서 분석 시작",
        type="primary",
        use_container_width=True,
        disabled=uploaded_file is None
    )

    if uploaded_file is None:
        st.info(
            "먼저 계약서 파일을 업로드하세요."
        )


if analyze_button and uploaded_file is not None:
    with st.status(
        "계약서를 분석하고 있습니다.",
        expanded=True
    ) as status:

        try:
            st.write("계약서 내용을 읽고 있습니다.")

            file_extension = (
                uploaded_file.name
                .lower()
                .split(".")[-1]
            )

            if file_extension == "pdf":
                contract_pages = (
                    extract_uploaded_pdf_pages(
                        uploaded_file
                    )
                )

                if not contract_pages:
                    st.error(
                        "PDF에서 글자를 추출하지 못했습니다. "
                        "스캔 이미지 PDF일 가능성이 있습니다."
                    )
                    st.stop()

                contract_text = "\n\n".join(
                    [
                        (
                            f"[계약서 "
                            f"{doc.metadata['page']}페이지]\n"
                            f"{doc.page_content}"
                        )
                        for doc in contract_pages
                    ]
                )

            else:
                contract_text = (
                    extract_uploaded_image_text(
                        uploaded_file,
                        llm
                    )
                )

                if not contract_text.strip():
                    st.error(
                        "이미지에서 계약서 내용을 읽지 못했습니다."
                    )
                    st.stop()

            st.write("관련 주거 안내 자료를 검색하고 있습니다.")

            search_question = """
주택 임대차 계약에서 확인해야 할 사항,
보증금 보호, 관리비, 계약 해지, 수선 비용,
원상복구, 특약사항, 임대인 권한 확인
"""

            reference_docs = (
                vectorstore.similarity_search(
                    search_question,
                    k=5
                )
            )

            contract_context = (
                format_retrieved_documents(
                    reference_docs
                )
            )

            st.write("계약 조건과 위험 조항을 분석하고 있습니다.")

            messages = (
                contract_analysis_prompt
                .format_messages(
                    language=language,
                    contract=contract_text,
                    context=contract_context
                )
            )

            contract_analysis = (
                llm.invoke(messages)
            )

            st.session_state[
                "contract_analysis"
            ] = contract_analysis.content

            st.session_state[
                "contract_file_name"
            ] = uploaded_file.name

            status.update(
                label="계약서 분석이 완료되었습니다.",
                state="complete",
                expanded=False
            )

        except Exception as error:
            status.update(
                label="분석 중 오류가 발생했습니다.",
                state="error",
                expanded=True
            )

            st.code(str(error))


# --------------------------------------------------
# 계약서 분석 결과
# --------------------------------------------------

if "contract_analysis" in st.session_state:
    st.markdown(
        """
        <div class="result-header">
            <div class="result-title">
                ✅ 계약서 분석 결과
            </div>

            <div class="result-text">
                AI가 정리한 계약 조건과
                주의가 필요한 내용을 확인하세요.
            </div>
        </div>
        """,
        unsafe_allow_html=True
    )

    with st.container(border=True):
        st.markdown(
            st.session_state["contract_analysis"]
        )

    download_text = f"""
프로젝트명: {PROJECT_NAME}
이름: {STUDENT_NAME}
분석 파일: {st.session_state.get(
    "contract_file_name",
    "확인되지 않음"
)}

========================================
계약서 분석 결과
========================================

{st.session_state["contract_analysis"]}
"""

    st.download_button(
        label="분석 결과 TXT로 내려받기",
        data=download_text,
        file_name="contract_analysis_result.txt",
        mime="text/plain",
        use_container_width=True
    )


# --------------------------------------------------
# RAG 질문 기능
# --------------------------------------------------

with st.container(border=True):
    st.markdown(
        """
        <span class="section-number">3</span>
        <span class="section-title">
            주거 계약 질문
        </span>

        <div class="section-description">
            계약 전 확인사항이나 보증금,
            관리비, 해지 조건에 관해 질문해보세요.
        </div>
        """,
        unsafe_allow_html=True
    )

    question = st.text_input(
        "질문 입력",
        placeholder=(
            "예: 중도 퇴거 조항에서 "
            "주의해야 할 부분은 무엇인가요?"
        ),
        label_visibility="collapsed"
    )

    question_button = st.button(
        "관련 자료를 검색하여 답변받기",
        use_container_width=True
    )


if question_button:
    if not question.strip():
        st.warning(
            "질문 내용을 입력하세요."
        )

    else:
        with st.spinner(
            "관련 안내 자료를 검색하고 있습니다."
        ):
            try:
                retrieved_docs = (
                    vectorstore.similarity_search(
                        question,
                        k=3
                    )
                )

                context = (
                    format_retrieved_documents(
                        retrieved_docs
                    )
                )

                messages = (
                    rag_prompt.format_messages(
                        language=language,
                        question=question,
                        context=context
                    )
                )

                response = llm.invoke(messages)

                st.markdown(
                    """
                    <div class="result-header">
                        <div class="result-title">
                            💬 AI 안내 답변
                        </div>

                        <div class="result-text">
                            등록된 주거 안내 자료를
                            근거로 생성한 답변입니다.
                        </div>
                    </div>
                    """,
                    unsafe_allow_html=True
                )

                with st.container(border=True):
                    st.markdown(
                        response.content
                    )

            except Exception as error:
                st.error(
                    "답변 생성 중 오류가 발생했습니다."
                )

                st.code(str(error))


# --------------------------------------------------
# 하단 안내
# --------------------------------------------------

st.markdown(
    f"""
    <div class="footer-box">
        <strong>{PROJECT_NAME}</strong><br>
        제작자: {STUDENT_NAME}<br>
        본 서비스의 분석 결과는
        법률 전문가의 자문을 대신하지 않습니다.
    </div>
    """,
    unsafe_allow_html=True
)
