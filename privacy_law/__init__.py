"""privacy_law — 가입 동의 문서(이용약관 · 개인정보 처리방침 · 개인정보 수집·이용 동의 · 마케팅 수신) 관리.

목적: 개인정보 관련 법령이 바뀌면 여기 파일만 고쳐서 모든 서비스에 한 번에 반영한다.
  - texts/documents.json : 문서 목록(키 · 제목 · 필수 여부 · 버전 · 본문 파일)
  - texts/*.html         : 본문. {{ 변수 }} 는 서비스가 넘긴 values 로 채운다 (예: {{ service_name }}, {{ collect_items }})
  - 파일을 고치면 재시작 없이 다음 요청부터 반영된다(수정 시각 확인).
  - 서비스는 문구를 바꿀 수 없다(덮어쓰기 없음) — 서비스마다 다른 것은 values 로만 넘긴다.

문구를 크게 바꾸면 documents.json 의 version 을 올린다. 동의 기록(auth_consents)에 동의한 버전이 남으므로
outdated(username) 로 재동의가 필요한 문서를 알 수 있다.

폐쇄망 에디션은 enabled=False: 개인정보 처리자가 고객 사업장이므로 가입 화면 동의 · 기록을 하지 않는다.
판매 시점의 texts/ 문구는 참고용으로 함께 전달하고, 이후 개정은 사업장이 맡는다.

보통은 auth_core 의 AuthCore.attach_consents() 로 붙인다(에디션에 따라 enabled 자동 결정).
저장소는 서비스 DB 를 그대로 쓴다: ConsentBook(connect) — connect() 는 sqlite3 연결을 돌려주는 함수.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

__version__ = "0.1.0"

DEFAULT_DIR = Path(__file__).resolve().parent / "texts"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS auth_consents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL,
    doc_key TEXT NOT NULL,                -- terms / privacy / collect / marketing ...
    version TEXT NOT NULL,                -- 동의 당시 문서 버전
    agreed INTEGER NOT NULL,              -- 1 동의, 0 미동의(선택 항목)
    ip TEXT DEFAULT '',
    agreed_at TIMESTAMP DEFAULT (datetime('now', 'localtime'))
)"""
_INDEX = "CREATE INDEX IF NOT EXISTS idx_auth_consents_user ON auth_consents(username, doc_key)"
_VAR = re.compile(r"\{\{\s*(\w+)\s*\}\}")


@dataclass(frozen=True)
class ConsentDoc:
    key: str
    title: str
    required: bool
    version: str
    file: str | None = None      # None = 본문 없이 체크박스만 (예: 마케팅 수신)

    @property
    def field(self) -> str:
        """가입 폼의 체크박스 이름 (예: terms_agree)."""
        return f"{self.key}_agree"


class ConsentBook:
    def __init__(self, connect=None, *, values: dict | None = None, texts_dir=DEFAULT_DIR, enabled: bool = True):
        self._connect = connect
        self.enabled = enabled
        self.dir = Path(texts_dir)   # 기본은 auth_core 안의 texts (테스트에서만 바꿈)
        self.values = dict(values or {})
        self._cache: dict[Path, tuple[float, str]] = {}

    # ---- 문서 ----
    def _read(self, path: Path) -> str:
        mtime = path.stat().st_mtime
        hit = self._cache.get(path)
        if hit and hit[0] == mtime:
            return hit[1]
        text = path.read_text(encoding="utf-8")
        self._cache[path] = (mtime, text)
        return text

    def documents(self) -> list[ConsentDoc]:
        """화면에 보일 순서대로."""
        return [ConsentDoc(key=i["key"], title=i["title"], required=bool(i.get("required")),
                           version=str(i["version"]), file=i.get("file"))
                for i in json.loads(self._read(self.dir / "documents.json"))]

    def form_documents(self) -> list[ConsentDoc]:
        """가입 화면에 보일 동의 항목 (꺼져 있으면 없음)."""
        return self.documents() if self.enabled else []

    def get(self, key: str) -> ConsentDoc:
        for doc in self.documents():
            if doc.key == key:
                return doc
        raise KeyError(key)

    def html(self, key: str) -> str:
        """본문 HTML ({{ 변수 }} 채움). 모르는 변수는 그대로 둔다."""
        doc = self.get(key)
        if not doc.file:
            return ""
        return _VAR.sub(lambda m: str(self.values.get(m.group(1), m.group(0))), self._read(self.dir / doc.file))

    def missing(self, agreements: dict) -> ConsentDoc | None:
        """agreements = {문서 키: 체크 값}. 빠진 필수 동의가 있으면 그 문서, 없으면 None."""
        return next((d for d in self.form_documents() if d.required and not agreements.get(d.key)), None)

    # ---- 동의 기록 ----
    def _ensure(self, conn):
        conn.execute(_SCHEMA)
        conn.execute(_INDEX)

    def record(self, username: str, agreements: dict, ip: str = "", conn=None) -> None:
        """문서마다 (버전, 동의 여부)를 남긴다. conn 을 넘기면 그 트랜잭션 안에서(커밋은 호출자가)."""
        if not self.enabled:
            return
        own = conn is None
        conn = self._connect() if own else conn
        try:
            self._ensure(conn)
            conn.executemany(
                "INSERT INTO auth_consents (username, doc_key, version, agreed, ip) VALUES (?, ?, ?, ?, ?)",
                [(username, d.key, d.version, 1 if agreements.get(d.key) else 0, ip) for d in self.documents()])
            if own:
                conn.commit()
        finally:
            if own:
                conn.close()

    def latest(self, username: str) -> dict[str, dict]:
        """문서 키 → 마지막 기록 {version, agreed, agreed_at}."""
        conn = self._connect()
        try:
            self._ensure(conn)
            rows = conn.execute("""SELECT doc_key, version, agreed, agreed_at FROM auth_consents
                                   WHERE username = ? ORDER BY id""", (username,)).fetchall()
        finally:
            conn.close()
        return {r[0]: {"version": r[1], "agreed": bool(r[2]), "agreed_at": r[3]} for r in rows}

    def outdated(self, username: str) -> list[ConsentDoc]:
        """현재 버전에 동의하지 않은 필수 문서 (문구 변경 후 재동의 대상)."""
        if not self.enabled:
            return []
        last = self.latest(username)
        return [d for d in self.documents() if d.required
                and not (last.get(d.key, {}).get("agreed") and last[d.key]["version"] == d.version)]
