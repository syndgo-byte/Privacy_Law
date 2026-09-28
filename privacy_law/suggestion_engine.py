"""법령 개정 → 동의 문서 수정안.

제안은 proposals/<id>/ 폴더에 쌓인다:
  proposal.json  상태 · 근거 조문 · 결정 기록
  current.html   제안 당시 원문
  prompt.md      수정 요청문 (수동 모드에서 Claude Code 에 그대로 넘긴다)
  suggested.html 수정안 (자동 모드면 바로, 수동 모드면 사람이/Claude Code 가 채운다)

상태: needs_draft(수정안 없음) → pending(검토 대기) → approved | rejected
ANTHROPIC_API_KEY 가 있으면 자동, 없으면 수동(needs_draft).
"""
from __future__ import annotations

import json
import os
import re
import urllib.request
from datetime import datetime
from pathlib import Path

from .change_detector import ArticleChange

_VAR = re.compile(r"\{\{\s*(\w+)\s*\}\}")
_FENCE = re.compile(r"^```(?:html)?\s*\n(.*?)\n```\s*$", re.S)
DEFAULT_MODEL = "claude-sonnet-5"


class SuggestionError(RuntimeError):
    pass


def build_prompt(doc_title: str, law: str, effective: str, changes: list[ArticleChange], html: str) -> str:
    arts = "\n\n".join(
        f"### 제{c.article}조 ({c.kind})\n**개정 전**\n{c.before or '(없음)'}\n\n**개정 후**\n{c.after or '(삭제)'}"
        for c in changes)
    return f"""# 동의 문서 수정 요청: {doc_title}

「{law}」이 개정되었습니다 (시행일 {effective}). 아래 개정 조문을 반영해 동의 문서 HTML 을 고쳐 주세요.

규칙
- 개정 내용 때문에 바뀌어야 하는 부분만 고친다. 나머지 문장 · 구조 · 태그는 그대로 둔다.
- {{{{ 변수 }}}} 자리표시는 하나도 지우거나 바꾸지 않는다.
- 결과는 수정된 HTML 전체만 출력한다(설명 · 코드펜스 없이).
- 바꿀 것이 없다고 판단되면 원문을 그대로 출력한다.

## 개정 조문
{arts}

## 현재 문서 HTML
{html}
"""


def validate(original: str, suggested: str) -> str:
    s = suggested.strip()
    m = _FENCE.match(s)
    if m:
        s = m.group(1).strip()
    if len(s) < 20 or "<" not in s:
        raise SuggestionError("수정안이 비었거나 HTML 이 아닙니다")
    lost = set(_VAR.findall(original)) - set(_VAR.findall(s))
    if lost:
        raise SuggestionError(f"수정안에서 변수 자리표시가 사라졌습니다: {sorted(lost)}")
    return s + "\n"


def call_claude(prompt: str, api_key: str, model: str = DEFAULT_MODEL, post=None) -> str:
    body = json.dumps({"model": model, "max_tokens": 8000,
                       "messages": [{"role": "user", "content": prompt}]}).encode()
    headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
    try:
        if post:
            raw = post("https://api.anthropic.com/v1/messages", body, headers)
        else:
            req = urllib.request.Request("https://api.anthropic.com/v1/messages", body, headers)
            with urllib.request.urlopen(req, timeout=120) as r:
                raw = r.read()
        data = json.loads(raw)
        return "".join(b.get("text", "") for b in data["content"] if b.get("type") == "text")
    except Exception as e:
        raise SuggestionError(f"Claude API 호출 실패: {e}") from e


class ProposalStore:
    def __init__(self, root: Path):
        self.root = Path(root)

    def _dir(self, pid: str) -> Path:
        if not re.fullmatch(r"[\w.-]+", pid):
            raise KeyError(pid)
        return self.root / pid

    def create(self, *, doc_key: str, doc_title: str, law: str, mst: str, effective: str,
               changes: list[ArticleChange], current_html: str, prompt: str) -> dict:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        pid = f"{stamp}-{doc_key}"
        d = self.root / pid
        n = 1
        while d.exists():
            n += 1
            pid = f"{stamp}-{doc_key}-{n}"
            d = self.root / pid
        d.mkdir(parents=True)
        (d / "current.html").write_text(current_html, encoding="utf-8")
        (d / "prompt.md").write_text(prompt, encoding="utf-8")
        meta = {"id": pid, "doc_key": doc_key, "doc_title": doc_title, "law": law, "mst": mst,
                "effective": effective, "changes": [c.to_dict() for c in changes],
                "status": "needs_draft", "mode": "manual", "error": None,
                "created_at": datetime.now().isoformat(timespec="seconds"),
                "decided_at": None, "decided_by": None, "reason": None}
        self._save(pid, meta)
        return meta

    def _save(self, pid: str, meta: dict) -> None:
        p = self._dir(pid) / "proposal.json"
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(p)

    def get(self, pid: str) -> dict:
        p = self._dir(pid) / "proposal.json"
        if not p.exists():
            raise KeyError(pid)
        return json.loads(p.read_text(encoding="utf-8"))

    def update(self, pid: str, **fields) -> dict:
        meta = self.get(pid)
        meta.update(fields)
        self._save(pid, meta)
        return meta

    def read(self, pid: str, name: str) -> str | None:
        p = self._dir(pid) / name
        return p.read_text(encoding="utf-8") if p.exists() else None

    def all(self) -> list[dict]:
        if not self.root.exists():
            return []
        return [self.get(d.name) for d in sorted(self.root.iterdir(), reverse=True)
                if (d / "proposal.json").exists()]

    def set_draft(self, pid: str, html: str, mode: str) -> dict:
        """수정안을 넣고 검토 대기로. 원문 대비 변수 누락이면 거부."""
        meta = self.get(pid)
        if meta["status"] not in ("needs_draft", "pending"):
            raise SuggestionError(f"이미 {meta['status']} 된 제안입니다")
        clean = validate(self.read(pid, "current.html") or "", html)
        (self._dir(pid) / "suggested.html").write_text(clean, encoding="utf-8")
        return self.update(pid, status="pending", mode=mode, error=None)

    def draft_with_claude(self, pid: str, api_key: str | None = None, post=None) -> dict:
        """자동 모드. 키가 없거나 실패하면 needs_draft 그대로 두고 error 만 남긴다."""
        key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            return self.get(pid)
        model = os.environ.get("PRIVACY_LAW_MODEL", DEFAULT_MODEL)
        try:
            text = call_claude(self.read(pid, "prompt.md"), key, model, post=post)
            return self.set_draft(pid, text, mode="auto")
        except SuggestionError as e:
            return self.update(pid, error=str(e))
