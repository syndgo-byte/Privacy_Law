"""인증키 보관 · 조회. 환경변수가 우선이고, 없으면 PRIVACY_LAW_HOME/.secrets.json (git 에 올리지 않음).

    python -m privacy_law.keys set LAW_OC <법제처 OC>      # 저장
    python -m privacy_law.keys show                        # 저장된 키 (가림 표시)
    python -m privacy_law.keys check                       # 법제처 API 를 실제로 불러 키가 통하는지 확인

LAW_OC: open.law.go.kr 회원가입 → [OPEN API] → [OPEN API 신청] 에서 받는 OC(가입 이메일의 @ 앞부분).
        신청할 때 이 PC(또는 서버)의 공인 IP · 도메인을 등록해야 한다 — 등록 안 된 곳에서 부르면
        법제처가 "사용자 정보 검증에 실패하였습니다" 를 돌려준다.
ANTHROPIC_API_KEY: 있으면 수정안 자동 초안(없으면 수동 모드).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from . import HOME

KEYS = {
    "LAW_OC": "법제처 Open API 인증키(OC) — https://open.law.go.kr 에서 신청, 호출할 PC/서버 IP 등록 필요",
    "ANTHROPIC_API_KEY": "Claude API 키 — 있으면 법령 개정 시 수정안을 자동으로 쓴다",
}


def secrets_path(home: Path = HOME) -> Path:
    return Path(home) / ".secrets.json"


def _load(home: Path) -> dict:
    p = secrets_path(home)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def get_key(name: str, home: Path = HOME) -> str:
    """환경변수 → .secrets.json 순. 없으면 빈 문자열."""
    return (os.environ.get(name) or _load(home).get(name) or "").strip()


def key_source(name: str, home: Path = HOME) -> str:
    if os.environ.get(name):
        return "env"
    return "file" if _load(home).get(name) else "none"


def set_key(name: str, value: str, home: Path = HOME) -> None:
    if name not in KEYS:
        raise KeyError(f"모르는 키: {name} (가능: {', '.join(KEYS)})")
    data = _load(home)
    if value:
        data[name] = value.strip()
    else:
        data.pop(name, None)
    p = secrets_path(home)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)


def mask(v: str) -> str:
    if not v:
        return "(없음)"
    return v[:2] + "*" * max(len(v) - 4, 1) + v[-2:] if len(v) > 4 else "*" * len(v)


def check_law_oc(home: Path = HOME, get=None) -> dict:
    """실제로 법제처를 불러 본다. {"ok", "source", "message", "law"?}"""
    from .law_fetcher import LawFetcher, LawFetchError
    oc = get_key("LAW_OC", home)
    src = key_source("LAW_OC", home)
    if not oc:
        return {"ok": False, "source": src, "message": "LAW_OC 가 없습니다. " + KEYS["LAW_OC"]}
    try:
        f = LawFetcher(oc, get=get) if get else LawFetcher(oc)
        v = f.current("개인정보 보호법")
        return {"ok": True, "source": src, "message": "법제처 API 연결 정상",
                "law": {"name": v.name, "mst": v.mst, "effective": v.effective}}
    except LawFetchError as e:
        return {"ok": False, "source": src, "message": str(e)}


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in ("set", "show", "check"):
        print(__doc__)
        return 2
    if argv[0] == "set":
        if len(argv) != 3:
            print("사용법: python -m privacy_law.keys set <이름> <값>   (값을 빈 문자열로 주면 삭제)")
            return 2
        set_key(argv[1], argv[2])
        print(f"{argv[1]} 저장: {mask(argv[2])} → {secrets_path()}")
        return 0
    if argv[0] == "show":
        for k, desc in KEYS.items():
            print(f"{k:18} {mask(get_key(k)):20} [{key_source(k)}]  {desc}")
        return 0
    r = check_law_oc()
    print(json.dumps(r, ensure_ascii=False, indent=2))
    return 0 if r["ok"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
