"""Offline evidence validation tests. Never calls OpenAI."""
import json
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from hs_ai import Analysis, AIService, SafeError, validate_analysis, check_quote
from hs_core import ProcessBudget

SOURCE = {"id":"guide","title":"안내","url":"https://www.moj.go.kr/","agency":"법무부","kind":"official-guidance","reviewed_at":"2026-10-09","keywords":["관리비"],"content":"관리비 항목을 확인한다."}
PAGES = [{"page":1,"text":"보증금: 10,000,000원\n관리비 항목은 별도 협의한다."}]
def payload():
    return {"summary":"관리비를 확인하세요.","fields":{"deposit":"10,000,000원","monthly_rent":"확인되지 않음","maintenance_fee":"확인되지 않음","duration":"확인되지 않음","move_in":"확인되지 않음","termination":"확인되지 않음"},"risks":[{"title":"관리비","level":"unknown","quote":"관리비 항목은 별도 협의한다.","page":1,"reason":"항목을 확인하세요.","questions":["포함 항목은 무엇인가요?"],"source_ids":["guide"]}],"missing":["관리비 항목"],"checklist":["원문 확인"],"limitations":["등기와 권리관계는 확인하지 않았습니다."]}

class EvidenceTests(unittest.TestCase):
    def test_valid_report(self):
        result = validate_analysis(Analysis.model_validate(payload()),PAGES,[SOURCE])
        self.assertEqual(result["fields"]["보증금"],"10,000,000원")
        self.assertEqual(result["pages_total"],1)
    def test_fabricated_quote(self):
        p=payload();p["risks"][0]["quote"]="계약은 안전합니다."
        with self.assertRaises(ValueError):validate_analysis(Analysis.model_validate(p),PAGES,[SOURCE])
    def test_wrong_page(self):
        with self.assertRaises(ValueError):check_quote(2,"관리비 항목은 별도 협의한다.",PAGES)
    def test_unknown_source(self):
        p=payload();p["risks"][0]["source_ids"]=["imaginary-law"]
        with self.assertRaises(ValueError):validate_analysis(Analysis.model_validate(p),PAGES,[SOURCE])
    def test_invalid_level(self):
        p=payload();p["risks"][0]["level"]="safe"
        with self.assertRaises(ValueError):Analysis.model_validate(p)
    def test_extra_fields(self):
        p=payload();p["fraud_probability"]=0
        with self.assertRaises(ValueError):Analysis.model_validate(p)
    def test_empty_quote(self):
        with self.assertRaises(ValueError):check_quote(1,"",PAGES)
    def test_budget_rejection_no_network(self):
        budget=ProcessBudget(max_calls=1);budget.reserve("s")
        with patch("hs_ai.OpenAI") as client:
            service=AIService("test-key","gpt-4.1-mini",budget,"s")
            with self.assertRaises(SafeError):service.ocr(b"fake","image/png")
            client.return_value.chat.completions.create.assert_not_called()
    def test_api_error_does_not_disclose_secret(self):
        with patch("hs_ai.OpenAI") as client:
            client.return_value.chat.completions.create.side_effect=RuntimeError("SECRET / raw contract")
            service=AIService("test-key","gpt-4.1-mini",ProcessBudget(max_calls=2),"s")
            with self.assertRaises(SafeError) as err:service.ocr(b"fake","image/png")
            self.assertNotIn("SECRET",str(err.exception))
    def test_incomplete_response_rejected(self):
        with patch("hs_ai.OpenAI") as client:
            client.return_value.chat.completions.create.return_value=SimpleNamespace(usage=None,choices=[SimpleNamespace(finish_reason="length",message=SimpleNamespace(content="{}"))])
            service=AIService("test-key","gpt-4.1-mini",ProcessBudget(max_calls=2),"s")
            with self.assertRaises(SafeError):service.ocr(b"fake","image/png")
    def test_confirmed_fields_cannot_be_overwritten(self):
        with patch("hs_ai.OpenAI"):
            service=AIService("test-key","gpt-4.1-mini",ProcessBudget(max_calls=2),"s")
            service._call=lambda *a,**k:json.dumps(payload(),ensure_ascii=False)
            result=service.analyze(PAGES,"한국어",[SOURCE],{"보증금":"20,000,000원"})
            self.assertEqual(result["fields"]["보증금"],"20,000,000원")
    def test_question_source_validation(self):
        with patch("hs_ai.OpenAI"):
            service=AIService("test-key","gpt-4.1-mini",ProcessBudget(max_calls=2),"s")
            service._call=lambda *a,**k:json.dumps({"answer":"답변","evidence":[],"source_ids":["invented"]})
            with self.assertRaises(SafeError):service.answer("질문",PAGES,{},"한국어",[SOURCE])
    def test_evidence_free_answer_is_controlled_refusal(self):
        with patch("hs_ai.OpenAI"):
            service=AIService("test-key","gpt-4.1-mini",ProcessBudget(max_calls=2),"s")
            service._call=lambda *a,**k:json.dumps({"answer":"보증금은 무조건 안전합니다.","evidence":[],"source_ids":[]})
            result=service.answer("안전한가요?",PAGES,{},"한국어",[SOURCE])
            self.assertNotIn("무조건 안전",result["answer"])
            self.assertIn("근거",result["answer"])
    def test_unconfirmed_movein_and_termination_are_unknown(self):
        with patch("hs_ai.OpenAI"):
            service=AIService("test-key","gpt-4.1-mini",ProcessBudget(max_calls=2),"s")
            p=payload();p["fields"]["move_in"]="2026-12-01";p["fields"]["termination"]="언제든 해지 가능"
            service._call=lambda *a,**k:json.dumps(p)
            result=service.analyze(PAGES,"한국어",[SOURCE],{"보증금":"10,000,000원"})
            self.assertEqual(result["fields"]["입주일"],"확인되지 않음")
            self.assertEqual(result["fields"]["해지 조건"],"확인되지 않음")
    def test_long_confirmed_fields_before_network(self):
        with patch("hs_ai.OpenAI") as client:
            service=AIService("test-key","gpt-4.1-mini",ProcessBudget(max_calls=2),"s")
            with self.assertRaises(SafeError):service.analyze(PAGES,"한국어",[SOURCE],{"보증금":"x"*1201})
            client.return_value.chat.completions.create.assert_not_called()
    def test_question_length(self):
        with patch("hs_ai.OpenAI"):
            service=AIService("test-key","gpt-4.1-mini",ProcessBudget(max_calls=2),"s")
            with self.assertRaises(SafeError):service.answer("x"*1001,PAGES,{},"한국어",[SOURCE])

if __name__ == "__main__":
    unittest.main()
