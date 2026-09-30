"""법령 밖의 기준을 모은다 — 개인정보보호위원회(pipc.go.kr) 게시판과 다른 회사의 개인정보 처리방침.

  PIPC 게시판: 보도자료 · 결과의 공표(행정처분) · 고시 · 안내서 · 결정문 — 새 글만 골라 알린다.
  결과의 공표: 첨부 PDF(처분 결과표)를 받아 '위반 조항'을 뽑고 조문별 건수를 쌓는다 → requirements.case_gaps.
  처리방침 벤치마크: benchmarks.json 의 공개 처리방침을 받아 버전 변화와 '다른 곳은 다루는데 우리는 없는 주제'를 본다.

상태는 root/sources/state.json. http 는 get(url) -> bytes 로 주입(테스트).
PDF 글자 추출은 pypdf (없으면 위반 조항 추출만 건너뛰고 errors 에 남긴다).
"""
from __future__ import annotations

import hashlib
import html as _html
import io
import json
import re
import urllib.request
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path

PIPC = "https://www.pipc.go.kr"
UA = "Mozilla/5.0 (privacy_law compliance watcher)"
BENCHMARKS = Path(__file__).resolve().parent / "benchmarks.json"
PARSER_VERSION = 2          # violated_articles 를 바꾸면 올린다
MAX_TRIES = 3               # 처분 첨부 받기 재시도 횟수(12시간 간격)

# 게시판 이름 → (bbsId, mCode)
PIPC_BOARDS = {
    "보도자료": ("BS074", "C020010000"),
    "결과의 공표": ("BS258", "C010040000"),
    "고시": ("BS216", "D010020010"),
    "안내서": ("BS217", "D010030000"),
    "결정문·사례집": ("BS231", "D070010010"),
}

# 제목에 이런 말이 있으면 동의 문서 · 서비스 점검과 관련 있는 글로 본다
RELEVANT = {
    "처리방침": r"처리\s*방침",
    "동의": r"동의",
    "위탁": r"위탁|수탁",
    "보호책임자": r"보호\s*책임자",
    "유출": r"유출",
    "과징금·과태료": r"과징금|과태료|처분|공표",
    "가명정보": r"가명",
    "국외 이전": r"국외\s*이전|해외\s*이전",
    "쿠키·행태정보": r"쿠키|행태\s*정보|맞춤형\s*광고",
    "자동화된 결정·AI": r"자동화된\s*결정|인공지능|AI",
    "아동": r"아동|14세",
    "안전조치": r"안전\s*조치|안전성\s*확보",
}

# 처리방침 본문에서 찾는 주제 (벤치마크 비교용). 괄호 안은 해당될 때만 필요한 practice 키
TOPICS = {
    "처리 근거(법적 근거)": (r"법적\s*근거|처리\s*근거", None),
    "가명정보": (r"가명정보", "pseudonymous"),
    "행태정보·맞춤형 광고": (r"행태\s*정보|맞춤형\s*광고", "cookies.ads"),
    "개인위치정보": (r"위치\s*정보", "location"),
    "민감정보": (r"민감정보", "sensitive"),
    "자동화된 결정": (r"자동화된\s*결정", "automated_decision"),
    "국내대리인": (r"국내\s*대리인", "foreign_operator"),
    "국외 이전": (r"국외\s*(로\s*)?이전", "overseas"),
    "만 14세 미만 아동": (r"14세\s*미만|아동", "children"),
    "안전성 확보 조치": (r"안전성\s*확보\s*조치", None),
    "자동 수집 장치(쿠키)": (r"쿠키|자동\s*수집\s*장치", None),
    "권익침해 구제": (r"권익\s*침해|분쟁\s*조정|침해\s*신고", None),
    "처리방침 변경 고지": (r"(처리\s*방침|방침)의?\s*변경|개정\s*전\s*고지", None),
}

_ROW = re.compile(r'<td class="boardTitle">.*?<a href="([^"]+)"[^>]*>(.*?)</a>.*?</td>\s*<td>(.*?)</td>\s*'
                  r'<td>(\d{4}-\d{2}-\d{2})</td>', re.S)
_DOWN = re.compile(r"fn_egov_downFile\('([^']+)','([^']+)','([^']*)'\)")
# '(舊) 보호법' / '법' + 이어지는 조항 나열 (제N조의M제K항제J호, '및 제3항', 쉼표 등).
# 실제 결과표 표기: "보호법 제21조제1항 제24조의2제2항", "舊 보호법* 제34조제1항‧ 제3항", "법 제34조 제1항"
# ('정보통신망법' 같은 다른 법의 '법' 은 앞 글자가 한글이라 제외)
_SEP = r"(?:및|,|ㆍ|·|‧)"
_CLAUSE = r"제\d+조(?:의\d+)?(?:\s*제\d+항)?(?:\s*제\d+호)?"
_LAW_REF = re.compile(r"(舊\s*)?(?:(?:개인정보\s*)?보호법|(?<![가-힣])법)\*?\s*((?:" + _CLAUSE
                      + r"(?:\s*" + _SEP + r"?\s*제\d+항)*\s*" + _SEP + r"?\s*)+)")


class SourceError(RuntimeError):
    pass


@dataclass(frozen=True)
class Notice:
    board: str
    ntt_id: str
    title: str
    dept: str
    date: str
    url: str

    def to_dict(self) -> dict:
        return asdict(self)


def _default_get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", " ", s))).strip()


def _fetch(get, url: str) -> bytes:
    try:
        return get(url)
    except Exception as e:
        raise SourceError(f"{url}: {e}") from e


# ---- 개인정보보호위원회 게시판 ----

def fetch_board(board: str, get=_default_get) -> list[Notice]:
    bbs, mcode = PIPC_BOARDS[board]
    raw = _fetch(get, f"{PIPC}/np/cop/bbs/selectBoardList.do?bbsId={bbs}&mCode={mcode}").decode("utf-8", "replace")
    rows = _ROW.findall(raw)
    if not rows:
        raise SourceError(f"개인정보보호위원회 '{board}' 목록을 읽지 못함 (페이지 구조가 바뀌었을 수 있음)")
    out = []
    for href, title, dept, date in rows:
        href = _html.unescape(href)
        m = re.search(r"nttId=(\d+)", href)
        out.append(Notice(board, m.group(1) if m else href, _clean(title), _clean(dept), date, PIPC + href))
    return out


def relevance(title: str) -> list[str]:
    return [k for k, p in RELEVANT.items() if re.search(p, title)]


def pdf_text(data: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as e:
        raise SourceError("PDF 글자 추출에 pypdf 가 필요합니다 (pip install pypdf)") from e
    try:
        return "\n".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(data)).pages)
    except Exception as e:
        raise SourceError(f"PDF 읽기 실패: {e}") from e


def hwpx_text(data: bytes) -> str:
    """한글 HWPX(zip + XML) 본문 글자."""
    import zipfile
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
        xml = "".join(z.read(n).decode("utf-8", "replace") for n in sorted(z.namelist())
                      if re.fullmatch(r"Contents/section\d+\.xml", n))
    except (zipfile.BadZipFile, KeyError) as e:
        raise SourceError(f"HWPX 읽기 실패: {e}") from e
    return " ".join(_html.unescape(t) for t in re.findall(r"<hp:t>([^<]*)</hp:t>", xml))


def violated_articles(text: str) -> Counter:
    """처분 결과표 글자에서 위반 조항(현행 개인정보 보호법 기준, 조 단위).

    결과표의 '위반조항' 칸은 "보호법 제21조제1항 제24조의2제2항 제29조" 처럼 '보호법' 뒤에 조항이 이어진다.
    '舊 보호법' 뒤의 조항들은 개정 전 번호 체계라 현행 점검에 쓰지 않는다. 위반내용 문장 속 조문은 세지 않는다."""
    c: Counter = Counter()
    for m in _LAW_REF.finditer(text):
        if m.group(1):
            continue
        for a in re.finditer(r"제(\d+)조(?:의(\d+))?", m.group(2)):
            c[a.group(1) + (f"의{a.group(2)}" if a.group(2) else "")] += 1
    return c


def fetch_disposition(n: Notice, get=_default_get) -> dict:
    """결과의 공표 글 → 첨부 PDF → 위반 조항."""
    page = _fetch(get, n.url).decode("utf-8", "replace")
    files = _DOWN.findall(page)
    if not files:
        raise SourceError(f"결과의 공표 {n.ntt_id}: 첨부파일을 찾지 못함")
    arts: Counter = Counter()
    snippet = ""
    unparsed = []            # 글자를 뽑을 수 없는 첨부(이미지 등) — 사람이 봐야 함
    for atch, sn, ext in files:
        ext = ext.lower()
        if ext not in ("pdf", "hwpx"):
            unparsed.append(ext or "?")
            continue
        data = _fetch(get, f"{PIPC}/np/cmm/fms/FileDown.do?atchFileId={atch}&fileSn={sn}")
        if ext == "pdf" and not data.startswith(b"%PDF") or ext == "hwpx" and not data.startswith(b"PK"):
            raise SourceError(f"결과의 공표 {n.ntt_id}: 첨부({ext})를 받지 못함 (다른 응답이 옴)")
        text = pdf_text(data) if ext == "pdf" else hwpx_text(data)
        body = text.split("처분내용", 1)[-1]     # 표 머리말 이후
        arts += violated_articles(body)
        snippet = snippet or re.sub(r"\s+", " ", body)[:600]
    return {"ntt_id": n.ntt_id, "title": n.title, "date": n.date, "url": n.url, "articles": dict(arts),
            "unparsed": unparsed, "snippet": snippet}


# ---- 다른 회사 처리방침 ----

def load_benchmarks(path: Path = BENCHMARKS) -> list[dict]:
    return json.loads(Path(path).read_text(encoding="utf-8")) if Path(path).exists() else []


def policy_topics(text: str) -> list[str]:
    return [k for k, (p, _) in TOPICS.items() if re.search(p, text)]


def fetch_policy(b: dict, get=_default_get) -> dict:
    raw = _fetch(get, b["url"]).decode("utf-8", "replace")
    body = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", raw, flags=re.S)
    heads = [_clean(h) for h in re.findall(r"<h[1-4][^>]*>(.*?)</h[1-4]>", body, re.S)]
    text = _clean(body)
    if len(text) < 500:
        raise SourceError(f"{b['name']} 처리방침 본문이 너무 짧음 ({len(text)}자) — 스크립트로 그리는 페이지일 수 있음")
    ver = re.search(r"Ver\.?\s*[\d.]+|시행\s*일자\s*[:：]?\s*\d{4}[.\-년 ]\s*\d{1,2}[.\-월 ]\s*\d{1,2}일?", text)
    return {"name": b["name"], "url": b["url"], "hash": hashlib.sha256(text.encode()).hexdigest()[:16],
            "version": ver.group(0) if ver else "", "headings": [h for h in heads if h][:60],
            "topics": policy_topics(text)}


def compare_topics(ours_text: str, others: list[dict], practice: dict | None = None) -> list[dict]:
    """다른 처리방침들이 다루는데 우리 문구에는 없는 주제. 해당될 때만 필요한 주제는 practice 로 판단."""
    from .requirements import _truthy
    practice = practice or {}
    ours = set(policy_topics(ours_text))
    seen = Counter(t for o in others for t in o["topics"])
    out = []
    for topic, n in seen.most_common():
        if topic in ours:
            continue
        cond = TOPICS[topic][1]
        applies = cond is None or _truthy(practice, cond)
        out.append({"topic": topic, "sites": n, "of": len(others), "condition": cond,
                    "action": "기재 필요" if applies and cond else ("검토 권장" if cond is None else "해당 시에만")})
    return out


# ---- 한 바퀴 ----

def _load_state(root: Path) -> dict:
    p = Path(root) / "sources" / "state.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def _save_state(root: Path, state: dict) -> None:
    p = Path(root) / "sources" / "state.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)


def collect(root: Path, get=_default_get, benchmarks: list[dict] | None = None) -> dict:
    """결과: {"notices": [새 글], "dispositions": [새로 읽은 처분], "violations": {조문: 누적 건수},
              "policies": [벤치마크], "policy_changes": [...], "errors": [...]}"""
    state = _load_state(root)
    if state and state.get("parser") != PARSER_VERSION:
        # 위반 조항 추출 방식이 바뀌었으면 처분 공표를 다시 읽는다(옛 추출 결과가 통계에 남지 않게)
        state["dispositions"], state["retry"] = [], {}
    # seen: 게시판별로 이미 알린 글 / read: 위반 조항을 읽은 처분 / retry: 첨부 받기 실패 횟수
    seen = {k: set(v) for k, v in state.get("seen", {}).items()}
    disp = list(state.get("dispositions", []))
    read = {d["ntt_id"] for d in disp}
    retry: dict[str, int] = dict(state.get("retry", {}))
    first = not seen
    res = {"first_run": first, "notices": [], "dispositions": [], "policies": [], "policy_changes": [], "errors": []}

    for board in PIPC_BOARDS:
        try:
            rows = fetch_board(board, get)
        except SourceError as e:
            res["errors"].append(str(e))
            continue
        known = seen.setdefault(board, set())
        for n in rows:
            if n.ntt_id not in known:
                known.add(n.ntt_id)
                # 첫 실행은 기준만 잡는다(목록 첫 쪽 전체를 '새 글'로 쏟아내지 않음). 처분은 첫 실행에도 읽어 통계를 만든다.
                if not first:
                    res["notices"].append({**n.to_dict(), "topics": relevance(n.title)})
            if board != "결과의 공표" or n.ntt_id in read:
                continue
            try:
                d = fetch_disposition(n, get)
            except SourceError as e:
                retry[n.ntt_id] = retry.get(n.ntt_id, 0) + 1
                if retry[n.ntt_id] < MAX_TRIES:
                    res["errors"].append(f"{e} (다음 실행에 다시 시도 {retry[n.ntt_id]}/{MAX_TRIES})")
                    continue
                # 계속 실패 — 통계에서 빠진다는 것을 사람이 알도록 '읽지 못한 처분'으로 남긴다
                res["errors"].append(f"{e} ({MAX_TRIES}회 실패 — 수동 확인 대상으로 넘김)")
                d = {"ntt_id": n.ntt_id, "title": n.title, "date": n.date, "url": n.url, "articles": {},
                     "unparsed": [f"받기 실패: {e}"], "snippet": ""}
            retry.pop(n.ntt_id, None)
            read.add(n.ntt_id)
            res["dispositions"].append(d)

    disp += res["dispositions"]
    violations: Counter = Counter()
    for d in disp:
        violations.update({a: 1 for a in d["articles"]})   # 처분 공표 1건 안의 같은 조항은 1로 센다
    res["violations"] = dict(violations)

    old = {p["name"]: p for p in state.get("policies", [])}
    for b in (load_benchmarks() if benchmarks is None else benchmarks):
        try:
            p = fetch_policy(b, get)
        except SourceError as e:
            res["errors"].append(str(e))
            if b["name"] in old:
                res["policies"].append(old[b["name"]])
            continue
        prev = old.get(b["name"])
        if prev and prev["hash"] != p["hash"]:
            res["policy_changes"].append({"name": p["name"], "url": p["url"], "from": prev.get("version", ""),
                                          "to": p["version"],
                                          "topics_added": sorted(set(p["topics"]) - set(prev["topics"])),
                                          "topics_removed": sorted(set(prev["topics"]) - set(p["topics"]))})
        res["policies"].append(p)

    _save_state(root, {"parser": PARSER_VERSION, "seen": {k: sorted(v) for k, v in seen.items()}, "dispositions": disp,
                       "retry": retry, "policies": res["policies"]})
    return res
