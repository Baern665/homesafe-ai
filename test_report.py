import unittest

import pymupdf as fitz

from hs_report import report_to_markdown, report_to_pdf


class ReportRenderingTests(unittest.TestCase):
    def make_report(self, language="한국어", long=False):
        clause = "임대인은 계약 종료 시 보증금을 반환한다. 원문 그대로 보존합니다."
        explanation = "확인 필요: 반환 시점과 공제 조건을 담당 기관 또는 전문가에게 확인하세요."
        if long:
            clause = clause + "\n" + "\n".join(["특약 내용은 문맥을 포함해 읽어야 합니다."] * 60)
            explanation = explanation + "\n" + "\n".join(["계약서에 기재된 범위와 실제 조건을 함께 확인하세요."] * 30)
        return {
            "summary": f"{language} summary: 계약 내용을 확인하세요.",
            "fields": {
                "보증금": "₩10,000,000",
                "월세": "₩500,000",
                "관리비": "확인되지 않음",
                "계약 기간": "2026-11-01부터 1년",
                "입주일": "2026-11-01",
                "해지 조건": "서면 확인 필요",
            },
            "risks": [{
                "title": "보증금 반환 조건",
                "level": "medium",
                "quote": clause,
                "page": 2,
                "reason": explanation,
                "questions": ["공제 항목과 반환일이 명시되어 있나요?"],
                "source_ids": ["guide-lease-1"],
            }],
            "missing": ["등기부·소유자 정보 미확인"],
            "checklist": ["계약 상대방의 신분과 권한을 확인하세요."],
            "limitations": ["등기, 채무, 부동산 가치 및 신원은 검증하지 않았습니다."],
            "sources": [{
                "id": "guide-lease-1", "title": "임대차 공식 안내", "kind": "official-guidance",
                "url": "https://example.gov.kr/guidance/lease", "reviewed_at": "2026-10-09",
            }],
            "pages_total": 4,
            "pages_missing": [3],
        }

    def test_pdf_language_variants_and_extractable_content(self):
        for language in ("한국어", "English", "日本語", "中文"):
            with self.subTest(language=language):
                data = report_to_pdf(self.make_report(language), language)
                self.assertTrue(data.startswith(b"%PDF-"))
                doc = fitz.open(stream=data, filetype="pdf")
                text = "\n".join(p.get_text() for p in doc)
                self.assertIn("계약 검토 보고서", text)
                self.assertIn("보증금", text)
                self.assertIn("임대인은 계약 종료 시 보증금을 반환한다.", text)
                self.assertIn("guide-lease-1", text)
                self.assertIn("https://example.gov.kr/guidance/lease", text)
                self.assertIn("2026-10-09", text)
                self.assertIn("안내", text)
                self.assertGreaterEqual(len(doc), 1)
                doc.close()

    def test_multiline_long_clause_flows_across_pages_and_bounds(self):
        pdf = fitz.open(stream=report_to_pdf(self.make_report(long=True)), filetype="pdf")
        self.assertGreater(len(pdf), 1)
        full_text = "\n".join(page.get_text() for page in pdf)
        self.assertIn("특약 내용은 문맥을 포함해 읽어야 합니다.", full_text)
        self.assertIn("계약서에 기재된 범위와 실제 조건을 함께 확인하세요.", full_text)
        for page in pdf:
            rect = page.rect
            for block in page.get_text("dict")["blocks"]:
                for line in block.get("lines", []):
                    for span in line["spans"]:
                        x0, y0, x1, y1 = span["bbox"]
                        self.assertGreaterEqual(x0, -0.5)
                        self.assertGreaterEqual(y0, -0.5)
                        self.assertLessEqual(x1, rect.width + 0.5)
                        self.assertLessEqual(y1, rect.height + 0.5)
        pdf.close()

    def test_markdown_escapes_untrusted_markup_and_preserves_literal_data(self):
        report = self.make_report()
        report["summary"] = '<script>alert("x")</script> **not a command** [x](javascript:alert(1))'
        report["risks"][0]["quote"] = '원문 <img src=x onerror="alert(1)"> `literal`'
        markdown = report_to_markdown(report, "English")
        self.assertNotIn("<script>", markdown)
        self.assertNotIn("<img", markdown)
        self.assertIn("&lt;script&gt;", markdown)
        self.assertIn("&lt;img", markdown)
        self.assertIn("javascript:alert", markdown)  # Data remains visible, not interpreted by this renderer.
        self.assertIn("literal", markdown)

    def test_pdf_treats_markup_as_plain_text_and_keeps_original_quote(self):
        report = self.make_report()
        report["risks"][0]["quote"] = '<script>alert("x")</script> 원문 계약 문구'
        doc = fitz.open(stream=report_to_pdf(report), filetype="pdf")
        text = "\n".join(page.get_text() for page in doc)
        self.assertIn("<script>alert(\"x\")</script> 원문 계약 문구", text)
        doc.close()

    def test_oversized_content_rejected_without_silent_truncation(self):
        report = self.make_report()
        report["summary"] = "x" * 2401
        with self.assertRaises(ValueError):
            report_to_pdf(report)
        with self.assertRaises(ValueError):
            report_to_markdown(report)


if __name__ == "__main__":
    unittest.main()
