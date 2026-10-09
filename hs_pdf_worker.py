"""Isolated PDF parser. stdin PDF, stdout bounded JSON; never receives AI credentials."""
from __future__ import annotations
import sys
import json
import base64
import re

def main():
    try:
        if sys.platform != "win32":
            import resource
            resource.setrlimit(resource.RLIMIT_AS, (384*1024*1024,384*1024*1024))
            resource.setrlimit(resource.RLIMIT_CPU, (12,14))
        import pymupdf
        pymupdf.TOOLS.mupdf_display_errors(False)
        pymupdf.TOOLS.mupdf_display_warnings(False)
        from hs_core import MAX_BYTES, MAX_PAGES, MAX_CHARS
        data=sys.stdin.buffer.read(MAX_BYTES+1)
        if not data or len(data)>MAX_BYTES:
            raise ValueError("PDF 크기를 확인하세요.")
        permit_ocr="--ocr" in sys.argv
        pages=[];total_text=0;total_images=0
        with pymupdf.open(stream=data,filetype="pdf") as pdf:
            if pdf.is_encrypted:
                raise ValueError("암호화된 PDF는 지원하지 않습니다. 암호를 해제한 사본을 사용하세요.")
            if not 1 <= len(pdf) <= MAX_PAGES:
                raise ValueError("전체 문서는 최대 12페이지까지 지원합니다.")
            for ordinal,page in enumerate(pdf,start=1):
                text=page.get_text("text",sort=True).strip()
                total_text+=len(text)
                if total_text>MAX_CHARS:
                    raise ValueError("문서가 35,000자를 초과해 중단했습니다.")
                image=None
                if len(re.sub(r"\s","",text))<30 or page.get_images():
                    if not permit_ocr:
                        raise ValueError(f"{ordinal}페이지에 이미지나 부족한 텍스트가 있습니다. 일부 조항이 누락될 수 있어 분석을 중단했습니다. 원본 전송 동의 후 OCR하거나 직접 입력하세요.")
                    if page.rect.width<=0 or page.rect.height<=0:
                        raise ValueError("PDF 페이지 크기가 올바르지 않습니다.")
                    scale=min(1.6,2000/page.rect.width,2000/page.rect.height)
                    bitmap=page.get_pixmap(matrix=pymupdf.Matrix(scale,scale),alpha=False).tobytes("png")
                    total_images+=len(bitmap)
                    if total_images>24*1024*1024:
                        raise ValueError("스캔 이미지 처리량이 너무 큽니다. 문서를 나눠 주세요.")
                    image=base64.b64encode(bitmap).decode("ascii")
                    text=""
                pages.append({"text":text,"image":image})
        result={"ok":True,"pages":pages}
    except ValueError as error:
        result={"ok":False,"error":str(error)[:350]}
    except Exception:
        result={"ok":False,"error":"PDF가 손상되었거나 처리 한도를 초과했습니다. 오류 코드: PDF-PARSE"}
    sys.stdout.write(json.dumps(result,ensure_ascii=True))

if __name__=="__main__":main()
