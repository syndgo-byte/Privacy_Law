"""동의 문서가 법이 요구하는 내용을 담았는지, 서비스의 실제 처리(practice)와 문구가 맞는지 점검한다.

규칙마다 근거 조문이 있다. 규칙을 쓸 때 읽은 조문 본문의 해시를 requirements_basis.json 에 남겨 두고,
감시 때 현행 조문과 해시가 다르면 그 규칙을 '재검토 필요'로 알린다(법이 바뀌었는데 규칙이 옛 법 기준일 수 있음).

    python -m privacy_law.requirements            # 공용 문구 점검 (프로필 없이)
    python -m privacy_law.requirements <프로필.json>  # 서비스 프로필 점검
    python -m privacy_law.requirements rebase     # 규칙을 현행 조문 기준으로 다시 확인했을 때만 (LAW_OC 필요)
"""
from __future__ import annotations

import hashlib
import html as _html
import json
import re
import sys
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path

PIPA = "개인정보 보호법"
DECREE = "개인정보 보호법 시행령"
NETWORK = "정보통신망 이용촉진 및 정보보호 등에 관한 법률"
BASIS_FILE = Path(__file__).resolve().parent / "requirements_basis.json"


@dataclass(frozen=True)
class Rule:
    id: str
    doc: str                      # privacy / collect / marketing / practice
    law: str
    article: str                  # "30" / "22의2"
    clause: str                   # 표시용 "①6"
    title: str
    need: tuple[str, ...] = ()    # 모두 있어야 함(각 정규식 안에서 | 로 대안)
    forbid: tuple[str, ...] = ()  # 하나라도 있으면 문제
    when: str | None = None       # practice 조건이 참일 때만 (점 표기: "cookies.ads")
    severity: str = "error"       # error: 법정 기재 누락 · 사실과 다름 / warn: 권고 · 관행
    note: str = ""


R = Rule
RULES: tuple[Rule, ...] = (
    # ---- 개인정보 처리방침: 법 제30조①, 시행령 제31조① ----
    R("P30-1", "privacy", PIPA, "30", "①1", "개인정보의 처리 목적", need=(r"처리\s*목적",)),
    R("P30-2", "privacy", PIPA, "30", "①2", "처리 및 보유 기간", need=(r"보유\s*(및|·|ㆍ)?\s*이용\s*기간|보유\s*기간",)),
    R("P30-3", "privacy", PIPA, "30", "①3", "제3자 제공에 관한 사항", when="third_parties",
      need=(r"제3자", r"제공받는\s*자"), forbid=(r"제3자에게\s*제공하지\s*않습니다",),
      note="프로필에 제3자 제공이 있는데 문구가 '제공하지 않습니다'이면 사실과 다름"),
    R("P30-3의2", "privacy", PIPA, "30", "①3의2", "파기절차 · 파기방법 및 보존 근거 · 항목",
      need=(r"파기\s*절차", r"파기\s*방법", r"보존", r"\d+\s*(년|개월)")),
    R("P30-3의3", "privacy", PIPA, "30", "①3의3", "민감정보 공개 가능성 및 비공개 선택 방법", when="sensitive",
      need=(r"민감정보", r"비공개")),
    R("P30-4", "privacy", PIPA, "30", "①4", "처리 위탁에 관한 사항", when="processors",
      need=(r"위탁",), forbid=(r"위탁하지\s*않습니다",),
      note="프로필에 위탁 업체가 있는데 문구가 '위탁하지 않습니다'이면 사실과 다름"),
    R("P30-4의2", "privacy", PIPA, "30", "①4의2", "가명정보 처리에 관한 사항", when="pseudonymous", need=(r"가명정보",)),
    R("P30-5", "privacy", PIPA, "30", "①5", "정보주체 · 법정대리인의 권리 · 의무 및 행사방법",
      need=(r"열람", r"정정", r"삭제", r"처리\s*정지", r"법정대리인")),
    R("P30-6", "privacy", PIPA, "30", "①6", "보호책임자 성명(또는 담당 부서)과 전화번호 등 연락처",
      need=(r"개인정보\s*보호책임자", r"\d{2,4}-\d{3,4}(-\d{4})?|[\w.+-]+@[\w-]+\.[\w.]+"),
      forbid=(r"기재\s*필요",), note="서비스가 officer(성명 · 전화 · 이메일)를 넘겨야 한다"),
    R("P30-7", "privacy", PIPA, "30", "①7", "자동 수집 장치(쿠키)의 설치 · 운영 및 거부",
      need=(r"쿠키|자동\s*수집\s*장치", r"거부")),
    R("P30-7x", "privacy", PIPA, "30", "①7", "광고 · 행태 분석 쿠키 사용 사실", when="cookies.ads",
      forbid=(r"광고\s*·?\s*행태\s*분석\s*목적의\s*쿠키는\s*사용하지\s*않습니다",),
      note="프로필은 광고 쿠키를 쓰는데 문구는 안 쓴다고 함"),
    R("D31-1", "privacy", DECREE, "31", "①1", "처리하는 개인정보의 항목", need=(r"개인정보의?\s*항목",)),
    R("D31-2", "privacy", DECREE, "31", "①2", "국외 이전의 근거와 법 제28조의8② 각 호", when="overseas",
      need=(r"이전되는\s*국가", r"이전받는\s*자", r"이전\s*항목|이전되는\s*개인정보\s*항목", r"거부"),
      forbid=(r"국외로\s*이전하지\s*않습니다",)),
    R("D31-3", "privacy", DECREE, "31", "①3", "안전성 확보 조치에 관한 사항", need=(r"안전성\s*확보\s*조치",)),
    R("D31-4", "privacy", DECREE, "31", "①4", "국외에서 직접 수집 시 처리 국가명", when="collects_from_abroad",
      need=(r"국가",)),
    R("P22-3", "privacy", PIPA, "22", "③", "동의 없이 처리하는 개인정보의 항목과 법적 근거(구분 공개)",
      need=(r"법적\s*근거|처리\s*근거", r"제15조제1항제[2-7]호|법령상\s*의무|계약.{0,6}이행")),
    R("P31의2", "privacy", PIPA, "31의2", "④", "국내대리인 성명 · 주소 · 전화 · 이메일", when="foreign_operator",
      need=(r"국내대리인",)),
    R("P37의2", "privacy", PIPA, "37의2", "④", "자동화된 결정의 기준 · 절차 공개", when="automated_decision",
      need=(r"자동화된\s*결정",)),
    # ---- 수집 · 이용 동의: 법 제15조②, 제22조, 시행령 제17조 ----
    R("C15-1", "collect", PIPA, "15", "②1", "수집 · 이용 목적", need=(r"목적",)),
    R("C15-2", "collect", PIPA, "15", "②2", "수집 항목", need=(r"항목",)),
    R("C15-3", "collect", PIPA, "15", "②3", "보유 및 이용 기간", need=(r"보유\s*(및|·|ㆍ)?\s*이용\s*기간",)),
    R("C15-4", "collect", PIPA, "15", "②4", "동의 거부 권리와 거부 시 불이익",
      need=(r"동의하지\s*않을\s*권리|거부할\s*권리", r"불이익|제한")),
    R("C22-1", "collect", PIPA, "22", "①", "계약 이행 필수 정보에 대한 필수 동의 관행", severity="warn",
      forbid=(r"동의하지\s*않으시면\s*회원\s*가입이\s*제한",),
      note="2023.3.14 개정으로 계약 이행에 필요한 정보는 제15조제1항제4호로 동의 없이 처리할 수 있다. "
           "보호위원회는 '필수 동의' 관행 개선을 권고 — 동의 없이 처리하는 항목과 근거는 처리방침에 공개(제22조③). "
           "위법은 아니지만 문구 · 가입 흐름을 바꿀지 판단 필요"),
    # ---- 마케팅: 법 제22조①7 · ⑤, 정보통신망법 제50조 ----
    R("M22-5", "marketing", PIPA, "22", "⑤", "홍보 · 판매 권유 동의는 선택 항목이어야 함",
      note="documents.json 의 marketing 이 required=false 인지 확인"),
    R("M50-8", "practice", NETWORK, "50", "⑧", "광고성 정보 수신동의 정기 확인", when="marketing.channels",
      note="정보통신망법 시행령 제62조의3①: 수신동의 받은 날부터 2년마다 확인 — practice.marketing.reconfirm_every_2y"),
)

# 외부 처분 사례에서 위반된 조문 → 서비스가 이행 내역을 밝혀야 하는 practice 키
CASE_CHECKS: dict[str, tuple[str, str]] = {
    "21": ("destruction", "보유 기간 경과 · 탈퇴 시 파기 절차"),
    "26": ("processors", "수탁자 공개 · 교육 · 감독"),
    "28": ("handlers_training", "개인정보취급자 관리 · 감독 · 교육"),
    "29": ("safeguards", "안전조치(접근 통제 · 접속기록 보관 · 암호화 등) 이행 내역"),
    "31": ("officer", "개인정보 보호책임자 지정"),
    "34": ("breach_response", "유출 통지 · 신고 절차"),
    "35": ("rights_channel", "열람 요구 창구와 10일 이내 처리"),
    "36": ("rights_channel", "정정 · 삭제 요구 처리"),
    "37": ("rights_channel", "처리정지 요구 처리"),
}

_VAR = re.compile(r"\{\{\s*\w+\s*\}\}")


@dataclass
class Finding:
    rule: str
    doc: str
    severity: str
    law: str
    article: str
    clause: str
    title: str
    message: str
    note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _cite(r: Rule) -> str:
    short = {"개인정보 보호법": "법", "개인정보 보호법 시행령": "시행령", NETWORK: "정보통신망법"}.get(r.law, r.law)
    return f"{short} 제{r.article}조{r.clause}"


def _truthy(practice: dict, key: str | None) -> bool:
    if key is None:
        return True
    cur = practice
    for part in key.split("."):
        if not isinstance(cur, dict):
            return False
        cur = cur.get(part)
    return bool(cur)


def plain(html: str) -> str:
    """태그를 벗기고 공백을 정리한 본문 글자."""
    t = re.sub(r"<!--.*?-->", " ", html, flags=re.S)
    t = re.sub(r"<br\s*/?>|</p>|</tr>|</h\d>", "\n", t)
    t = _html.unescape(re.sub(r"<[^>]+>", " ", t))
    return re.sub(r"[ \t]+", " ", t)


def _finding(r: Rule, message: str) -> Finding:
    return Finding(r.id, r.doc, r.severity, r.law, r.article, r.clause, r.title, message, r.note)


def check_text(doc_key: str, html: str, practice: dict | None = None) -> list[Finding]:
    """렌더링된 문서 하나를 점검."""
    practice = practice or {}
    text = plain(html)
    out: list[Finding] = []
    left = sorted(set(_VAR.findall(html)))
    if left:
        out.append(Finding("VAR", doc_key, "error", "", "", "", "채워지지 않은 변수",
                           f"문구에 변수가 그대로 남음: {', '.join(left)}"))
    for r in RULES:
        if r.doc != doc_key or not _truthy(practice, r.when):
            continue
        missing = [p for p in r.need if not re.search(p, text)]
        if missing:
            out.append(_finding(r, f"{_cite(r)} '{r.title}' 기재가 없음 (찾지 못한 표현: {' / '.join(missing)})"))
        hit = [m.group(0) for p in r.forbid if (m := re.search(p, text))]
        if hit:
            out.append(_finding(r, f"{_cite(r)} '{r.title}': 문제 문구 \"{hit[0].strip()}\""))
    return out


def check_book(book, practice: dict | None = None) -> list[Finding]:
    """ConsentBook(서비스 값 · 프로필 반영)의 모든 문서 + 문서 구성 + practice 를 점검."""
    practice = practice if practice is not None else (book.profile.practice if book.profile else {})
    out: list[Finding] = []
    docs = {d.key: d for d in book.documents()}
    for key, d in docs.items():
        if d.file:
            out.extend(check_text(key, book.html(key), practice))
    rule = {r.id: r for r in RULES}
    if "marketing" in docs and docs["marketing"].required:
        out.append(_finding(rule["M22-5"], "marketing 동의가 필수로 되어 있음 — 거부해도 서비스 제공을 거부할 수 없다(법 제22조⑤)"))
    mk = practice.get("marketing") or {}
    if mk.get("channels") and not mk.get("reconfirm_every_2y"):
        out.append(_finding(rule["M50-8"], "광고성 정보를 보내면서 수신동의 정기 확인(reconfirm_every_2y)이 없음"))
    if book.profile and book.profile.texts_dir:
        out.append(Finding("CUSTOM", "*", "warn", "", "", "", "서비스 전용 문서",
                           f"공용 문구 대신 {book.profile.texts_dir} 를 씀 — 공용 개정이 자동 반영되지 않음"))
    return out


def case_gaps(practice: dict, violations: dict[str, int]) -> list[dict]:
    """외부 처분 사례(위반 조문 → 건수)에 비춰, 서비스가 이행 내역을 밝히지 않은 항목."""
    out = []
    for art, n in sorted(violations.items(), key=lambda kv: -kv[1]):
        chk = CASE_CHECKS.get(art)
        if chk and not practice.get(chk[0]):
            out.append({"article": art, "cases": n, "practice_key": chk[0], "what": chk[1],
                        "message": f"외부 처분 {n}건이 법 제{art}조 위반 — 이 서비스는 '{chk[1]}' 이행 내역({chk[0]})을 밝히지 않음"})
    return out


# ---- 규칙의 근거 조문 추적 ----

def article_hash(text: str) -> str:
    return hashlib.sha256(" ".join(text.split()).encode("utf-8")).hexdigest()[:16]


def basis_articles() -> dict[str, list[str]]:
    out: dict[str, set] = {}
    for r in RULES:
        out.setdefault(r.law, set()).add(r.article)
    return {k: sorted(v) for k, v in out.items()}


def load_basis(path: Path = BASIS_FILE) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8")) if Path(path).exists() else {}


def stale_rules(current: dict[str, dict[str, str]], basis: dict | None = None) -> list[dict]:
    """current = {법령명: {조문: 본문}}. 근거 조문이 규칙 작성 때와 달라진 규칙들."""
    basis = load_basis() if basis is None else basis
    out = []
    for r in RULES:
        arts = current.get(r.law)
        if arts is None:
            continue   # 이번에 못 가져온 법령은 판단 보류
        was = basis.get("laws", {}).get(r.law, {}).get(r.article)
        now = arts.get(r.article)
        if now is None:
            out.append({"rule": r.id, "law": r.law, "article": r.article, "reason": "조문이 없어짐"})
        elif was and was["hash"] != article_hash(now):
            out.append({"rule": r.id, "law": r.law, "article": r.article, "reason": "근거 조문 내용이 바뀜",
                        "basis_mst": basis["laws"][r.law].get("_mst")})
        elif not was:
            out.append({"rule": r.id, "law": r.law, "article": r.article, "reason": "근거 기록 없음(rebase 필요)"})
    return out


def rebase(fetcher, path: Path = BASIS_FILE) -> dict:
    """규칙을 현행 조문 기준으로 다시 확인했다는 기록. 사람이 규칙을 검토한 뒤에만 실행한다."""
    from datetime import datetime
    laws = {}
    for name, arts in basis_articles().items():
        v = fetcher.with_articles(fetcher.current(name))
        laws[name] = {"_mst": v.mst, "_effective": v.effective,
                      **{a: {"hash": article_hash(v.articles[a]), "title": v.titles.get(a, "")} for a in arts
                         if a in v.articles}}
    data = {"checked_at": datetime.now().isoformat(timespec="seconds"), "laws": laws}
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return data


def summarize(findings: list[Finding]) -> dict:
    c = Counter(f.severity for f in findings)
    return {"error": c.get("error", 0), "warn": c.get("warn", 0)}


def main(argv: list[str]) -> int:
    from . import ConsentBook
    from .profile import ServiceProfile
    if argv[:1] == ["rebase"]:
        from .keys import get_key
        from .law_fetcher import LawFetcher
        print(json.dumps(rebase(LawFetcher(get_key("LAW_OC"))), ensure_ascii=False, indent=1))
        return 0
    prof = ServiceProfile.load(argv[0]) if argv else None
    book = ConsentBook(profile=prof)
    fs = check_book(book)
    print(json.dumps({"service": prof.service if prof else "(공용 문구)", "summary": summarize(fs),
                      "findings": [f.to_dict() for f in fs]}, ensure_ascii=False, indent=1))
    return 1 if summarize(fs)["error"] else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
