"""법제처에서 현행 법령과 조문을 가져온다.

  WebLawFetcher (기본) — www.law.go.kr 법령 본문 화면을 읽는다. 인증키 필요 없음(robots.txt 전체 허용).
                         공포 기준(ancYnChk=1)이라 시행일이 뒤인 조항도 포함 — API 와 같은 범위. 삭제 조문은 화면에 없다.
  LawFetcher           — 국가법령정보 Open API(DRF). LAW_OC 를 저장했을 때만 쓴다.
둘 다 current(name) / with_articles(version) 로 같은 LawVersion 을 준다. mst 는 법령일련번호(lsiSeq)로 같은 값.

OC(인증키)는 keys.get_key("LAW_OC") — 환경변수 LAW_OC 또는 .secrets.json (privacy_law.keys 참고).
http 호출은 get(url) -> bytes 로 주입할 수 있다(테스트 · 프록시).
"""
from __future__ import annotations

import html
import json
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

API = "https://www.law.go.kr/DRF"
UA = "Mozilla/5.0 (privacy_law law watcher)"


class LawFetchError(RuntimeError):
    pass


@dataclass(frozen=True)
class LawVersion:
    name: str
    mst: str              # 법령일련번호 — 개정될 때마다 바뀐다
    promulgated: str      # 공포일자 YYYYMMDD
    effective: str        # 시행일자 YYYYMMDD
    articles: dict[str, str] = field(default_factory=dict)   # "15" / "15의2" → 조문 본문(항 · 호 · 목 포함)
    titles: dict[str, str] = field(default_factory=dict)     # "15" → "개인정보의 수집ㆍ이용"
    changed: tuple[str, ...] = ()                            # 이번 개정에서 바뀐 조문(법제처 조문변경여부=Y)

    @property
    def key(self) -> str:
        """같은 버전인지 비교하는 값. 웹은 시행 예정 개정이 같은 lsiSeq 안에 들어오므로 공포일까지 본다."""
        return f"{self.mst}:{self.promulgated}"


def _default_get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def _as_list(v):
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


_TEXT_KEYS = ("조문내용", "항내용", "호내용", "목내용")
_CHILD_KEYS = ("항", "호", "목")


def article_text(unit) -> str:
    """조문 단위 JSON 에서 본문 글자만 순서대로 모은다(항번호 · 조문키 같은 메타는 뺀다)."""
    out: list[str] = []

    def walk(x):
        if isinstance(x, list):
            for i in x:
                walk(i)
        elif isinstance(x, dict):
            for k in _TEXT_KEYS:
                v = x.get(k)
                if isinstance(v, str):
                    out.append(v.strip())
                elif isinstance(v, list):   # 표 · 여러 줄 본문이 [[...]] 로 오는 경우
                    out.extend("".join(map(str, i)) if isinstance(i, list) else str(i) for i in v)
            for k in _CHILD_KEYS:
                if k in x:
                    walk(x[k])

    walk(unit)
    return "\n".join(s for s in out if s)


def article_key(unit) -> str:
    no = str(unit.get("조문번호", "")).strip()
    branch = str(unit.get("조문가지번호", "") or "").strip()
    return f"{no}의{branch}" if branch and branch != "0" else no


class LawFetcher:
    def __init__(self, oc: str, get=_default_get):
        if not oc:
            raise LawFetchError("LAW_OC(법제처 Open API 인증키)가 없습니다. https://open.law.go.kr 에서 신청하고 "
                                "`python -m privacy_law.keys set LAW_OC <OC>` 로 저장하세요.")
        self.oc = oc
        self._get = get

    def _json(self, path: str, **params) -> dict:
        url = f"{API}/{path}?" + urllib.parse.urlencode({"OC": self.oc, "type": "JSON", **params})
        try:
            data = json.loads(self._get(url))
        except Exception as e:
            raise LawFetchError(f"법제처 API 호출 실패 ({path}): {e}") from e
        # 키 · IP 검증 실패는 200 + {"result": "...실패...", "msg": "..."} 로 온다
        if isinstance(data, dict) and "result" in data and "LawSearch" not in data and "법령" not in data:
            raise LawFetchError(f"법제처 인증 실패: {data.get('result')} {data.get('msg', '')}".strip())
        return data

    def current(self, name: str) -> LawVersion:
        """법령명으로 현행 법령의 버전 정보(조문 없이)."""
        data = self._json("lawSearch.do", target="law", query=name, display=20)
        rows = _as_list(data.get("LawSearch", {}).get("law"))
        exact = [r for r in rows if r.get("법령명한글", "").replace(" ", "") == name.replace(" ", "")
                 and r.get("현행연혁코드", "현행") == "현행"]
        if not exact:
            raise LawFetchError(f"법령을 찾지 못했습니다: {name}")
        r = exact[0]
        return LawVersion(name=name, mst=str(r["법령일련번호"]),
                          promulgated=str(r.get("공포일자", "")), effective=str(r.get("시행일자", "")))

    def with_articles(self, v: LawVersion) -> LawVersion:
        """해당 버전의 조문 본문 · 제목 · 이번 개정 변경 여부까지 채운다."""
        data = self._json("lawService.do", target="law", MST=v.mst)
        units = _as_list(data.get("법령", {}).get("조문", {}).get("조문단위"))
        if not units:
            raise LawFetchError(f"조문이 비어 있습니다: {v.name} (MST {v.mst})")
        articles: dict[str, str] = {}
        titles: dict[str, str] = {}
        changed: list[str] = []
        for u in units:
            if u.get("조문여부") not in (None, "조문"):
                continue   # 장 · 절 제목
            key = article_key(u)
            articles[key] = article_text(u)
            titles[key] = str(u.get("조문제목", "") or "")
            if u.get("조문변경여부") == "Y":
                changed.append(key)
        return LawVersion(v.name, v.mst, v.promulgated, v.effective, articles, titles, tuple(changed))


WEB = "https://www.law.go.kr/LSW"
_POP = re.compile(r"lsPopViewAll2\('(\d+)',\s*'[^']*',\s*'[^']*',\s*'(\d{8})'")
_HEAD = re.compile(r"\[시행\s*(\d{4})\.\s*(\d{1,2})\.\s*(\d{1,2})\.?\]\s*\[[^\]]*?(\d{4})\.\s*(\d{1,2})\.\s*(\d{1,2})\.?,[^\]]*\]")
_ANCHOR = re.compile(r'<a name="J(\d+):(\d+)"[^>]*></a>')
_NOTE_DATES = re.compile(r"[<\[](?:[^<>\[\]]*?)(?:개정|신설|본조신설|전문개정)\s*([^>\]]*)[>\]]")
_YMD = re.compile(r"(\d{4})\.\s*(\d{1,2})\.\s*(\d{1,2})")
_PART = re.compile(r"^제\d+(?:장|절|관)(?:의\d+)?\s")
_TITLE = re.compile(r"^제\d+조(?:의\d+)?\s*\(([^)]*)\)")


def _plain(fragment: str) -> str:
    text = re.sub(r"<(?:br|/p|/div)\s*/?>", "\n", fragment)
    text = html.unescape(re.sub(r"<[^>]+>", " ", text))
    lines = (" ".join(line.split()) for line in text.splitlines())
    return "\n".join(line for line in lines if line)


def _ymd(y, m, d) -> str:
    return f"{int(y):04d}{int(m):02d}{int(d):02d}"


class WebLawFetcher:
    """law.go.kr 법령 화면(lsInfoP.do → 본문 lsInfoR.do)에서 현행 법령을 읽는다."""

    def __init__(self, get=_default_get):
        self._get = get

    def _page(self, path: str, **params) -> str:
        url = f"{WEB}/{path}?" + urllib.parse.urlencode(params)
        try:
            return self._get(url).decode("utf-8", "replace")
        except Exception as e:
            raise LawFetchError(f"법제처 웹 호출 실패 ({path}): {e}") from e

    def current(self, name: str) -> LawVersion:
        page = self._page("lsInfoP.do", lsNm=name)
        m = _POP.search(page)
        if not m:
            raise LawFetchError(f"법령을 찾지 못했습니다(웹): {name}")
        return self.with_articles(LawVersion(name=name, mst=m.group(1), promulgated="", effective=m.group(2)))

    def with_articles(self, v: LawVersion) -> LawVersion:
        body = self._page("lsInfoR.do", lsiSeq=v.mst, efYd=v.effective, chrClsCd="010202", ancYnChk="1")
        head = _HEAD.search(_plain(body[:20000]).replace("\n", " "))
        effective = _ymd(*head.group(1, 2, 3)) if head else v.effective
        promulgated = _ymd(*head.group(4, 5, 6)) if head else v.promulgated
        end = body.find('id="arDivArea"')          # 부칙 시작 — 본칙 조문만
        body = body[:body.rfind("<", 0, end)] if end > 0 else body
        marks = list(_ANCHOR.finditer(body))
        articles: dict[str, str] = {}
        titles: dict[str, str] = {}
        for i, m in enumerate(marks):
            seg = body[m.end(): marks[i + 1].start() if i + 1 < len(marks) else len(body)]
            # 다음 장 · 절 제목은 앞 조문 끝에 붙어 나온다
            text = "\n".join(line for line in _plain(seg).splitlines() if not _PART.match(line))
            if not text.startswith("제"):
                continue                              # 장 · 절 제목
            no, branch = m.group(1), m.group(2)
            key = f"{int(no)}의{int(branch)}" if branch != "0" else str(int(no))
            t = _TITLE.match(text)
            if not t:
                continue
            articles[key] = text
            titles[key] = t.group(1)
        if not articles:
            raise LawFetchError(f"조문이 비어 있습니다(웹): {v.name} (lsiSeq {v.mst})")
        # 공포 기준 화면에는 아직 시행 전인 개정도 들어 있다 — 조문 연혁 표기의 가장 늦은 날짜가 실제 마지막 공포일
        dated = {k: {_ymd(*d) for n in _NOTE_DATES.findall(a) for d in _YMD.findall(n)} for k, a in articles.items()}
        latest = max([promulgated, *(d for ds in dated.values() for d in ds)])
        changed = tuple(k for k, ds in dated.items() if latest in ds)
        return LawVersion(v.name, v.mst, latest, effective, articles, titles, changed)
