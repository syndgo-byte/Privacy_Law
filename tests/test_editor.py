"""문구 수정 화면: 저장 검증 · 한 줄 형식 유지 · 접속 토큰 · 외부 Host 차단."""
import json
import shutil
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from privacy_law import DEFAULT_DIR, ConsentBook, editor


class EditorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()) / "texts"
        shutil.copytree(DEFAULT_DIR, self.tmp)
        self._orig, editor.DEFAULT_DIR = editor.DEFAULT_DIR, self.tmp

    def tearDown(self):
        editor.DEFAULT_DIR = self._orig

    def test_save_updates_text_and_version(self):
        editor.save({"key": "collect", "title": "개인정보 수집 · 이용", "required": True, "version": "2027-01-01",
                     "html": "<p>{{ collect_items }} 개정</p>"})
        book = ConsentBook(texts_dir=self.tmp, values={"collect_items": "아이디"})
        self.assertEqual(book.get("collect").version, "2027-01-01")
        self.assertEqual(book.html("collect").strip(), "<p>아이디 개정</p>")
        lines = (self.tmp / "documents.json").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 6)                   # [ + 문서 4줄 + ]  (git 변경 내역 보기 쉬운 형식 유지)

    def test_save_rejects_bad_input(self):
        for body in ({"key": "nope", "title": "x", "version": "1"},
                     {"key": "terms", "title": "", "version": "1"},
                     {"key": "terms", "title": "x", "version": "../../evil"}):
            with self.assertRaises(ValueError):
                editor.save(body)

    def test_http_requires_token_and_local_host(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), editor.Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            def status(path, headers=None, data=None):
                req = urllib.request.Request(base + path, headers=headers or {}, data=data)
                try:
                    return urllib.request.urlopen(req).status
                except urllib.error.HTTPError as e:
                    return e.code
            self.assertEqual(status(f"/?t={editor.TOKEN}"), 200)
            self.assertEqual(status("/?t=wrong"), 404)
            self.assertEqual(status("/api/state"), 404)                                    # 토큰 없음
            self.assertEqual(status("/api/state", {"X-Token": editor.TOKEN}), 200)
            self.assertEqual(status("/api/save", {"X-Token": "x"}, b"{}"), 403)
            self.assertEqual(status(f"/?t={editor.TOKEN}", {"Host": "evil.example"}), 403)  # DNS 리바인딩 차단
        finally:
            server.shutdown()


if __name__ == "__main__":
    unittest.main()
