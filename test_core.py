"""Portable stdlib regression tests. Run with python -B -m unittest test_core -v."""
import copy
import hashlib
import unittest
from concurrent.futures import ThreadPoolExecutor

import hs_core as core


def source(sid="general", title="주택임대차 일반 안내", **extra):
    result = {"id": sid, "title": title, "url": "https://www.easylaw.go.kr/guide", "reviewed_at": "2026-10-09", "kind": "official-guidance"}
    result.update(extra)
    return result


def report():
    return {"summary": "확인이 필요합니다.", "fields": {"보증금": "10,000,000원", "주소": "서울시 강남구"}, "risks": [{"title": "원상복구", "level": "unknown", "quote": "원상복구는 협의", "page": 1, "reason": "범위 불명확", "questions": ["어디까지 복구하나요?"], "source_ids": ["general"]}], "missing": ["수선 범위"], "checklist": ["등기 확인"], "limitations": ["OCR 확인 필요"], "sources": [source()], "pages_total": 2, "pages_missing": [2]}


class UploadTests(unittest.TestCase):
    def test_supported_magic_and_extension(self):
        for name, data, kind in [("a.PDF", b"%PDF-1.7\nabc", "pdf"), ("a.png", b"\x89PNG\r\n\x1a\nabc", "png"), ("a.jpeg", b"\xff\xd8\xff\xe0abc", "jpg"), ("a.JPG", b"\xff\xd8\xff\xdbabc", "jpg")]:
            with self.subTest(name=name):
                self.assertEqual(core.validate_upload(name, data), kind)

    def test_header_mismatch_and_mixed_files(self):
        files = [("a.pdf", b"%PDF-1.4\n"), ("b.png", b"%PDF-1.4\n"), ("c.jpg", b"\x89PNG\r\n\x1a\n"), ("d.exe", b"MZ")]
        accepted = []
        for name, data in files:
            try:
                accepted.append(core.validate_upload(name, data))
            except ValueError:
                pass
        self.assertEqual(accepted, ["pdf"])

    def test_empty_bad_name_and_size(self):
        for name, data in [("a.pdf", b""), ("a.pdf\x00", b"%PDF-1.4\n"), ("a", b"%PDF-1.4\n"), ("secret.pdf", b"%PDF-1.4\n" + b"x" * core.MAX_BYTES), (None, b"x"), ("a.jpg", b"\xff\xd8\xff")]:
            with self.subTest(name=name), self.assertRaises(ValueError) as ctx:
                core.validate_upload(name, data)
            self.assertNotIn("secret", str(ctx.exception))


class PrivacyTests(unittest.TestCase):
    def test_resident_and_foreigner_ids(self):
        raw = "주민번호 900101-1234567 외국인번호 010203-5678901 다른 번호 0203046789012"
        result = core.redact_pii(raw)
        for value in ("900101-1234567", "010203-5678901", "0203046789012"):
            self.assertNotIn(value, result)
        self.assertEqual(result.count("신분번호 삭제"), 3)

    def test_phone_email_and_international(self):
        raw = "연락처 010-1234-5678 / 02-123-4567 / +82 10 2345 6789 / +82 (0)2-1234-5678 / 01012345678\n메일 alice+rent@example.co.kr"
        result = core.redact_pii(raw)
        self.assertEqual(result.count("전화번호 삭제"), 5)
        self.assertNotIn("alice", result)
        self.assertIn("이메일 삭제", result)

    def test_accounts_and_names(self):
        raw = "계좌번호: 123-456-789012\n입금계좌: 국민은행 123456789012\n임대인: 홍길동\n임차인 성명: 김영희\n이름: John Smith\n성명 이철수"
        result = core.redact_pii(raw)
        self.assertEqual(result.count("계좌번호 삭제"), 2)
        self.assertEqual(result.count("이름 삭제"), 4)
        for secret in ("홍길동", "김영희", "John Smith", "이철수", "123456789012"):
            self.assertNotIn(secret, result)

    def test_money_dates_address_and_clauses_preserved(self):
        raw = "보증금 100,000,000원 월세 700000원 계약기간 2026-10-09 ~ 2027-10-08\n주소 서울시 강남구 123-45\n임대인 동의 없이 전대 금지\n임차인 수선 의무\n계좌 입금 1000000원"
        self.assertEqual(core.redact_pii(raw), raw)
        self.assertIn("OCR", core.PII_NOTICE)

    def test_pii_limits_and_idempotence(self):
        with self.assertRaises(ValueError):
            core.redact_pii("x" * (core.MAX_CHARS + 1))
        redacted = core.redact_pii("임대인: 홍길동 010-1234-5678")
        self.assertEqual(core.redact_pii(redacted), redacted)

    def test_document_identity_language_and_data(self):
        a = core.document_key(b"abc", "한국어")
        self.assertEqual(a, hashlib.sha256("한국어".encode() + b"\x00abc").hexdigest())
        self.assertEqual(a, core.document_key(b"abc", "ko"))
        self.assertNotEqual(a, core.document_key(b"abc", "English"))
        self.assertNotEqual(a, core.document_key(b"abcd", "한국어"))
        with self.assertRaises(ValueError):
            core.document_key(b"", "한국어")


class RetrievalStateDiffTests(unittest.TestCase):
    def test_specific_retrieval_and_synonyms(self):
        sources = [source(), source("repair", "수선과 수리 안내", keywords=["수선"]), source("restore", "원상회복 안내", keywords=["원상복구"]), source("termination", "중도 해지 위약금 안내")]
        self.assertEqual(core.select_sources("원상복구 특약", sources)[0]["id"], "restore")
        self.assertEqual(core.select_sources("Repair obligations", sources)[0]["id"], "repair")
        self.assertEqual(core.select_sources("중도해지 위약금", sources)[0]["id"], "termination")
        self.assertEqual(core.select_sources("different", sources)[0]["id"], "general")

    def test_invalid_sources_general_fallback_and_limit(self):
        sources = [source("x", "구체적인 수선 안내"), source(), source("bad", url="javascript:alert(1)"), source("bad2", reviewed_at="2026-02-30"), source("bad3", kind="blog"), None, source()]
        self.assertEqual(core.select_sources("질문 없음", sources, 1)[0]["id"], "general")
        self.assertEqual(len(core.select_sources("질문 없음", sources)), 2)
        self.assertEqual(core.select_sources("x", sources, 0), [])
        with self.assertRaises(ValueError):
            core.select_sources("x", sources, -1)

    def test_state_reset_preserves_budget(self):
        key = core.document_key(b"doc", "한국어")
        state = {name: "old" for name in core._DOCUMENT_KEYS}
        state.update({"hs_doc_editor": "secret", "session_calls": 7, "session_budget": {"used": 7}, "usage_total": 7, "theme": "light"})
        self.assertTrue(core.reset_document_state(state, key))
        self.assertEqual(state, {"document_key": key, "session_calls": 7, "session_budget": {"used": 7}, "usage_total": 7, "theme": "light"})
        state["report"] = "new"
        self.assertFalse(core.reset_document_state(state, key))
        self.assertEqual(state["report"], "new")
        self.assertTrue(core.reset_document_state(state, core.document_key(b"other", "ko")))
        self.assertNotIn("report", state)

    def test_diff_output_and_line_endings(self):
        result = core.diff_contracts("월세 50만원", "월세 60만원")
        self.assertIn("--- before", result)
        self.assertIn("-월세 50만원", result)
        self.assertIn("+월세 60만원", result)
        self.assertEqual(core.diff_contracts("same", "same"), "")
        self.assertTrue(core.diff_contracts("a\n", "a"))

    def test_diff_limit_never_silent(self):
        with self.assertRaises(ValueError):
            core.diff_contracts("x" * (core.MAX_CHARS + 1), "a")
        with self.assertRaises(ValueError):
            core.diff_contracts("a" * 18000, "b" * 18000)


class ReportTests(unittest.TestCase):
    def test_report_languages_and_no_score(self):
        data = report()
        data["legal_score"] = "UNTRUSTED_SCORE"
        for language in ("한국어", "English", "日本語", "中文"):
            with self.subTest(language=language):
                md = core.report_to_markdown(data, language)
                self.assertIn(core.disclaimer(language), md)
                self.assertNotIn("UNTRUSTED_SCORE", md)
                self.assertIn("10,000,000원", md)

    def test_markdown_html_and_unsafe_links_escaped(self):
        data = report()
        data["summary"] = '<script>alert(1)</script>\n# heading\n[click](javascript:alert(1))\n![image](https://evil.test/x)\n```html\n<a href="x">x</a>'
        data["fields"]["<b>name</b>"] = "**bold** | [bad](data:text/html,x)"
        md = core.report_to_markdown(data)
        self.assertNotIn("<script>", md)
        self.assertNotIn("\n# heading", md)
        self.assertNotIn("[click](javascript:", md)
        self.assertNotIn("![image](", md)
        self.assertNotIn("```", md)
        self.assertIn("&lt;script&gt;", md)
        self.assertIn("javascript&#58;", md)
        self.assertIn("\\[click\\]", md)

    def test_report_structure_rejects_types_and_missing_fields(self):
        mutations = [("summary", 123), ("fields", []), ("risks", "bad"), ("pages_total", 13), ("pages_total", True), ("pages_missing", [3]), ("checklist", [1])]
        for key, value in mutations:
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                data = report()
                data[key] = value
                core.report_to_markdown(data)
        data = report()
        del data["summary"]
        with self.assertRaises(ValueError):
            core.report_to_markdown(data)

    def test_report_risk_and_source_validation(self):
        for key, value in [("level", "critical"), ("page", 0), ("page", True), ("source_ids", ["unlisted"]), ("questions", [123])]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                data = report()
                data["risks"][0][key] = value
                core.report_to_markdown(data)
        for url in ("javascript:alert(1)", "http://example.com", "https://user:password@example.com", "https://example.com/\n<script>", "https://example.com:99999"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                data = report()
                data["sources"][0]["url"] = url
                core.report_to_markdown(data)

    def test_report_limit_and_nonmutation(self):
        data = report()
        snapshot = copy.deepcopy(data)
        core.report_to_markdown(data)
        self.assertEqual(data, snapshot)
        data["summary"] = "x" * core.MAX_CHARS
        with self.assertRaises(ValueError):
            core.report_to_markdown(data)
        data = report()
        data["summary"] = "*" * 18000
        with self.assertRaises(ValueError):
            core.report_to_markdown(data)


class BudgetTests(unittest.TestCase):
    def test_session_global_caps_and_no_pii_retention(self):
        budget = core.ProcessBudget(max_calls=3)
        self.assertTrue(budget.reserve("session-one", 1))
        self.assertFalse(budget.reserve("session-one", 1))
        self.assertTrue(budget.reserve("session-two", 2))
        self.assertTrue(budget.reserve("session-two", 2))
        self.assertFalse(budget.reserve("session-three"))
        self.assertEqual(budget.used, 3)
        self.assertEqual(budget.session_calls("session-two"), 2)
        self.assertNotIn("session-one", budget._sessions)
        self.assertTrue(all(len(k) == 64 for k in budget._sessions))

    def test_concurrency_global_cap(self):
        budget = core.ProcessBudget(max_calls=17)
        with ThreadPoolExecutor(max_workers=16) as pool:
            accepted = list(pool.map(lambda n: budget.reserve("s" + str(n)), range(100)))
        self.assertEqual(sum(accepted), 17)
        self.assertEqual(budget.used, 17)

    def test_concurrency_same_session_cap(self):
        budget = core.ProcessBudget(max_calls=100)
        with ThreadPoolExecutor(max_workers=16) as pool:
            accepted = list(pool.map(lambda n: budget.reserve("same", 8), range(100)))
        self.assertEqual(sum(accepted), 8)
        self.assertEqual(budget.used, 8)

    def test_cooldown_and_daily_reset(self):
        clock = [100.0]
        budget = core.ProcessBudget(3, 10, clock=lambda: clock[0])
        self.assertTrue(budget.reserve("a"))
        self.assertFalse(budget.reserve("a"))
        self.assertTrue(budget.reserve("b"))
        clock[0] += 10
        self.assertTrue(budget.reserve("a"))
        self.assertFalse(budget.reserve("c"))
        clock[0] = 86400.0
        self.assertEqual(budget.used, 0)
        self.assertTrue(budget.reserve("a"))
        self.assertEqual(budget.session_calls("a"), 1)

    def test_invalid_budget_configuration(self):
        for options in ({"max_calls": -1}, {"max_calls": True}, {"cooldown_seconds": -1}, {"cooldown_seconds": float("nan")}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                core.ProcessBudget(**options)
        budget = core.ProcessBudget(0)
        self.assertFalse(budget.reserve("a"))
        with self.assertRaises(ValueError):
            budget.reserve("")
        with self.assertRaises(ValueError):
            budget.reserve("a", -1)


if __name__ == "__main__":
    unittest.main()
