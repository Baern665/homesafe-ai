"""Safe Markdown and CJK PDF rendering for bounded HomeSafe reports."""
from __future__ import annotations
import re
from typing import Any
from urllib.parse import urlsplit
import pymupdf as fitz

PAGE_W, PAGE_H = 595.276, 841.890
MARGIN_X, MARGIN_TOP, MARGIN_BOTTOM = 48.0, 48.0, 48.0
FONT_SIZE, LINE_GAP = 10.0, 4.0
MAX_TEXT, MAX_LIST_ITEMS, MAX_SOURCES = 12000, 30, 12
PDF_FONT_NAME = "hs_cjk"
FIELD_LABELS = {"보증금":"보증금","월세":"월세","관리비":"관리비","계약 기간":"계약 기간","입주일":"입주일","해지 조건":"해지 조건","deposit":"보증금","monthly_rent":"월세","maintenance_fee":"관리비","duration":"계약 기간","move_in":"입주일","termination":"해지 조건"}
RISK_LEVELS = {"low":"낮음","medium":"보통","high":"높음","unknown":"확인 필요"}

def _text(value: Any, name: str, limit: int = MAX_TEXT) -> str:
    if not isinstance(value, str): raise ValueError(f"{name} must be a string")
    if len(value) > limit: raise ValueError(f"{name} exceeds {limit} characters")
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    return "".join(ch for ch in value if ch in "\n\t" or (ord(ch) >= 32 and ord(ch) != 127))

def _items(value: Any, name: str, count: int = MAX_LIST_ITEMS, item_limit: int = 2000) -> list[str]:
    if not isinstance(value, list) or len(value) > count: raise ValueError(f"{name} must be a bounded list")
    return [_text(v, name + " item", item_limit) for v in value]

def _validate_report(report: dict) -> dict:
    if not isinstance(report, dict): raise ValueError("report must be a dictionary")
    required = {"summary","fields","risks","missing","checklist","limitations","sources","pages_total","pages_missing"}
    if required.difference(report): raise ValueError("report missing required keys: " + ", ".join(sorted(required.difference(report))))
    out = dict(report)
    out["summary"] = _text(report["summary"], "summary", 2400)
    fields = report["fields"]
    if not isinstance(fields, dict) or len(fields) > 6: raise ValueError("fields must have at most six entries")
    normalized = {}
    for key, value in fields.items():
        if key not in FIELD_LABELS: raise ValueError("unsupported financial field label")
        normalized[FIELD_LABELS[key]] = _text(value, FIELD_LABELS[key], 1200)
    out["fields"] = normalized
    risks = report["risks"]
    if not isinstance(risks, list) or len(risks) > 15: raise ValueError("risks must contain at most 15 entries")
    safe_risks = []
    for risk in risks:
        if not isinstance(risk, dict) or not {"title","level","quote","page","reason","questions","source_ids"}.issubset(risk): raise ValueError("invalid risk entry")
        if risk["level"] not in RISK_LEVELS: raise ValueError("invalid risk level")
        if type(risk["page"]) is not int or risk["page"] < 1: raise ValueError("risk page must be positive")
        safe_risks.append({"title":_text(risk["title"],"risk title",200),"level":risk["level"],"quote":_text(risk["quote"],"risk quote",1800),"page":risk["page"],"reason":_text(risk["reason"],"risk reason",1800),"questions":_items(risk["questions"],"questions",6,1200),"source_ids":_items(risk["source_ids"],"risk source_ids",6,100)})
    out["risks"] = safe_risks
    for key, cap in (("missing",15),("checklist",20),("limitations",15)): out[key] = _items(report[key],key,cap,2000)
    if type(report["pages_total"]) is not int or not 1 <= report["pages_total"] <= 12: raise ValueError("invalid pages_total")
    out["pages_total"] = report["pages_total"]
    missing_pages = report["pages_missing"]
    if (not isinstance(missing_pages, list) or len(missing_pages) > 12 or
        any(type(p) is not int or not 1 <= p <= report["pages_total"] for p in missing_pages) or
        len(set(missing_pages)) != len(missing_pages)):
        raise ValueError("invalid pages_missing")
    out["pages_missing"] = missing_pages
    if not isinstance(report["sources"],list) or len(report["sources"]) > MAX_SOURCES: raise ValueError("sources must contain at most 12 entries")
    safe_sources, seen = [], set()
    for source in report["sources"]:
        if not isinstance(source,dict) or not {"id","title","url","reviewed_at","kind"}.issubset(source): raise ValueError("invalid source metadata")
        sid = _text(source["id"],"source id",100)
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}",sid) or sid in seen: raise ValueError("invalid or duplicate source ID")
        seen.add(sid)
        url = _text(source["url"],"source URL",2048); parts = urlsplit(url)
        if parts.scheme != "https" or not parts.netloc or parts.username or parts.password: raise ValueError("source URL must be HTTPS")
        reviewed = _text(source["reviewed_at"],"reviewed_at",10)
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}",reviewed): raise ValueError("reviewed_at must be an ISO date")
        from datetime import date
        try: date.fromisoformat(reviewed)
        except ValueError: raise ValueError("reviewed_at must be an ISO date") from None
        kind = _text(source["kind"],"source kind",60)
        if kind not in {"official-guidance", "official-service"}: raise ValueError("source kind must identify official guidance or service")
        safe_sources.append({"id":sid,"title":_text(source["title"],"source title",300),"url":url,"reviewed_at":reviewed,"kind":kind})
    out["sources"] = safe_sources
    allowed_source_ids = {source["id"] for source in safe_sources}
    for risk in out["risks"]:
        if risk["page"] > out["pages_total"]:
            raise ValueError("risk page is outside the document")
        if any(source_id not in allowed_source_ids for source_id in risk["source_ids"]):
            raise ValueError("risk references an unknown source ID")
    return out

def _disclaimer(language: str) -> str:
    try:
        from hs_core import disclaimer as core_disclaimer
        value = core_disclaimer(language)
        if isinstance(value,str) and value.strip(): return _text(value,"disclaimer",2000)
    except (ImportError,AttributeError): pass
    return "안내: 이 보고서는 정보 제공용이며 법률 자문이나 법률 의견이 아닙니다. 계약, 조항, 등기, 권리관계, 보증 또는 보증금의 안전을 보장하지 않습니다. 개별 사안은 최신 공식 안내와 담당 기관 또는 자격을 갖춘 전문가에게 확인하세요."

def _md_escape(s: str) -> str:
    s = s.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;").replace(chr(96),"\\"+chr(96))
    return re.sub(r"([\\*_{}\[\]()#+.!|>-])", r"\\\1", s)

def _md_block(s: str) -> str: return "\n".join(_md_escape(line) for line in s.split("\n"))

def report_to_markdown(report: dict, language: str = "한국어") -> str:
    """Render complete bounded report as escaped, non-HTML Markdown."""
    d = _validate_report(report); language = _text(language,"language",80)
    lines = ["# 계약 검토 보고서","","## 요약",_md_block(d["summary"]),"","## 주요 금액 및 조건"]
    for label,value in d["fields"].items(): lines.append(f"- **{_md_escape(label)}**: {_md_block(value)}")
    lines.extend(["","## 검토 사항"])
    if not d["risks"]: lines.append("- 표시된 검토 사항이 없습니다.")
    for r in d["risks"]:
        lines.extend([f"### {_md_escape(r['title'])} ({RISK_LEVELS[r['level']]})",f"- 원문 (p. {r['page']}): {_md_block(r['quote'])}",f"- 설명: {_md_block(r['reason'])}"])
        lines.extend(f"- 확인 질문: {_md_block(q)}" for q in r["questions"])
        if r["source_ids"]: lines.append("- 출처 ID: " + ", ".join(_md_escape(x) for x in r["source_ids"]))
    for key,title in (("missing","확인되지 않은 정보"),("checklist","확인 목록"),("limitations","한계")):
        lines.extend(["","## "+title]); lines.extend(["- "+_md_block(x) for x in d[key]] or ["- 없음"])
    lines.extend(["","## 출처"])
    for s in d["sources"]: lines.append(f"- {_md_escape(s['title'])} (ID: {_md_escape(s['id'])}; {_md_escape(s['kind'])}; 검토일: {s['reviewed_at']}) - {_md_escape(s['url'])}")
    lines.extend(["",f"문서 페이지: {d['pages_total']} | 누락 페이지: {', '.join(str(p) for p in d['pages_missing']) if d['pages_missing'] else '없음'}","","## 안내",_md_block(_disclaimer(language))])
    return "\n".join(lines)+"\n"

def _wrap(text: str, font: fitz.Font, max_width: float) -> list[str]:
    lines=[]
    for paragraph in text.split("\n"):
        if not paragraph: lines.append(""); continue
        current=""
        for char in paragraph:
            candidate=current+char
            if current and font.text_length(candidate,fontsize=FONT_SIZE)>max_width: lines.append(current); current=char
            else: current=candidate
        lines.append(current)
    return lines

def report_to_pdf(report: dict, language: str = "한국어") -> bytes:
    """Create readable, text-only A4 CJK PDF using PyMuPDF's bundled Korea font."""
    d=_validate_report(report); language=_text(language,"language",80)
    # Measure and render using the exact same bundled font bytes. The built-in
    # CJK alias otherwise measures Latin text differently from PDF insertion.
    font=fitz.Font("korea")
    doc=fitz.open(); page=doc.new_page(width=PAGE_W,height=PAGE_H)
    page.insert_font(fontname=PDF_FONT_NAME,fontbuffer=font.buffer)
    usable=PAGE_W-2*MARGIN_X; bottom=PAGE_H-MARGIN_BOTTOM; y=MARGIN_TOP
    def ensure_space(height=FONT_SIZE+LINE_GAP):
        nonlocal page,y
        if y+height>bottom:
            page=doc.new_page(width=PAGE_W,height=PAGE_H); page.insert_font(fontname=PDF_FONT_NAME,fontbuffer=font.buffer); y=MARGIN_TOP
    def draw(text,gap=0):
        nonlocal y
        y+=gap
        for line in _wrap(text,font,usable):
            ensure_space(); page.insert_text((MARGIN_X,y+FONT_SIZE),line,fontname=PDF_FONT_NAME,fontsize=FONT_SIZE,color=(0.08,0.08,0.08)); y+=FONT_SIZE+LINE_GAP
    def heading(text):
        nonlocal y
        y+=4; draw(text,2); y+=2
    draw("계약 검토 보고서"); heading("요약"); draw(d["summary"] or "(내용 없음)")
    heading("주요 금액 및 조건")
    for k,v in d["fields"].items(): draw(f"{k}: {v}")
    heading("검토 사항")
    if not d["risks"]: draw("표시된 검토 사항이 없습니다.")
    for i,r in enumerate(d["risks"],1):
        draw(f"{i}. {r['title']} [{RISK_LEVELS[r['level']]}]"); draw(f"원문 (p. {r['page']}): {r['quote']}"); draw("설명: "+r["reason"])
        for q in r["questions"]: draw("확인 질문: "+q)
        if r["source_ids"]: draw("출처 ID: "+", ".join(r["source_ids"]))
    for key,title in (("missing","확인되지 않은 정보"),("checklist","확인 목록"),("limitations","한계")):
        heading(title)
        for item in d[key] or ["없음"]: draw("- "+item)
    heading("출처 및 검토일")
    for s in d["sources"]:
        draw(f"{s['title']} (ID: {s['id']}; {s['kind']}; 검토일: {s['reviewed_at']})"); draw("URL: "+s["url"])
    draw(f"문서 페이지: {d['pages_total']} | 누락 페이지: {', '.join(str(p) for p in d['pages_missing']) if d['pages_missing'] else '없음'}")
    heading("안내"); draw(_disclaimer(language))
    total=len(doc)
    for i,pg in enumerate(doc,1): pg.insert_text((PAGE_W-MARGIN_X-70,PAGE_H-20),f"{i} / {total}",fontname=PDF_FONT_NAME,fontsize=9,color=(0.25,0.25,0.25))
    result=doc.tobytes(garbage=4,deflate=True); doc.close(); return result
