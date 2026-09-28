"""두 법령 버전의 조문을 조 단위로 비교하고, 영향받는 동의 문서를 고른다."""
from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class ArticleChange:
    article: str          # "15", "22의2"
    kind: str             # added | removed | modified
    before: str
    after: str

    def to_dict(self) -> dict:
        return asdict(self)


def _norm(text: str) -> str:
    return " ".join(text.split())


def diff_articles(old: dict[str, str], new: dict[str, str]) -> list[ArticleChange]:
    """공백 차이는 무시한다(법제처 응답의 줄바꿈이 버전마다 다름)."""
    out = []
    for no in sorted(old.keys() | new.keys(), key=_order):
        a, b = old.get(no), new.get(no)
        if a is None:
            out.append(ArticleChange(no, "added", "", b))
        elif b is None:
            out.append(ArticleChange(no, "removed", a, ""))
        elif _norm(a) != _norm(b):
            out.append(ArticleChange(no, "modified", a, b))
    return out


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
