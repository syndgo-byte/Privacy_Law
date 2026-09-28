"""법제처 국가법령정보 Open API(DRF) 에서 현행 법령과 조문을 가져온다.

OC(인증키)는 open.law.go.kr 에서 신청한 이메일 ID. 환경변수 LAW_OC 로 넘긴다.
http 호출은 get(url) -> bytes 로 주입할 수 있다(테스트 · 프록시).
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

API = "https://www.law.go.kr/DRF"


class LawFetchError(RuntimeError):
    pass


@dataclass(frozen=True)
class LawVersion:
    name: str
    mst: str              # 법령일련번호 — 개정될 때마다 바뀐다
    promulgated: str      # 공포일자 YYYYMMDD
    effective: str        # 시행일자 YYYYMMDD
    articles: dict[str, str] = field(default_factory=dict)   # "15" / "15의2" → 조문 전체 텍스트


def _default_get(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=20) as r:
        return r.read()


def _as_list(v):
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def _flatten(v) -> str:
    """항 · 호 · 목이 중첩 dict/list 로 오므로 글자만 이어 붙인다."""
    if isinstance(v, str):
        return v.strip()
    if isinstance(v, list):
        return "\n".join(filter(None, (_flatten(i) for i in v)))
    if isinstance(v, dict):
        return "\n".join(filter(None, (_flatten(i) for i in v.values())))
    return ""


class LawFetcher:
    def __init__(self, oc: str, get=_default_get):
        if not oc:
            raise LawFetchError("LAW_OC(법제처 Open API 인증키)가 없습니다")
        self.oc = oc
        self._get = get

    def _json(self, path: str, **params) -> dict:
        url = f"{API}/{path}?" + urllib.parse.urlencode({"OC": self.oc, "type": "JSON", **params})
        try:
            raw = self._get(url)
            return json.loads(raw)
        except Exception as e:
            raise LawFetchError(f"법제처 API 호출 실패 ({path}): {e}") from e

    def current(self, name: str) -> LawVersion:
        """법령명으로 현행 법령의 버전 정보(조문 없이)."""
        data = self._json("lawSearch.do", target="law", query=name, display=20)
        rows = _as_list(data.get("LawSearch", {}).get("law"))
        exact = [r for r in rows if r.get("법령명한글", "").replace(" ", "") == name.replace(" ", "")]
        if not exact:
            raise LawFetchError(f"법령을 찾지 못했습니다: {name}")
        r = exact[0]
        return LawVersion(name=name, mst=str(r["법령일련번호"]),
                          promulgated=str(r.get("공포일자", "")), effective=str(r.get("시행일자", "")))

    def with_articles(self, v: LawVersion) -> LawVersion:
        """해당 버전의 조문 본문까지 채운다."""
        data = self._json("lawService.do", target="law", MST=v.mst)
        units = _as_list(data.get("법령", {}).get("조문", {}).get("조문단위"))
        articles: dict[str, str] = {}
        for u in units:
            if u.get("조문여부") not in (None, "조문"):
                continue   # 장 · 절 제목
            no = str(u.get("조문번호", "")).strip()
            branch = str(u.get("조문가지번호", "") or "").strip()
            key = f"{no}의{branch}" if branch and branch != "0" else no
            parts = [u.get("조문내용"), u.get("항")]
            articles[key] = _flatten(parts)
        return LawVersion(v.name, v.mst, v.promulgated, v.effective, articles)
