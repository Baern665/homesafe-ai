"""Streamlit UI smoke tests using synthetic data and mocked AI only."""
import os
from pathlib import Path
import unittest
from unittest.mock import patch
from streamlit.testing.v1 import AppTest
from test_ai import payload, SOURCE
from hs_ai import Analysis, validate_analysis

APP = Path(__file__).with_name("app.py")

def button(app,label):
    return next(b for b in app.button if b.label==label)

def checkbox(app,label):
    return next(c for c in app.checkbox if c.label==label)

def setup_app(ai=False):
    with patch.dict(os.environ,{"OPENAI_API_KEY":"test-key" if ai else ""}):
        return AppTest.from_file(str(APP),default_timeout=30).run()

def load_sample(app):
    button(app,"합성 예시 계약서로 체험하기").click().run()
    button(app,"계약서 내용 읽기").click().run()
    return app

class UITests(unittest.TestCase):
    def test_initial_empty_no_error(self):
        app=setup_app()
        self.assertEqual(len(app.exception),0)
        self.assertTrue(button(app,"계약서 내용 읽기").disabled)
    def test_sample_local_read(self):
        app=load_sample(setup_app())
        self.assertEqual(len(app.exception),0)
        self.assertEqual(len(app.session_state["pages"]),1)
        self.assertTrue(button(app,"확인한 내용으로 분석하기").disabled)
    def test_language_change_clears_document(self):
        app=load_sample(setup_app())
        app.selectbox[0].select("English").run()
        self.assertEqual(len(app.exception),0)
        self.assertFalse("pages" in app.session_state)
    def test_changed_input_clears_document(self):
        app=load_sample(setup_app())
        app.text_area(key="manual_input").set_value("보증금: 20,000,000원").run()
        self.assertEqual(len(app.exception),0)
        self.assertFalse("pages" in app.session_state)
    def test_clear_all(self):
        app=load_sample(setup_app())
        button(app,"나의 문서·결과 지우기").click().run()
        self.assertEqual(len(app.exception),0)
        self.assertEqual(app.text_area(key="manual_input").value,"")
        self.assertFalse("pages" in app.session_state)
    def test_ocr_consent_reset_for_changed_document(self):
        with patch.dict(os.environ,{"OPENAI_API_KEY":"test-key"}):
            app=load_sample(AppTest.from_file(str(APP),default_timeout=30).run())
            label="사진·스캔 페이지 원본의 OpenAI 전송을 허용합니다 (OCR에만 필요)"
            checkbox(app,label).check().run()
            self.assertTrue(checkbox(app,label).value)
            app.text_area(key="manual_input").set_value("보증금: 20,000,000원").run()
            self.assertFalse(checkbox(app,label).value)
            self.assertEqual(len(app.exception),0)
    def test_reread_resets_editors_and_confirmation(self):
        with patch.dict(os.environ,{"OPENAI_API_KEY":"test-key"}):
            app=load_sample(AppTest.from_file(str(APP),default_timeout=30).run())
            app.text_area(key="hs_doc_page_1").set_value("edited stale text").run()
            checkbox(app,"전체 조항과 금액·날짜를 원문과 비교했고, 남아 있는 개인정보를 확인했습니다.").check().run()
            button(app,"계약서 내용 읽기").click().run()
            self.assertNotEqual(app.text_area(key="hs_doc_page_1").value,"edited stale text")
            self.assertFalse(checkbox(app,"전체 조항과 금액·날짜를 원문과 비교했고, 남아 있는 개인정보를 확인했습니다.").value)
            self.assertEqual(len(app.exception),0)
    def test_upload_mode_empty_no_error(self):
        app=setup_app()
        app.radio[0].set_value("PDF · 사진 업로드").run()
        self.assertEqual(len(app.exception),0)
        self.assertTrue(button(app,"계약서 내용 읽기").disabled)
    def test_compare_local(self):
        app=setup_app()
        app.text_area(key="compare_before").set_value("관리비 80,000원")
        app.text_area(key="compare_after").set_value("관리비 90,000원")
        button(app,"변경 내용 비교").click().run()
        self.assertEqual(len(app.exception),0)
        self.assertIn("+관리비 90,000원",app.session_state["compare_result"])
    def test_no_consent_does_not_call_ai(self):
        with patch.dict(os.environ,{"OPENAI_API_KEY":"test-key"}):
            app=load_sample(AppTest.from_file(str(APP),default_timeout=30).run())
            with patch("hs_ai.AIService.analyze") as mocked:
                button(app,"확인한 내용으로 분석하기").click().run()
                mocked.assert_not_called()
            self.assertEqual(len(app.exception),0)
            self.assertTrue(any("동의" in err.value for err in app.error))
    def test_verified_analysis_and_export(self):
        with patch.dict(os.environ,{"OPENAI_API_KEY":"test-key"}):
            app=load_sample(AppTest.from_file(str(APP),default_timeout=30).run())
            def fake_analyze(service,pages,language,sources,fields):
                p=payload();p["risks"][0]["quote"]="관리비: 월 80,000원. 관리비 포함 항목은 별도 협의한다."
                p["risks"][0]["source_ids"]=[]
                result=validate_analysis(Analysis.model_validate(p),pages,[])
                result["fields"].update(fields)
                return result
            checkbox(app,"전체 조항과 금액·날짜를 원문과 비교했고, 남아 있는 개인정보를 확인했습니다.").check()
            checkbox(app,"확인한 텍스트와 이후 질문을 OpenAI에 전송하여 참고용 분석을 받는 데 동의합니다.").check()
            with patch("hs_ai.AIService.analyze",fake_analyze):
                button(app,"확인한 내용으로 분석하기").click().run()
            self.assertEqual(len(app.exception),0)
            self.assertTrue("report" in app.session_state)
            self.assertFalse(any("다운로드 파일" in warn.value for warn in app.warning))


class SettingsTests(unittest.TestCase):
    def configured_app(self, secrets):
        app=AppTest.from_file(str(APP),default_timeout=30)
        app.secrets.update(secrets)
        return app
    def test_cloud_secret_wins_over_stale_environment(self):
        with patch.dict(os.environ,{"OPENAI_API_KEY":"stale-environment-test-key"}):
            app=self.configured_app({"OPENAI_API_KEY":"fresh-secret-test-key"})
            with patch("hs_ai.AIService") as service:
                app.run()
                self.assertEqual(len(app.exception),0)
                self.assertEqual(service.call_args.args[0],"fresh-secret-test-key")
    def test_environment_fallback_when_secret_missing(self):
        with patch.dict(os.environ,{"OPENAI_API_KEY":"environment-test-key"}):
            app=self.configured_app({"other_setting":"value"})
            with patch("hs_ai.AIService") as service:
                app.run()
                self.assertEqual(len(app.exception),0)
                self.assertEqual(service.call_args.args[0],"environment-test-key")
    def test_whitespace_is_trimmed_from_api_key(self):
        app=self.configured_app({"OPENAI_API_KEY":"  whitespace-test-key \n"})
        with patch("hs_ai.AIService") as service:
            app.run()
            self.assertEqual(len(app.exception),0)
            self.assertEqual(service.call_args.args[0],"whitespace-test-key")
    def test_explicit_empty_secret_disables_ai_without_env_fallback(self):
        with patch.dict(os.environ,{"OPENAI_API_KEY":"stale-environment-test-key"}):
            app=self.configured_app({"OPENAI_API_KEY":""})
            with patch("hs_ai.AIService") as service:
                app.run()
                self.assertEqual(len(app.exception),0)
                service.assert_not_called()

if __name__ == "__main__":
    unittest.main()
