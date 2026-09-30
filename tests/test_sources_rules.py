"""키 · 조문 파싱 · 프로필 · 점검 규칙 · 외부 기준 수집.

fixtures/ 는 모두 실제 응답을 잘라 둔 것이다(2026-09-30 수집):
  law_pipa_283839.json   법제처 DRF lawService.do?MST=283839 (개인정보 보호법) 중 규칙 근거 조문
  pipc_list_BS258.html   개인정보보호위원회 '결과의 공표' 목록 표
  pipc_detail_12454.html 결과의 공표 nttId=12454 첨부 스크립트 부분
  disp_12454.pdf         그 첨부(처분 결과표) — 경성대학교 제29조 · 제35조제3항, 양평군청 舊 제28조제1항
  disp_12334.hwpx        nttId=12334 첨부(한글) — 법원행정처 제21조 · 제24조의2 · 제29조 등
  policy_naver/kakao.html 네이버 · 카카오 개인정보 처리방침 (script · style 제거)
PRIVACY_LAW_LIVE=1 이면 실제 사이트를 부르는 테스트도 돈다.
"""
import json
import os
import re
import shutil
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from privacy_law import DEFAULT_DIR, ConsentBook, keys
from privacy_law import sources as src
from privacy_law.law_fetcher import LawFetcher, LawFetchError
from privacy_law.profile import ServiceProfile
from privacy_law.requirements import (BASIS_FILE, article_hash, case_gaps, check_book, check_text,
                                      load_basis, stale_rules)
from privacy_law.watch import mapping, mapping_issues, run

FX = Path(__file__).parent / "fixtures"
LAW = "개인정보 보호법"
live = pytest.mark.skipif(os.environ.get("PRIVACY_LAW_LIVE") != "1", reason="PRIVACY_LAW_LIVE=1 일 때만")

# 법제처가 키 · IP 검증에 실패했을 때 실제로 돌려주는 응답
AUTH_FAIL = {"result": "사용자 정보 검증에 실패하였습니다.",
             "msg": "OPEN API 호출 시 사용자 검증을 위하여 정확한 서버장비의 IP주소 및 도메인주소를 등록해 주세요."}

DEMO = {"service": "데모샵", "practice": {
    "service_desc": "온라인 쇼핑몰 주문 · 배송",
    "collect_items": ["아이디", "비밀번호", "이름", "휴대전화번호", "배송지 주소"],
    "processors": [{"name": "CJ대한통운", "task": "상품 배송"}],
    "officer": {"name": "홍길동", "dept": "개인정보보호팀", "phone": "02-000-0000", "email": "privacy@example.com"},
    "cookies": {"session": True, "analytics": False, "ads": False},
    "safeguards": ["접근 권한 관리"], "rights_channel": "계정 설정", "destruction": "탈퇴 후 5일 이내",
    "handlers_training": "연 1회", "breach_response": "72시간 이내",
    "marketing": {"channels": ["email"], "reconfirm_every_2y": True}}}


@pytest.fixture(autouse=True)
def _no_real_keys(monkeypatch):
    for k in ("LAW_OC", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(k, raising=False)


class RealLawAPI:
    """실제 DRF 응답(fixture)을 돌려준다."""

    def __init__(self):
        self.law = (FX / "law_pipa_283839.json").read_bytes()

    def __call__(self, url):
        q = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
        if "lawSearch" in url:
            rows = [{"법령명한글": q["query"], "법령일련번호": "283839", "현행연혁코드": "현행",
                     "공포일자": "20260310", "시행일자": "20260911"}]
            return json.dumps({"LawSearch": {"law": rows}}, ensure_ascii=False).encode()
        return self.law


def fake_web(url: str) -> bytes:
    """pipc.go.kr · 처리방침 요청을 실제 응답 fixture 로."""
    if "selectBoardList" in url:
        if "BS258" in url:
            return (FX / "pipc_list_BS258.html").read_bytes()
        return b"<table><tbody></tbody></table>"      # 다른 게시판은 '구조 바뀜' 오류가 된다
    if "selectBoardArticle" in url:
        if "nttId=12454" in url:
            return (FX / "pipc_detail_12454.html").read_bytes()
        return b"<p>no file</p>"
    if "FileDown" in url:
        return (FX / "disp_12454.pdf").read_bytes()
    if "naver" in url:
        return (FX / "policy_naver.html").read_bytes()
    if "kakao" in url:
        return (FX / "policy_kakao.html").read_bytes()
    raise OSError(f"unexpected {url}")


BENCH = [{"name": "네이버", "url": "https://policy.naver.com/policy/privacy.html"},
         {"name": "카카오", "url": "https://www.kakao.com/policy/privacy"}]


# ---- 인증키 ----

def test_keys_file_env_precedence_and_mask(tmp_path, monkeypatch):
    assert keys.get_key("LAW_OC", tmp_path) == "" and keys.key_source("LAW_OC", tmp_path) == "none"
    keys.set_key("LAW_OC", "myoc1234", tmp_path)
    assert keys.get_key("LAW_OC", tmp_path) == "myoc1234" and keys.key_source("LAW_OC", tmp_path) == "file"
    monkeypatch.setenv("LAW_OC", "fromenv")
    assert keys.get_key("LAW_OC", tmp_path) == "fromenv" and keys.key_source("LAW_OC", tmp_path) == "env"
    assert keys.mask("myoc1234") == "my****34"
    with pytest.raises(KeyError):
        keys.set_key("NOPE", "x", tmp_path)
    keys.set_key("LAW_OC", "", tmp_path)
    monkeypatch.delenv("LAW_OC")
    assert keys.get_key("LAW_OC", tmp_path) == ""


def test_check_law_oc_reports_auth_failure(tmp_path):
    keys.set_key("LAW_OC", "wrong", tmp_path)
    r = keys.check_law_oc(tmp_path, get=lambda url: json.dumps(AUTH_FAIL, ensure_ascii=False).encode())
    assert not r["ok"] and "IP주소" in r["message"]
    ok = keys.check_law_oc(tmp_path, get=RealLawAPI())
    assert ok["ok"] and ok["law"]["mst"] == "283839"
    assert not keys.check_law_oc(tmp_path / "none")["ok"]


def test_watch_without_key_tells_how_to_set_it(tmp_path):
    r = run(root=tmp_path, sources=False)
    assert "LAW_OC" in r["errors"][0] and "privacy_law.keys set" in r["errors"][0]
    assert r["findings"]["(공용 문구)"]["summary"]["error"] > 0     # 법령을 못 받아도 문구 점검은 한다


# ---- 조문 파싱 (실제 응답) ----

def test_real_law_json_parses_titles_and_matches_rule_basis():
    f = LawFetcher("x", get=RealLawAPI())
    v = f.with_articles(f.current(LAW))
    assert "1" not in v.articles                            # '전문'(장 제목) 제외
    assert v.titles["30"] == "개인정보 처리방침의 수립 및 공개"
    assert v.titles["39의6"] == "소송기록 열람 등의 청구 통지 등"   # 예전 매핑(→ privacy)이 틀렸던 조문
    assert "31의2" in v.changed                             # 2026.3.10 개정 변경 조문
    basis = load_basis()["laws"][LAW]
    assert basis["_mst"] == "283839"
    for art in ("15", "22", "30", "31의2", "37의2"):
        assert basis[art]["hash"] == article_hash(v.articles[art]), art
    assert stale_rules({LAW: v.articles}) == []
    changed = {**v.articles, "30": v.articles["30"] + " 새 항"}
    assert {s["rule"] for s in stale_rules({LAW: changed})} >= {"P30-1", "P30-7"}


def test_auth_failure_response_raises():
    with pytest.raises(LawFetchError, match="인증 실패"):
        LawFetcher("bad", get=lambda url: json.dumps(AUTH_FAIL, ensure_ascii=False).encode()).current(LAW)


def test_mapping_title_check_catches_wrong_article():
    f = LawFetcher("x", get=RealLawAPI())
    v = f.with_articles(f.current(LAW))
    law = {"articles": {"30": {"docs": ["privacy"], "title": "개인정보 처리방침의 수립 및 공개"},
                        "39의6": {"docs": ["privacy"], "title": "개인정보의 파기에 대한 특례"},
                        "99": ["privacy"]}}
    issues = mapping_issues(LAW, mapping(law), v)
    assert [i["article"] for i in issues] == ["39의6", "99"]
    assert issues[0]["actual"] == "소송기록 열람 등의 청구 통지 등"


def test_shipped_watch_json_titles_are_well_formed():
    cfg = json.loads((Path(DEFAULT_DIR).parent / "watch.json").read_text(encoding="utf-8"))
    for law in cfg["laws"]:
        for art, m in mapping(law).items():
            assert re.fullmatch(r"\d+(의\d+)?", art) and m["docs"] and m["title"], (law["name"], art)
    pipa = mapping(cfg["laws"][0])
    assert "39의6" not in pipa and {"29", "31", "31의2", "28의2"} <= set(pipa)


# ---- 프로필 · 점검 ----

def test_profile_renders_practice_into_text():
    p = ServiceProfile.from_dict({"service": "S", "practice": {
        "overseas": [{"recipient": "AWS", "contact": "aws@x", "country": "미국", "items": "로그",
                      "when_how": "수시 · 네트워크", "purpose": "보관", "retention": "1년", "refuse": "탈퇴"}],
        "cookies": {"session": True, "ads": True},
        "consent_free": [{"items": "결제 기록", "basis": "전자상거래법 제6조"}]},
        "sections": {"privacy": "<h3>제10조의2 (위치정보)</h3><p>x</p>"}})
    html = ConsentBook(profile=p).html("privacy")
    assert "이전되는 국가: 미국" in html and "이전 거부 방법 · 효과: 탈퇴" in html
    assert "맞춤형 광고 · 행태 분석" in html and "이용 통계 분석 목적의 쿠키는 사용하지 않습니다" in html
    assert "결제 기록: 전자상거래법 제6조" in html and "제10조의2 (위치정보)" in html
    with pytest.raises(ValueError):
        ServiceProfile.from_dict({"service": "S", "practise": {}})


def test_base_texts_need_service_values_and_complete_profile_passes():
    base = check_book(ConsentBook())
    rules = {f.rule for f in base if f.severity == "error"}
    assert "VAR" in rules and "P30-6" in rules            # 서비스 값 없이 쓰면 안 됨(보호책임자 등)
    demo = check_book(ConsentBook(profile=ServiceProfile.from_dict(DEMO)))
    assert [f for f in demo if f.severity == "error"] == []
    assert {f.rule for f in demo} == {"C22-1"}            # 필수 동의 관행 경고만


def test_privacy_without_legal_basis_fails_22_3():
    html = ConsentBook(profile=ServiceProfile.from_dict(DEMO)).html("privacy")
    old = re.sub(r"<p>- 처리 근거:.*?</p>", "", html, flags=re.S)       # 2026-09-29 판 문구
    assert "P22-3" in {f.rule for f in check_text("privacy", old, DEMO["practice"])}
    assert "P22-3" not in {f.rule for f in check_text("privacy", html, DEMO["practice"])}


def test_conditional_rules_follow_practice():
    prof = ServiceProfile.from_dict({**DEMO, "practice": {**DEMO["practice"], "pseudonymous": True,
                                                          "foreign_operator": True,
                                                          "marketing": {"channels": ["sms"]}}})
    rules = {f.rule for f in check_book(ConsentBook(profile=prof))}
    assert {"P31의2", "M50-8"} <= rules


def test_marketing_must_stay_optional(tmp_path):
    texts = tmp_path / "t"
    shutil.copytree(DEFAULT_DIR, texts)
    docs = json.loads((texts / "documents.json").read_text(encoding="utf-8"))
    for d in docs:
        if d["key"] == "marketing":
            d["required"] = True
    (texts / "documents.json").write_text(json.dumps(docs, ensure_ascii=False), encoding="utf-8")
    assert "M22-5" in {f.rule for f in check_book(ConsentBook(texts_dir=texts, profile=ServiceProfile.from_dict(DEMO)))}


# ---- 개인정보보호위원회 · 처분 사례 (실제 응답) ----

def test_board_list_parses_real_rows():
    rows = src.fetch_board("결과의 공표", fake_web)
    assert len(rows) >= 10 and all(re.fullmatch(r"\d{4}-\d{2}-\d{2}", n.date) for n in rows)
    assert "12454" in {n.ntt_id for n in rows}
    with pytest.raises(src.SourceError, match="구조"):
        src.fetch_board("보도자료", fake_web)


def test_disposition_pdf_gives_current_articles_only():
    n = src.Notice("결과의 공표", "12454", "공표", "", "2026-09-08",
                   src.PIPC + "/np/cop/bbs/selectBoardArticle.do?bbsId=BS258&mCode=C010040000&nttId=12454")
    d = src.fetch_disposition(n, fake_web)
    assert d["articles"] == {"29": 1, "35": 1}          # 양평군청 舊 제28조는 빠짐
    assert "경성대학교" in d["snippet"]


def test_hwpx_and_article_patterns_from_real_tables():
    text = src.hwpx_text((FX / "disp_12334.hwpx").read_bytes()).split("처분내용", 1)[-1]
    assert dict(src.violated_articles(text)) == {"21": 1, "24의2": 1, "29": 1, "34": 1, "28": 1}
    # 실제 결과표 표기들
    assert dict(src.violated_articles("1 (주)재미스홈 舊 보호법* 제29조 안전조치의무 舊 보호법* 제34조제1항‧ 제3항")) == {}
    assert dict(src.violated_articles("2 성남시의료원 법 제34조 제1항 유출통지 의무 위반")) == {"34": 1}
    assert dict(src.violated_articles("정보통신망법 제50조 위반")) == {}
    assert dict(src.violated_articles("공무원 연금공단 보호법 제24조제3항, 제29조 안전조치의무")) == {"24": 1, "29": 1}


def test_case_gaps_point_to_missing_practice():
    gaps = case_gaps({"safeguards": ["x"]}, {"29": 4, "34": 2, "99": 1})
    assert [g["article"] for g in gaps] == ["34"] and gaps[0]["practice_key"] == "breach_response"


def test_policies_real_topics_and_gap_compare():
    nv = src.fetch_policy(BENCH[0], fake_web)
    kk = src.fetch_policy(BENCH[1], fake_web)
    assert nv["version"].startswith("Ver.") and "가명정보" in nv["topics"] and "개인위치정보" in nv["topics"]
    assert "국외 이전" in kk["topics"] and kk["version"].endswith("일")
    ours = ConsentBook(profile=ServiceProfile.from_dict(DEMO)).html("privacy")
    gaps = {g["topic"]: g for g in src.compare_topics(ours, [nv, kk], {**DEMO["practice"], "location": True})}
    assert gaps["개인위치정보"]["action"] == "기재 필요"       # 위치정보를 쓰는데 문구에 없음
    assert gaps["가명정보"]["action"] == "해당 시에만"
    assert "안전성 확보 조치" not in gaps                    # 우리 문구에도 있음


def test_collect_first_run_baselines_then_reports_new(tmp_path):
    r1 = src.collect(tmp_path, fake_web, BENCH)
    assert r1["first_run"] and r1["notices"] == []
    assert any(e for e in r1["errors"] if "보도자료" in e)
    assert r1["violations"].get("29") == 1 and len(r1["policies"]) == 2
    r2 = src.collect(tmp_path, fake_web, BENCH)
    # 첨부를 못 받은 처분은 재시도하지만 '새 글'로 다시 알리지는 않는다
    assert r2["notices"] == [] and r2["dispositions"] == [] and r2["violations"] == r1["violations"]
    assert any("다시 시도 2/3" in e for e in r2["errors"])
    # 목록에서 하나를 '처음 보는 글'로 만든다
    state = json.loads((tmp_path / "sources" / "state.json").read_text(encoding="utf-8"))
    state["seen"]["결과의 공표"].remove("12454")
    state["dispositions"] = [d for d in state["dispositions"] if d["ntt_id"] != "12454"]
    (tmp_path / "sources" / "state.json").write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    r3 = src.collect(tmp_path, fake_web, BENCH)
    assert [n["ntt_id"] for n in r3["notices"]] == ["12454"] and "과징금·과태료" in r3["notices"][0]["topics"]


def test_disposition_download_gives_up_into_manual_review(tmp_path):
    for _ in range(src.MAX_TRIES):
        r = src.collect(tmp_path, fake_web, BENCH)
    failed = [d for d in r["dispositions"] if d["unparsed"]]
    assert failed and all(d["articles"] == {} and "받기 실패" in d["unparsed"][0] for d in failed)
    assert src.collect(tmp_path, fake_web, BENCH)["dispositions"] == []      # 더는 시도하지 않음


def test_policy_change_detected(tmp_path):
    src.collect(tmp_path, fake_web, BENCH)

    def changed(url):
        b = fake_web(url)
        return b.replace("Ver.12.2".encode(), b"Ver.12.3") if "naver" in url else b
    r = src.collect(tmp_path, changed, BENCH)
    assert [(c["name"], c["to"]) for c in r["policy_changes"]] == [("네이버", "Ver.12.3")]


def test_parser_version_change_rereads_dispositions(tmp_path):
    src.collect(tmp_path, fake_web, BENCH)
    p = tmp_path / "sources" / "state.json"
    state = json.loads(p.read_text(encoding="utf-8"))
    state["parser"] = 1
    state["dispositions"][0]["articles"] = {"28": 1}      # 옛 파서의 잘못된 결과
    p.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    assert src.collect(tmp_path, fake_web, BENCH)["violations"] == {"29": 1, "35": 1}


# ---- 한 바퀴 ----

def test_watch_full_cycle_writes_report(tmp_path):
    config = tmp_path / "watch.json"
    config.write_text(json.dumps({"laws": [{"name": LAW, "articles": {
        "30": {"docs": ["privacy"], "title": "개인정보 처리방침의 수립 및 공개"},
        "39의6": {"docs": ["privacy"], "title": "개인정보의 파기에 대한 특례"}}}]}, ensure_ascii=False), encoding="utf-8")
    prof_dir = tmp_path / "profiles"
    prof_dir.mkdir()
    (prof_dir / "demo.json").write_text(json.dumps({**DEMO, "practice": {**DEMO["practice"], "breach_response": ""}},
                                                   ensure_ascii=False), encoding="utf-8")
    r = run(fetcher=LawFetcher("x", get=RealLawAPI()), root=tmp_path, config=config, get=fake_web, benchmarks=BENCH)
    assert r["checked"][0]["mst"] == "283839" and r["changed"] == []
    assert [i["article"] for i in r["mapping_issues"]] == ["39의6"]
    assert r["stale_rules"] == [] or all(s["law"] != LAW for s in r["stale_rules"])
    assert r["findings"]["데모샵"]["summary"]["error"] == 0
    assert r["case_gaps"]["데모샵"] == []                   # 29 · 35 이행 내역 있음
    assert {g["article"] for g in r["case_gaps"]["(공용 문구)"]} == {"29", "35"}
    assert "데모샵" in r["benchmark_gaps"]
    saved = json.loads((tmp_path / "reports" / "latest.json").read_text(encoding="utf-8"))
    assert saved["mapping_issues"] == r["mapping_issues"]


# ---- 실제 사이트 (PRIVACY_LAW_LIVE=1) ----

@live
def test_live_law_api_and_basis():
    f = LawFetcher(keys.get_key("LAW_OC") or "test")
    v = f.with_articles(f.current(LAW))
    assert v.titles["30"] == "개인정보 처리방침의 수립 및 공개"
    stale = stale_rules({LAW: v.articles})
    assert stale == [], f"규칙 근거 조문이 바뀜 — 규칙 검토 후 rebase: {stale}"


@live
def test_live_pipc_and_policies(tmp_path):
    r = src.collect(tmp_path, benchmarks=BENCH)
    assert r["violations"], r["errors"]
    assert len(r["policies"]) == 2
