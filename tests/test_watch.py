import json
import shutil
import sqlite3
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from privacy_law import DEFAULT_DIR, ConsentBook
from privacy_law.approval_workflow import ApprovalError, ApprovalWorkflow, next_version
from privacy_law.change_detector import affected_documents, diff_articles
from privacy_law.law_fetcher import LawFetcher, LawFetchError
from privacy_law.suggestion_engine import ProposalStore, SuggestionError, validate
from privacy_law.watch import run

LAW = "개인정보 보호법"


class FakeLawAPI:
    """법제처 DRF 응답 흉내. versions[mst] = {조문번호: 내용}."""

    def __init__(self):
        self.mst = "100"
        self.versions = {"100": {"15": "① 개인정보처리자는 동의를 받은 경우 수집할 수 있다.", "30": "처리방침을 정한다."}}
        self.fail = False

    def __call__(self, url):
        if self.fail:
            raise OSError("연결 거부")
        q = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
        if "lawSearch" in url:
            rows = [{"법령명한글": q["query"], "법령일련번호": self.mst, "공포일자": "20260101", "시행일자": "20260301"}]
            return json.dumps({"LawSearch": {"law": rows}}).encode()
        arts = self.versions[q["MST"]]
        units = [{"조문여부": "전문", "조문번호": "1", "조문내용": "제1장 총칙"}]
        for no, text in arts.items():
            head, _, br = no.partition("의")
            units.append({"조문여부": "조문", "조문번호": head, "조문가지번호": br, "조문내용": f"제{no}조", "항": [{"항내용": text}]})
        return json.dumps({"법령": {"조문": {"조문단위": units}}}).encode()


@pytest.fixture(autouse=True)
def _no_real_keys(monkeypatch):
    # 개발 PC 의 실제 키로 외부 API 를 부르지 않게
    for k in ("LAW_OC", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(k, raising=False)


@pytest.fixture
def env(tmp_path):
    texts = tmp_path / "texts"
    shutil.copytree(DEFAULT_DIR, texts)
    config = tmp_path / "watch.json"
    config.write_text(json.dumps({"laws": [{"name": LAW, "articles": {"15": ["collect", "marketing"], "30": ["privacy"]}}]},
                                 ensure_ascii=False), encoding="utf-8")
    api = FakeLawAPI()
    root = tmp_path / "repo"
    kw = dict(fetcher=LawFetcher("test", get=api), root=root, texts_dir=texts, config=config, sources=False)
    return api, root, texts, kw


def _amend(api, new_15):
    api.versions["200"] = {**api.versions["100"], "15": new_15}
    api.mst = "200"


# ---- 부품 ----

def test_fetcher_parses_articles_and_skips_chapter_titles():
    f = LawFetcher("x", get=FakeLawAPI())
    v = f.with_articles(f.current(LAW))
    assert v.mst == "100" and set(v.articles) == {"15", "30"}
    assert "동의를 받은 경우" in v.articles["15"]


def test_fetcher_requires_key_and_wraps_errors():
    with pytest.raises(LawFetchError):
        LawFetcher("")
    api = FakeLawAPI()
    api.fail = True
    with pytest.raises(LawFetchError, match="연결 거부"):
        LawFetcher("x", get=api).current(LAW)


def test_diff_ignores_whitespace_and_orders_branch_articles():
    old = {"15": "가  나\n다", "22": "a", "3": "x"}
    new = {"15": "가 나 다", "22의2": "b", "3": "y"}
    kinds = [(c.article, c.kind) for c in diff_articles(old, new)]
    assert kinds == [("3", "modified"), ("22", "removed"), ("22의2", "added")]
    hit = affected_documents(diff_articles(old, new), {"3": ["privacy"], "22의2": ["collect", "terms"]})
    assert sorted(hit) == ["collect", "privacy", "terms"]


def test_validate_strips_fence_and_keeps_placeholders():
    assert validate("<p>{{ service_name }}</p>", "```html\n<p>{{ service_name }} 새 문구입니다</p>\n```").startswith("<p>")
    with pytest.raises(SuggestionError, match="service_name"):
        validate("<p>{{ service_name }}</p>", "<p>변수를 지워 버린 수정안입니다</p>")
    with pytest.raises(SuggestionError):
        validate("<p>x</p>", "")


def test_next_version():
    assert next_version("2026-01-01", "2026-09-28") == "2026-09-28"
    assert next_version("2026-09-28", "2026-09-28") == "2026-09-28.2"
    assert next_version("2026-09-28.2", "2026-09-28") == "2026-09-28.3"


# ---- 한 바퀴 ----

def test_first_run_saves_baseline_only(env):
    api, root, texts, kw = env
    r = run(**kw)
    assert r["changed"] == [] and r["errors"] == []
    assert (root / "snapshots" / "개인정보_보호법.json").exists()
    assert run(**kw)["changed"] == []          # 같은 MST 면 조문도 안 받는다


def test_unrelated_amendment_makes_no_proposal(env):
    api, root, texts, kw = env
    run(**kw)
    api.versions["200"] = {**api.versions["100"], "99": "새 조문"}
    api.mst = "200"
    r = run(**kw)
    assert r["changed"][0]["articles"] == ["99"] and r["changed"][0]["proposals"] == []


def test_manual_cycle_to_reconsent(env, tmp_path):
    api, root, texts, kw = env
    run(**kw)
    _amend(api, "① 개인정보처리자는 명시적 동의를 받은 경우에만 수집할 수 있다.")
    r = run(**kw)
    ch = r["changed"][0]
    assert ch["articles"] == ["15"] and ch["unmapped_docs"] == ["marketing"]
    [pid] = ch["proposals"]

    store = ProposalStore(root / "proposals")
    meta = store.get(pid)
    assert meta["status"] == "needs_draft" and meta["doc_key"] == "collect"
    assert "명시적 동의" in store.read(pid, "prompt.md")

    # 동의 기록: 현재 버전으로 가입한 사용자
    db = tmp_path / "svc.db"
    book = ConsentBook(lambda: sqlite3.connect(db), texts_dir=texts)
    book.record("kim", {"terms": 1, "privacy": 1, "collect": 1})
    assert book.outdated("kim") == []

    wf = ApprovalWorkflow(root, texts, git=False)
    with pytest.raises(ApprovalError):
        wf.approve(pid, "admin")                 # 초안 없이 승인 불가
    original = store.read(pid, "current.html")
    wf.draft(pid, original.replace("</table>", "</table>\n<p>명시적 동의를 받은 경우에만 수집합니다.</p>", 1), "admin")
    old_version = book.get("collect").version
    meta = wf.approve(pid, "admin")

    assert meta["status"] == "approved" and meta["old_version"] == old_version
    assert "명시적 동의" in (texts / "collect.html").read_text(encoding="utf-8")
    assert book.get("collect").version != old_version
    assert [d.key for d in book.outdated("kim")] == ["collect"]     # 재동의 대상
    events = [json.loads(l)["event"] for l in (root / ".audit" / "audit.jsonl").read_text(encoding="utf-8").splitlines()]
    assert events == ["baseline", "law_changed", "drafted", "approved"]

    book.record("kim", {"terms": 1, "privacy": 1, "collect": 1})
    assert book.outdated("kim") == []


def test_auto_mode_drafts_with_claude(env):
    api, root, texts, kw = env
    run(**kw)
    _amend(api, "① 명시적 동의가 필요하다.")
    sent = {}

    def post(url, body, headers):
        sent["prompt"] = json.loads(body)["messages"][0]["content"]
        sent["key"] = headers["x-api-key"]
        html = (texts / "collect.html").read_text(encoding="utf-8") + "<p>명시적 동의 반영</p>"
        return json.dumps({"content": [{"type": "text", "text": html}]}).encode()

    [pid] = run(**kw, api_key="sk-test", post=post)["changed"][0]["proposals"]
    meta = ProposalStore(root / "proposals").get(pid)
    assert meta["status"] == "pending" and meta["mode"] == "auto"
    assert sent["key"] == "sk-test" and "명시적 동의가 필요하다" in sent["prompt"]


def test_auto_mode_failure_falls_back_to_manual(env):
    api, root, texts, kw = env
    run(**kw)
    _amend(api, "① 개정")

    def post(url, body, headers):
        return json.dumps({"content": [{"type": "text", "text": "<p>자리표시 변수를 모두 날려 버린 수정안</p>"}]}).encode()

    [pid] = run(**kw, api_key="sk", post=post)["changed"][0]["proposals"]
    meta = ProposalStore(root / "proposals").get(pid)
    assert meta["status"] == "needs_draft" and "자리표시" in meta["error"]


def test_approve_refuses_when_text_changed_meanwhile(env):
    api, root, texts, kw = env
    run(**kw)
    _amend(api, "① 개정")
    [pid] = run(**kw)["changed"][0]["proposals"]
    wf = ApprovalWorkflow(root, texts, git=False)
    wf.draft(pid, ProposalStore(root / "proposals").read(pid, "current.html") + "<p>추가</p>", "admin")
    (texts / "collect.html").write_text("<p>{{ service_name }} 누가 editor 로 고침</p>", encoding="utf-8")
    with pytest.raises(ApprovalError, match="원문이 바뀌었"):
        wf.approve(pid, "admin")


def test_reject_needs_reason_and_is_final(env):
    api, root, texts, kw = env
    run(**kw)
    _amend(api, "① 개정")
    [pid] = run(**kw)["changed"][0]["proposals"]
    wf = ApprovalWorkflow(root, texts, git=False)
    with pytest.raises(ApprovalError):
        wf.reject(pid, "admin", " ")
    assert wf.reject(pid, "admin", "문구 영향 없음")["status"] == "rejected"
    with pytest.raises(ApprovalError):
        wf.approve(pid, "admin")


def test_fetch_failure_is_reported_not_raised(env):
    api, root, texts, kw = env
    api.fail = True
    r = run(**kw)
    assert r["changed"] == [] and "연결 거부" in r["errors"][0]


def test_web_parser_and_version_key():
    """웹 화면(lsInfoR.do) 조문 파싱 · 삭제 조문 무시 · 시행 전 개정까지 공포일로 버전 구분."""
    from privacy_law.law_fetcher import WebLawFetcher
    pop = "<script>lsPopViewAll2('283839', '', '', '20260911', 'Y', '','010202','0');</script>"
    body = ('<div>[시행 2026. 9. 11.] [법률 제21445호, 2026. 3. 10., 일부개정]</div>'
            '<a name="J2:0" id="J2:0"></a><div><p>제2조(정의) 뜻은 다음과 같다. <span>&lt;개정 2026. 9. 8.&gt;</span></p>'
            '<p>제2장 개인정보 보호정책의 수립 등</p></div>'
            '<a name="J29:0" id="J29:0"></a><div><p>제29조(안전조치의무) 조치를 하여야 한다. &lt;개정 2015. 7. 24.&gt;</p></div>'
            '<div id="arDivArea">부칙</div>')
    get = lambda url: (pop if "lsInfoP" in url else body).encode()
    v = WebLawFetcher(get=get).current(LAW)
    assert v.mst == "283839" and v.effective == "20260911"
    assert v.promulgated == "20260908" and v.key == "283839:20260908"      # 시행 전 개정(2026.9.8) 반영
    assert v.titles == {"2": "정의", "29": "안전조치의무"} and v.changed == ("2",)
    assert "제2장" not in v.articles["2"]                                   # 다음 장 제목은 빼기
    api_style = {"2": v.articles["2"].replace("“", '"'), "29": "제29조(안전조치의무) 조치를 하여야 한다. <개정 2015.7.24>",
                 "8": "제8조 삭제 <2020.2.4>"}
    assert diff_articles(api_style, v.articles) == []                       # 표기 차이 · 삭제 조문은 변경 아님
