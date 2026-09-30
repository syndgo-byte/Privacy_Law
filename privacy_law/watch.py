"""법령 감시 한 바퀴: 현행 버전 확인 → 개정이면 조문 비교 → 영향 문서마다 제안 생성(가능하면 자동 초안).

mcp_hub 가 매일 run() 을 부른다. 명령줄: python -m privacy_law.watch
처음 실행은 기준 스냅샷만 저장한다(비교 대상이 없으므로 제안 없음).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from . import DEFAULT_DIR
from .approval_workflow import audit
from .change_detector import affected_documents, diff_articles
from .law_fetcher import LawFetcher, MockLawFetcher, LawFetchError, LawVersion
from .suggestion_engine import ProposalStore, build_prompt

HOME = Path(os.environ.get("PRIVACY_LAW_HOME") or Path(__file__).resolve().parent.parent)
CONFIG = Path(__file__).resolve().parent / "watch.json"


def _snap_path(root: Path, name: str) -> Path:
    return root / "snapshots" / (name.replace(" ", "_") + ".json")


def _load_snap(root: Path, name: str) -> LawVersion | None:
    p = _snap_path(root, name)
    if not p.exists():
        return None
    d = json.loads(p.read_text(encoding="utf-8"))
    return LawVersion(d["name"], d["mst"], d["promulgated"], d["effective"], d["articles"])


def _save_snap(root: Path, v: LawVersion) -> None:
    p = _snap_path(root, v.name)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(v.__dict__, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)


def run(*, fetcher: LawFetcher | None = None, root: Path = HOME, texts_dir: Path = DEFAULT_DIR,
        config: Path = CONFIG, api_key: str | None = None, post=None) -> dict:
    """결과: {"checked": [...], "changed": [{law, effective, articles, proposals}], "errors": [...]}"""
    root = Path(root)
    result = {"checked": [], "changed": [], "errors": []}
    try:
        oc = os.environ.get("LAW_OC", "")
        if fetcher is None:
            fetcher = LawFetcher(oc) if oc else MockLawFetcher()
    except LawFetchError as e:
        result["errors"].append(str(e))
        return result

    laws = json.loads(Path(config).read_text(encoding="utf-8"))["laws"]
    docs = {d["key"]: d for d in json.loads((Path(texts_dir) / "documents.json").read_text(encoding="utf-8"))}
    store = ProposalStore(root / "proposals")

    for law in laws:
        name = law["name"]
        try:
            head = fetcher.current(name)
            prev = _load_snap(root, name)
            result["checked"].append({"law": name, "mst": head.mst, "effective": head.effective})
            if prev and prev.mst == head.mst:
                continue
            full = fetcher.with_articles(head)
            _save_snap(root, full)
            if not prev:
                audit(root, "baseline", law=name, mst=head.mst, effective=head.effective)
                continue

            changes = diff_articles(prev.articles, full.articles)
            hit = affected_documents(changes, law.get("articles", {}))
            ids = []
            for key, arts in hit.items():
                doc = docs.get(key)
                if not doc or not doc.get("file"):
                    continue   # 본문 없는 항목(마케팅 체크박스)은 감사 기록으로만 남긴다
                html = (Path(texts_dir) / doc["file"]).read_text(encoding="utf-8")
                meta = store.create(doc_key=key, doc_title=doc["title"], law=name, mst=head.mst,
                                    effective=head.effective, changes=arts, current_html=html,
                                    prompt=build_prompt(doc["title"], name, head.effective, arts, html))
                meta = store.draft_with_claude(meta["id"], api_key=api_key, post=post)
                ids.append(meta["id"])
            entry = {"law": name, "effective": head.effective, "mst": head.mst,
                     "articles": [c.article for c in changes],
                     "unmapped_docs": sorted(k for k in hit if not (docs.get(k) or {}).get("file")),
                     "proposals": ids}
            result["changed"].append(entry)
            audit(root, "law_changed", **entry)
        except LawFetchError as e:
            result["errors"].append(str(e))
            audit(root, "fetch_failed", law=name, error=str(e))
    return result


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2))
