"""검토 대기 제안의 승인 · 거절. 승인하면 texts/ 에 반영하고 버전을 올려 재동의를 일으킨다."""
from __future__ import annotations

import json
import subprocess
from datetime import date, datetime
from pathlib import Path

from .suggestion_engine import ProposalStore, SuggestionError


class ApprovalError(RuntimeError):
    pass


def audit(root: Path, event: str, **data) -> None:
    """추가만 하는 감사 기록 (.audit/audit.jsonl)."""
    p = Path(root) / ".audit" / "audit.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    line = {"at": datetime.now().isoformat(timespec="seconds"), "event": event, **data}
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(line, ensure_ascii=False) + "\n")


def next_version(old: str, today: str | None = None) -> str:
    """버전은 날짜. 같은 날 두 번째 개정이면 2026-09-28.2 처럼."""
    today = today or date.today().isoformat()
    if not old.startswith(today):
        return today
    _, _, n = old.partition(f"{today}.")
    return f"{today}.{int(n) + 1 if n.isdigit() else 2}"


class ApprovalWorkflow:
    def __init__(self, root: Path, texts_dir: Path, *, git: bool = True):
        self.root = Path(root)
        self.texts = Path(texts_dir)
        self.store = ProposalStore(self.root / "proposals")
        self.git = git

    def approve(self, pid: str, by: str) -> dict:
        meta = self.store.get(pid)
        if meta["status"] != "pending":
            raise ApprovalError(f"검토 대기 상태가 아닙니다 ({meta['status']})")
        docs_path = self.texts / "documents.json"
        docs = json.loads(docs_path.read_text(encoding="utf-8"))
        doc = next((d for d in docs if d["key"] == meta["doc_key"]), None)
        if not doc or not doc.get("file"):
            raise ApprovalError(f"본문 파일이 있는 문서가 아닙니다: {meta['doc_key']}")
        target = self.texts / doc["file"]
        now = target.read_text(encoding="utf-8").replace("\r\n", "\n")
        then = (self.store.read(pid, "current.html") or "").replace("\r\n", "\n")
        if now != then:
            raise ApprovalError("제안 이후 원문이 바뀌었습니다. 이 제안은 거절하고 다시 만들어야 합니다")

        old_version = doc["version"]
        doc["version"] = next_version(old_version)
        target.write_text(self.store.read(pid, "suggested.html"), encoding="utf-8")
        tmp = docs_path.with_suffix(".tmp")
        tmp.write_text("[\n" + ",\n".join("  " + json.dumps(d, ensure_ascii=False) for d in docs) + "\n]\n",
                       encoding="utf-8")
        tmp.replace(docs_path)

        meta = self.store.update(pid, status="approved", decided_by=by,
                                 decided_at=datetime.now().isoformat(timespec="seconds"),
                                 old_version=old_version, new_version=doc["version"])
        audit(self.root, "approved", id=pid, doc_key=doc["key"], by=by,
              old_version=old_version, new_version=doc["version"], law=meta["law"])
        self._commit(f"법령 개정 반영: {meta['doc_title']} ({meta['law']} 시행 {meta['effective']}) — 승인 {by}")
        return meta

    def reject(self, pid: str, by: str, reason: str) -> dict:
        meta = self.store.get(pid)
        if meta["status"] not in ("needs_draft", "pending"):
            raise ApprovalError(f"이미 {meta['status']} 된 제안입니다")
        if not reason.strip():
            raise ApprovalError("거절 사유를 적어 주세요")
        meta = self.store.update(pid, status="rejected", decided_by=by, reason=reason,
                                 decided_at=datetime.now().isoformat(timespec="seconds"))
        audit(self.root, "rejected", id=pid, doc_key=meta["doc_key"], by=by, reason=reason)
        self._commit(f"법령 개정 제안 거절: {meta['doc_title']} — {reason}")
        return meta

    def draft(self, pid: str, html: str, by: str) -> dict:
        """수동 모드: 사람이/Claude Code 가 쓴 수정안을 넣는다."""
        try:
            meta = self.store.set_draft(pid, html, mode="manual")
        except SuggestionError as e:
            raise ApprovalError(str(e)) from e
        audit(self.root, "drafted", id=pid, doc_key=meta["doc_key"], by=by)
        return meta

    def _commit(self, message: str) -> None:
        if not self.git or not (self.root / ".git").exists():
            return
        paths = [str(p) for p in (self.texts, self.root / "proposals", self.root / ".audit",
                                  self.root / "snapshots") if p.exists()]
        run = lambda *a: subprocess.run(["git", *a], cwd=self.root, capture_output=True, text=True)
        run("add", "--", *paths)
        r = run("commit", "-m", message)
        if r.returncode != 0 and "nothing to commit" not in (r.stdout + r.stderr):
            audit(self.root, "git_commit_failed", message=message, output=(r.stdout + r.stderr)[-500:])
