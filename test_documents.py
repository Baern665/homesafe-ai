"""Synthetic upload/OCR regression tests. No external AI calls."""
from io import BytesIO
import unittest
from unittest.mock import Mock, patch
import os
import pymupdf
from PIL import Image
from hs_documents import extract_contract_files, parse_pdf_isolated, _PDF_LOCK
import subprocess

def png_bytes():
    output=BytesIO();Image.new("RGB",(80,80),"white").save(output,format="PNG");return output.getvalue()
def pdf_bytes(mixed=False, count=1, encrypted=False):
    with pymupdf.open() as doc:
        for n in range(count):
            p=doc.new_page();p.insert_text((50,50),"Rental contract deposit: 10000000 KRW. Monthly rent: 600000 KRW. Please review all clauses.")
        if mixed:
            p=doc.new_page();p.insert_image(p.rect,stream=png_bytes())
        if encrypted:
            return doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256,owner_pw="owner",user_pw="test")
        return doc.tobytes()

class DocumentTests(unittest.TestCase):
    def setUp(self):
        # Trusted synthetic fixtures only, not a supported public Windows upload mode.
        self.env_patch = patch.dict(os.environ,{"HOMESAFE_TRUSTED_TEST_UPLOADS":"1"})
        self.env_patch.start()
    def tearDown(self):
        self.env_patch.stop()
    def test_text_pdf_local_no_ocr(self):
        ocr=Mock();pages,warnings=extract_contract_files([{"name":"contract.pdf","data":pdf_bytes()}],ocr=ocr)
        self.assertEqual(len(pages),1);self.assertIn("10000000",pages[0]["text"]);self.assertEqual(warnings,[]);ocr.assert_not_called()
    def test_mixed_pdf_no_consent_blocks(self):
        ocr=Mock()
        with self.assertRaises(ValueError):extract_contract_files([{"name":"mixed.pdf","data":pdf_bytes(mixed=True)}],ocr=ocr)
        ocr.assert_not_called()
    def test_mixed_pdf_ocr_keeps_all_pages(self):
        ocr=Mock(return_value="특약: 퇴거 청소비 200,000원. 관리비는 별도 협의한다.")
        pages,warnings=extract_contract_files([{"name":"mixed.pdf","data":pdf_bytes(mixed=True)}],ocr=ocr,ocr_allowed=True)
        self.assertEqual([p['page'] for p in pages],[1,2]);self.assertIn("200,000원",pages[1]['text']);self.assertEqual(len(warnings),1)
        ocr.assert_called_once();self.assertEqual(ocr.call_args.args[1],"image/png")
    def test_image_no_consent_blocks(self):
        ocr=Mock()
        with self.assertRaises(ValueError):extract_contract_files([{"name":"photo.png","data":png_bytes()}],ocr=ocr)
        ocr.assert_not_called()
    def test_image_redacts_after_consented_ocr(self):
        ocr=Mock(return_value="임대인: 홍길동\n전화: 010-1234-5678\n보증금: 10,000,000원")
        pages,_=extract_contract_files([{"name":"photo.png","data":png_bytes()}],ocr=ocr,ocr_allowed=True)
        self.assertNotIn("홍길동",pages[0]['text']);self.assertNotIn("010-1234",pages[0]['text']);self.assertIn("10,000,000원",pages[0]['text'])
    def test_ocr_failure_no_partial_success(self):
        ocr=Mock(return_value="")
        with self.assertRaises(ValueError):extract_contract_files([{"name":"mixed.pdf","data":pdf_bytes(mixed=True)}],ocr=ocr,ocr_allowed=True)
    def test_page_limit(self):
        with self.assertRaises(ValueError):extract_contract_files([{"name":"long.pdf","data":pdf_bytes(count=13)}])
    def test_encrypted_pdf(self):
        with self.assertRaises(ValueError):extract_contract_files([{"name":"locked.pdf","data":pdf_bytes(encrypted=True)}])
    def test_corrupted_pdf(self):
        with self.assertRaises(ValueError):extract_contract_files([{"name":"bad.pdf","data":b"%PDF-1.7\ninvalid"}])
    def test_multiple_files_preserve_order(self):
        pages,_=extract_contract_files([{"name":"one.pdf","data":pdf_bytes()},{"name":"two.pdf","data":pdf_bytes()}])
        self.assertEqual([p['page'] for p in pages],[1,2])
    def test_parser_timeout_is_safe(self):
        with patch("hs_documents.subprocess.run",side_effect=subprocess.TimeoutExpired("worker",20)):
            with self.assertRaises(ValueError):parse_pdf_isolated(pdf_bytes())
    def test_parser_does_not_inherit_credentials(self):
        response=subprocess.CompletedProcess([],0,stdout=b'{"ok":true,"pages":[]}',stderr=b'')
        with patch.dict(os.environ,{"OPENAI_API_KEY":"DO-NOT-INHERIT","CUSTOM_SECRET":"DO-NOT-INHERIT"}):
            with patch("hs_documents.subprocess.run",return_value=response) as runner:
                parse_pdf_isolated(pdf_bytes())
                env=runner.call_args.kwargs["env"]
                self.assertNotIn("OPENAI_API_KEY",env)
                self.assertNotIn("CUSTOM_SECRET",env)
                self.assertEqual(runner.call_args.kwargs["timeout"],20)
    def test_concurrent_parser_admission_bounded(self):
        _PDF_LOCK.acquire()
        try:
            with self.assertRaises(ValueError):parse_pdf_isolated(pdf_bytes())
        finally:_PDF_LOCK.release()
    def test_overlength_ocr_rejected(self):
        ocr=Mock(return_value="x"*35001)
        with self.assertRaises(ValueError):extract_contract_files([{"name":"photo.png","data":png_bytes()}],ocr=ocr,ocr_allowed=True)

if __name__=="__main__":unittest.main()
