"""법제처 국가법령정보 Open API(DRF) 에서 현행 법령과 조문을 가져온다.

OC(인증키)는 keys.get_key("LAW_OC") — 환경변수 LAW_OC 또는 .secrets.json (privacy_law.keys 참고).
http 호출은 get(url) -> bytes 로 주입할 수 있다(테스트 · 프록시).
"""
from __future__ import annotations

import json
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
