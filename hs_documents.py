"""Consent-first bounded document extraction. Does not persist upload data."""
from io import BytesIO
import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from PIL import Image
from hs_core import MAX_BYTES, MAX_PAGES, MAX_CHARS, validate_upload, redact_pii

def extract_contract_files(files: list[dict], ocr=None, ocr_allowed: bool = False):
    if not files or len(files) > MAX_PAGES or sum(len(f["data"]) for f in files) > MAX_BYTES:
        raise ValueError("전체 파일은 10MB 이하, 최대 12개로 업로드하세요.")
    pages, warnings = [], []
    def scan(data, mime, ordinal):
        if not ocr_allowed or ocr is None:
            raise ValueError(f"{ordinal}페이지에 이미지나 부족한 텍스트가 있습니다. 일부 조항이 누락될 수 있어 분석을 중단했습니다. 원본 전송 동의 후 OCR하거나 직접 입력하세요.")
        text = ocr(data, mime)
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"{ordinal}페이지 인식에 실패했습니다. 직접 입력하거나 원본을 확인하세요.")
        warnings.append(f"{ordinal}페이지는 AI OCR로 읽었습니다. 원문·금액·날짜를 직접 확인하세요.")
        return text
    for file in files:
        data = file["data"]
        kind = validate_upload(file["name"], data)
        if kind == "pdf":
            extracted = parse_pdf_isolated(data, ocr_allowed and ocr is not None)
            if len(pages)+len(extracted)>MAX_PAGES:
                raise ValueError("전체 문서는 최대 12페이지까지 지원합니다.")
            for entry in extracted:
                ordinal=len(pages)+1
                text=scan(base64.b64decode(entry["image"]),"image/png",ordinal) if entry["image"] else entry["text"]
                if not text.strip():
                    raise ValueError(f"{ordinal}페이지를 읽지 못했습니다. 직접 입력하세요.")
                pages.append({"page":ordinal,"text":redact_pii(text)})
                if sum(len(p['text']) for p in pages)>MAX_CHARS:
                    raise ValueError("문서가 35,000자를 초과해 중단했습니다. 필요한 부분만 분리해 주세요.")

        else:
            if not ocr_allowed or ocr is None:
                raise ValueError("사진 인식에는 원본의 외부 AI 전송 동의와 AI 설정이 필요합니다. 개인정보를 가린 사본을 권장합니다.")
            try:
                with Image.open(BytesIO(data)) as image:
                    if image.width * image.height > 20_000_000:
                        raise ValueError("사진은 2,000만 픽셀 이하로 줄여 업로드하세요.")
                    image.verify()
            except ValueError:
                raise
            except Exception:
                raise ValueError("사진이 손상되었거나 지원하지 않는 이미지입니다.") from None
            text = scan(data, "image/png" if kind=="png" else "image/jpeg",len(pages)+1)
            pages.append({"page":len(pages)+1,"text":redact_pii(text)})
        if sum(len(p['text']) for p in pages) > MAX_CHARS:
            raise ValueError("문서가 35,000자를 초과해 중단했습니다. 필요한 부분만 분리해 주세요.")
    return pages, warnings

_PDF_LOCK=threading.BoundedSemaphore(1)

def parse_pdf_isolated(data:bytes, permit_ocr:bool=False):
    if os.name != "posix" and os.environ.get("HOMESAFE_TRUSTED_TEST_UPLOADS") != "1":
        raise ValueError("PDF 업로드는 자원 격리를 지원하는 Linux 배포에서만 제공됩니다. 텍스트 직접 입력을 이용하세요.")
    if not _PDF_LOCK.acquire(blocking=False):
        raise ValueError("다른 PDF를 처리하고 있습니다. 잠시 후 다시 시도하세요.")
    try:
        command=[sys.executable,str(Path(__file__).with_name("hs_pdf_worker.py"))]
        if permit_ocr: command.append("--ocr")
        safe_env={k:v for k,v in os.environ.items() if k.upper() in {"PATH","SYSTEMROOT","WINDIR","LANG","LC_ALL","PYTHONUTF8","TMPDIR","TEMP","TMP"}}
        try:
            completed=subprocess.run(command,input=data,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=20,env=safe_env,check=False)
        except (subprocess.TimeoutExpired,OSError):
            raise ValueError("PDF 처리 시간·자원 한도를 초과했습니다. 문서를 나누거나 직접 입력하세요. 오류 코드: PDF-LIMIT") from None
        if completed.returncode!=0 or len(completed.stdout)>34*1024*1024:
            raise ValueError("PDF 처리 한도를 초과했습니다. 오류 코드: PDF-LIMIT")
        try:
            result=json.loads(completed.stdout)
        except (ValueError,TypeError):
            raise ValueError("PDF를 읽지 못했습니다. 오류 코드: PDF-PARSE") from None
        if not result.get("ok"):
            raise ValueError(result.get("error","PDF를 읽지 못했습니다."))
        return result["pages"]
    finally:
        _PDF_LOCK.release()
