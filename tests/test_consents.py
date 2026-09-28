"""가입 동의 문서: 공통 문구 파일 · 즉시 반영 · 필수 검증 · 버전별 동의 기록."""
import json
import os
import shutil
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

from privacy_law import DEFAULT_DIR, ConsentBook


class ConsentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.db = self.tmp / "svc.db"
        self.texts = self.tmp / "texts"
        shutil.copytree(DEFAULT_DIR, self.texts)   # 실제 공통 문구의 복사본 (법령 변경을 흉내 내며 고침)
        self.book = ConsentBook(lambda: sqlite3.connect(self.db), texts_dir=self.texts,
                                values={"service_name": "테스트", "service_desc": "설계 도구", "collect_items": "아이디, 이메일"})

    def test_default_documents_and_values(self):
        keys = [d.key for d in self.book.documents()]
        self.assertEqual(keys, ["terms", "privacy", "collect", "marketing"])
        self.assertFalse(self.book.get("marketing").required)
        collect = self.book.html("collect")
        self.assertIn("아이디, 이메일", collect)
        self.assertIn("동의하지 않을 권리", collect)
        for key in ("terms", "privacy", "collect"):
            self.assertNotIn("{{", self.book.html(key), key)   # 채우지 못한 변수 없음
        self.assertEqual(self.book.html("marketing"), "")

    def test_missing_required(self):
        self.assertEqual(self.book.missing({"terms": "on", "privacy": "on"}).key, "collect")
        self.assertIsNone(self.book.missing({"terms": "on", "privacy": "on", "collect": "on"}))   # 마케팅은 선택

    def test_common_text_change_is_live(self):
        self.book.html("collect")                         # 캐시에 올려 둠
        f = self.texts / "collect.html"
        f.write_text("<p>{{ service_name }} 개정 법령 반영본</p>", encoding="utf-8")
        os.utime(f, (time.time() + 5, time.time() + 5))   # 파일 시각 해상도 대비
        self.assertEqual(self.book.html("collect"), "<p>테스트 개정 법령 반영본</p>")   # 재시작 없이 반영

    def test_record_and_reconsent_after_version_bump(self):
        self.book.record("kim", {"terms": 1, "privacy": 1, "collect": 1, "marketing": None}, ip="10.0.0.1")
        last = self.book.latest("kim")
        self.assertTrue(last["collect"]["agreed"])
        self.assertFalse(last["marketing"]["agreed"])
        self.assertEqual(self.book.outdated("kim"), [])
        # 개인정보 처리방침 문구를 바꾸고 버전을 올림 → 그 문서만 재동의 대상
        docs = self.texts / "documents.json"
        items = json.loads(docs.read_text(encoding="utf-8"))
        next(i for i in items if i["key"] == "privacy")["version"] = "2027-01-01"
        docs.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
        os.utime(docs, (time.time() + 5, time.time() + 5))
        self.assertEqual([d.key for d in self.book.outdated("kim")], ["privacy"])

    def test_disabled_for_closed_edition(self):
        closed = ConsentBook(lambda: sqlite3.connect(self.db), texts_dir=self.texts, enabled=False)
        self.assertEqual(closed.form_documents(), [])          # 가입 화면에 동의 항목 없음
        self.assertIsNone(closed.missing({}))                  # 동의 없이 가입 가능
        closed.record("park", {})
        self.assertEqual(closed.latest("park"), {})            # 기록도 남기지 않음
        self.assertIn("수집", closed.html("collect"))          # 문구 자체는 참고용으로 계속 제공

    def test_record_in_caller_transaction(self):
        conn = sqlite3.connect(self.db)
        self.book.record("lee", {"terms": 1, "privacy": 1, "collect": 1}, conn=conn)
        conn.rollback()                                   # 가입 실패 시 동의 기록도 함께 취소
        conn.close()
        self.assertEqual(self.book.latest("lee"), {})


if __name__ == "__main__":
    unittest.main()
