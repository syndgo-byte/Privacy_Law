"""두 법령 버전의 조문을 조 단위로 비교하고, 영향받는 동의 문서를 고른다."""
from __future__ import annotations

import re

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class ArticleChange:
    article: str          # "15", "22의2"
    kind: str             # added | removed | modified
    before: str
    after: str

    def to_dict(self) -> dict:
        return asdict(self)


_DATE = re.compile(r"(\d{4})\.\s*(\d{1,2})\.\s*(\d{1,2})\.?")
_NOTE = re.compile(r"\[[^\]]*(?:개정|신설|이동|삭제|종전)[^\]]*\]")    # [전문개정 2012. 8. 13.] 등 연혁 표기
_QUOTES = str.maketrans({"“": '"', "”": '"', "‘": "'", "’": "'"})


def _norm(text: str) -> str:
    """출처(API · 웹 화면)마다 다른 표기를 맞춘다: 공백 · 따옴표 모양 · 날짜 점 · 연혁 표기. 글자 내용만 비교."""
    text = _NOTE.sub("", text.translate(_QUOTES))
    text = _DATE.sub(lambda m: f"{m[1]}.{int(m[2])}.{int(m[3])}", text)
    return "".join(text.split())


def diff_articles(old: dict[str, str], new: dict[str, str]) -> list[ArticleChange]:
    """공백 차이는 무시한다(법제처 응답의 줄바꿈이 버전마다 다름)."""
    out = []
    for no in sorted(old.keys() | new.keys(), key=_order):
        a, b = old.get(no), new.get(no)
        if _deleted(a) and (b is None or _deleted(b)) or (a is None and _deleted(b)):
            continue   # 삭제 조문: API 는 '제8조 삭제' 로 주고 웹 화면은 아예 안 보여 준다 — 같은 상태
        if a is None:
            out.append(ArticleChange(no, "added", "", b))
        elif b is None:
            out.append(ArticleChange(no, "removed", a, ""))
        elif _norm(a) != _norm(b):
            out.append(ArticleChange(no, "modified", a, b))
    return out


_DELETED = re.compile(r"^제\d+조(?:의\d+)?\s*삭제")


def _deleted(text: str | None) -> bool:
    return bool(text) and bool(_DELETED.match(text))


def _order(no: str):
    head, _, branch = no.partition("의")
    return (int(head) if head.isdigit() else 10**6, int(branch) if branch.isdigit() else 0, no)


def affected_documents(changes: list[ArticleChange], mapping: dict[str, list[str]]) -> dict[str, list[ArticleChange]]:
    """mapping = {"15": ["collect", "privacy"], ...}. 매핑에 없는 조문은 문서에 영향 없음으로 본다."""
    out: dict[str, list[ArticleChange]] = {}
    for c in changes:
        for doc in mapping.get(c.article, []):
            out.setdefault(doc, []).append(c)
    return out
