"""Offline localization and consent regression tests; no live AI or user documents."""
import re
import unittest
from pathlib import Path
from unittest.mock import patch
from streamlit.testing.v1 import AppTest
from hs_i18n import LANGUAGES, LocalizedUI, catalog, translate, translate_error
from hs_core import disclaimer
from hs_report import report_to_markdown
import test_report

APP = Path(__file__).with_name("app.py")

def button(app, label):
    return next(b for b in app.button if b.label == label)

class CatalogTests(unittest.TestCase):
    def test_five_languages_and_spanish_alias(self):
        self.assertEqual(LANGUAGES, ["한국어", "English", "日本語", "中文", "Español"])
        self.assertEqual(disclaimer("es"), disclaimer("Español"))
        self.assertIn("no asesoramiento", disclaimer("Español"))
    def test_catalog_complete_and_placeholders_preserved(self):
        reference = set(catalog()["English"])
        for language in LANGUAGES[1:]:
            table = catalog()[language]
            self.assertEqual(set(table), reference)
            for key, value in table.items():
                self.assertIsInstance(value, str)
                self.assertTrue(value.strip())
                self.assertEqual(sorted(re.findall(r"\{[^{}]+\}", key)), sorted(re.findall(r"\{[^{}]+\}", value)), (language,key))
    def test_error_and_template_translation(self):
        self.assertNotRegex(translate_error("원문 확인과 AI 전송 동의가 모두 필요합니다.", "Español"), r"[가-힣]")
        self.assertIn("AI-AUTH", translate_error("Unexpected failure AI-AUTH", "Español"))
        self.assertIn("2", translate("계약서 {page}페이지", "Español", page=2))
    def test_report_headers_change_quotes_and_amounts_do_not(self):
        report = test_report.ReportRenderingTests().make_report()
        original_quote = report["risks"][0]["quote"]
        for language in LANGUAGES:
            result = report_to_markdown(report,language)
            self.assertIn(translate("# 계약 검토 보고서",language), result)
            self.assertIn(original_quote, result)
            self.assertIn("₩10,000,000", result)
    def test_ui_keeps_state_keys_values_and_urls(self):
        class Target:
            def text_area(self,*args,**kwargs): return args,kwargs
            def link_button(self,*args,**kwargs): return args,kwargs
        ui=LocalizedUI(Target(),"Español")
        args,kwargs=ui.text_area("계약서 내용", "보증금: 10,000,000원", key="manual_input")
        self.assertEqual(args[0],translate("계약서 내용","Español"))
        self.assertEqual(args[1],"보증금: 10,000,000원")
        self.assertEqual(kwargs["key"],"manual_input")
        args,_=ui.link_button("공식 원문 확인","https://example.gov.kr/source")
        self.assertEqual(args[1],"https://example.gov.kr/source")

class LocalizedAppTests(unittest.TestCase):
    def app(self):
        app=AppTest.from_file(str(APP),default_timeout=30)
        app.secrets["OPENAI_API_KEY"]=""
        return app.run()
    def test_all_languages_change_navigation_and_controls(self):
        for language in LANGUAGES:
            with self.subTest(language=language):
                app=self.app()
                app.selectbox[0].select(language).run()
                self.assertEqual(len(app.exception),0)
                self.assertEqual([tab.label for tab in app.tabs],[translate(t,language) for t in ["계약서 읽기","바뀐 내용 비교","이용 안내"]])
                self.assertEqual(app.title[0].value,translate("서명하기 전, 한 번 더 읽어보세요.",language))
                self.assertEqual(app.selectbox[0].label,translate("언어 / Language",language))
                self.assertEqual(app.text_area(key="manual_input").label,translate("계약서 내용",language))
                self.assertTrue(button(app,translate("계약서 내용 읽기",language)).disabled)
                self.assertFalse(any(c.key=="consent_ocr" for c in app.checkbox))
    def test_language_change_keeps_original_clears_analysis_and_consent(self):
        app=self.app()
        button(app,"예시 계약서로 시작하기").click().run()
        original=app.text_area(key="manual_input").value
        button(app,"계약서 내용 읽기").click().run()
        self.assertTrue("pages" in app.session_state)
        app.selectbox[0].select("Español").run()
        self.assertEqual(len(app.exception),0)
        self.assertEqual(app.text_area(key="manual_input").value,original)
        self.assertFalse("pages" in app.session_state)
        self.assertFalse("hs_doc_ai_consent" in app.session_state)
    def test_spanish_original_confirmation_and_no_consent(self):
        with patch("hs_ai.AIService.analyze") as analyze:
            app=AppTest.from_file(str(APP),default_timeout=30)
            app.secrets["OPENAI_API_KEY"]="test-key"
            app.run(); app.selectbox[0].select("Español").run()
            button(app,translate("예시 계약서로 시작하기","Español")).click().run()
            button(app,translate("계약서 내용 읽기","Español")).click().run()
            self.assertIn("보증금: 10,000,000원",app.text_area(key="hs_doc_page_1").value)
            button(app,translate("확인한 내용으로 분석하기","Español")).click().run()
            analyze.assert_not_called()
            self.assertEqual(len(app.exception),0)
            self.assertTrue(any(translate("원문 확인과 AI 전송 동의가 모두 필요합니다.","Español")==e.value for e in app.error))
    def test_spanish_upload_consent_label_and_clear(self):
        app=self.app(); app.selectbox[0].select("Español").run()
        app.radio[0].set_value("PDF · 사진 업로드").run()
        label=translate("사진·스캔 페이지 원본의 OpenAI 전송을 허용합니다 (OCR에만 필요)","Español")
        consent=next(c for c in app.checkbox if c.key=="consent_ocr")
        self.assertEqual(consent.label,label)
        self.assertFalse(consent.value)
        app.text_area(key="compare_before").set_value("관리비 80,000원")
        app.text_area(key="compare_after").set_value("관리비 90,000원")
        button(app,translate("변경 내용 비교","Español")).click().run()
        button(app,translate("나의 문서·결과 지우기","Español")).click().run()
        self.assertEqual(len(app.exception),0)
        self.assertEqual(app.text_area(key="compare_before").value,"")
        self.assertEqual(app.text_area(key="compare_after").value,"")

if __name__ == "__main__":
    unittest.main()
