"""감시 한 바퀴 (mcp_hub 가 12시간마다 run() 을 부른다. 명령줄: python -m privacy_law.watch [profile.json ...]).

  1. 법령: 현행 버전 확인 → 개정이면 조문 비교 → 영향 문서마다 수정 제안(가능하면 Claude 초안).
     watch.json 의 조문 매핑은 기대 제목을 함께 적고, 실제 제목과 다르면 mapping_issues 로 알린다
     (조문 번호가 밀리거나 다른 조문이 그 번호를 쓰게 된 경우).
  2. 규칙 근거: requirements 규칙의 근거 조문이 규칙 작성 때(requirements_basis.json)와 달라졌으면 stale_rules.
  3. 문구 점검: 공용 문구 + 서비스 프로필(root/profiles/*.json 또는 인자)을 requirements 로 점검 → findings.
  4. 외부 기준: 개인정보보호위원회 새 글 · 행정처분 위반 조항 통계(→ 서비스별 case_gaps) · 다른 회사 처리방침 비교.

처음 실행은 법령 기준 스냅샷만 저장한다(비교 대상이 없으므로 제안 없음). 결과는 root/reports/latest.json 에도 남긴다.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

from . import DEFAULT_DIR, HOME, ConsentBook
from .approval_workflow import audit
from .change_detector import affected_documents, diff_articles
from .keys import get_key
from .law_fetcher import LawFetcher, LawFetchError, LawVersion
from .profile import ServiceProfile
from .requirements import case_gaps, check_book, plain, stale_rules, summarize
from .suggestion_engine import ProposalStore, build_prompt

CONFIG = Path(__file__).resolve().parent / "watch.json"


def _snap_path(root: Path, name: str) -> Path:
    return root / "snapshots" / (name.replace(" ", "_") + ".json")


def _load_snap(root: Path, name: str) -> LawVersion | None:
    p = _snap_path(root, name)
    if not p.exists():
        return None
    d = json.loads(p.read_text(encoding="utf-8"))
    return LawVersion(d["name"], d["mst"], d["promulgated"], d["effective"], d["articles"],
                      d.get("titles", {}), tuple(d.get("changed", ())))


def _save_snap(root: Path, v: LawVersion) -> None:
    p = _snap_path(root, v.name)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    d = {**v.__dict__, "changed": list(v.changed)}
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)


def mapping(law: dict) -> dict[str, dict]:
    """watch.json 조문 매핑 → {조문: {"docs": [...], "title": 기대 제목 or ""}} (예전 목록 형식도 받는다)."""
    out = {}
    for art, v in (law.get("articles") or {}).items():
        out[art] = {"docs": list(v), "title": ""} if isinstance(v, list) else {"docs": list(v.get("docs", [])),
                                                                               "title": v.get("title", "")}
    return out


def _norm_title(t: str) -> str:
    return "".join(t.replace("ㆍ", "·").replace("・", "·").split())


def mapping_issues(name: str, maps: dict[str, dict], v: LawVersion) -> list[dict]:
    out = []
    for art, m in maps.items():
        if art not in v.articles:
            out.append({"law": name, "article": art, "expected": m["title"], "actual": None,
                        "message": f"{name} 제{art}조가 현행 법령에 없음 — 매핑 점검 필요"})
        elif m["title"] and _norm_title(m["title"]) != _norm_title(v.titles.get(art, "")):
            out.append({"law": name, "article": art, "expected": m["title"], "actual": v.titles.get(art, ""),
                        "message": f"{name} 제{art}조 제목이 '{v.titles.get(art, '')}' 로 바뀜(기대: '{m['title']}') "
                                   "— 조문 번호가 밀렸거나 다른 조문일 수 있음"})
    return out


def load_profiles(root: Path) -> list[ServiceProfile]:
    d = Path(root) / "profiles"
    return [ServiceProfile.load(p) for p in sorted(d.glob("*.json"))] if d.is_dir() else []


def _save_report(root: Path, result: dict) -> None:
    d = Path(root) / "reports"
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / "latest.tmp"
    tmp.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(d / "latest.json")


def run(*, fetcher: LawFetcher | None = None, root: Path = HOME, texts_dir: Path = DEFAULT_DIR,
        config: Path = CONFIG, api_key: str | None = None, post=None, profiles: list | None = None,
        sources: bool = True, get=None, benchmarks: list | None = None, basis: dict | None = None) -> dict:
    """결과 키:
      checked · changed[{law, effective, mst, articles, amended, unmapped_docs, proposals}] · errors  (법령)
      mapping_issues · stale_rules                                                        (매핑 · 규칙 근거)
      findings{서비스: {summary, items}}                                                   (문구 점검)
      notices · dispositions · violations · case_gaps{서비스: [...]} · benchmarks · policy_changes  (외부 기준)
    get: 외부 사이트 http 주입(테스트). sources=False 면 외부 기준을 건너뛴다."""
    root = Path(root)
    result: dict = {"checked_at": datetime.now().isoformat(timespec="seconds"), "checked": [], "changed": [],
                    "errors": [], "mapping_issues": [], "stale_rules": [], "findings": {}, "notices": [],
                    "dispositions": [], "violations": {}, "case_gaps": {}, "benchmarks": [], "policy_changes": []}
    api_key = api_key or get_key("ANTHROPIC_API_KEY", root) or None

    # ---- 1. 법령 ----
    try:
        if fetcher is None:
            fetcher = LawFetcher(get_key("LAW_OC", root))
    except LawFetchError as e:
        result["errors"].append(str(e))
        fetcher = None

    laws = json.loads(Path(config).read_text(encoding="utf-8"))["laws"]
    docs = {d["key"]: d for d in json.loads((Path(texts_dir) / "documents.json").read_text(encoding="utf-8"))}
    store = ProposalStore(root / "proposals")
    current: dict[str, dict[str, str]] = {}

    for law in laws if fetcher else []:
        name = law["name"]
        maps = mapping(law)
        try:
            head = fetcher.current(name)
            prev = _load_snap(root, name)
            result["checked"].append({"law": name, "mst": head.mst, "effective": head.effective})
            full = prev if prev and prev.mst == head.mst and prev.titles else fetcher.with_articles(head)
            current[name] = full.articles
            result["mapping_issues"].extend(mapping_issues(name, maps, full))
            if prev and prev.mst == head.mst:
                if full is not prev:
                    _save_snap(root, full)      # 예전 형식 스냅샷(제목 없음) 보강
                continue
            _save_snap(root, full)
            if not prev:
                audit(root, "baseline", law=name, mst=head.mst, effective=head.effective)
                continue

            changes = diff_articles(prev.articles, full.articles)
            hit = affected_documents(changes, {a: m["docs"] for a, m in maps.items()})
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
                     "amended": list(full.changed),   # 법제처가 '이번 개정에서 변경'으로 표시한 조문
                     "unmapped_docs": sorted(k for k in hit if not (docs.get(k) or {}).get("file")),
                     "proposals": ids}
            result["changed"].append(entry)
            audit(root, "law_changed", **entry)
        except LawFetchError as e:
            result["errors"].append(str(e))
            audit(root, "fetch_failed", law=name, error=str(e))

    # ---- 2. 규칙 근거 ----
    result["stale_rules"] = stale_rules(current, basis)

    # ---- 3. 문구 점검 ----
    profiles = load_profiles(root) if profiles is None else profiles
    books = [("(공용 문구)", ConsentBook(texts_dir=texts_dir), {})]
    books += [(p.service, ConsentBook(texts_dir=None if p.texts_dir else texts_dir, profile=p), p.practice)
              for p in profiles]
    for svc, book, practice in books:
        fs = check_book(book, practice)
        result["findings"][svc] = {"summary": summarize(fs), "items": [f.to_dict() for f in fs]}

    # ---- 4. 외부 기준 ----
    if sources:
        from . import sources as src
        kw = {"get": get} if get else {}
        s = src.collect(root, benchmarks=benchmarks, **kw)
        result["errors"].extend(s["errors"])
        for k in ("notices", "dispositions", "violations", "policy_changes"):
            result[k] = s[k]
        # 첨부가 이미지뿐이라 위반 조항을 못 뽑은 처분 — 통계에서 빠지므로 사람이 봐야 한다
        result["manual_review"] = [{k: d[k] for k in ("ntt_id", "title", "date", "url", "unparsed")}
                                   for d in s.get("unread", [])]
        result["benchmarks"] = [{k: p[k] for k in ("name", "url", "version", "hash", "topics")} for p in s["policies"]]
        for svc, book, practice in books:
            if practice or svc == "(공용 문구)":
                result["case_gaps"][svc] = case_gaps(practice, s["violations"])
            if s["policies"] and "privacy" in {d.key for d in book.documents()}:
                result.setdefault("benchmark_gaps", {})[svc] = src.compare_topics(
                    plain(book.html("privacy")), s["policies"], practice)

    _save_report(root, result)
    return result


def main(argv: list[str]) -> int:
    profiles = [ServiceProfile.load(p) for p in argv] if argv else None
    print(json.dumps(run(profiles=profiles), ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
