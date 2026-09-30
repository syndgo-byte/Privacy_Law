"""서비스별 개인정보 처리 프로필.

서비스마다 개인정보를 다루는 방식이 다르다(위탁 업체 · 제3자 제공 · 국외 이전 · 쿠키 · 위치정보 …).
프로필은 두 가지를 담는다:
  practice  — 실제로 무엇을 어떻게 처리하는지(사실). 컴플라이언스 점검의 기준.
  values    — 문구 변수를 직접 지정할 때만(없으면 practice 에서 만든다).
  sections  — 문서 키 → 그 서비스에만 있는 조항 HTML(본문의 {{ service_sections }} 자리에 들어간다).
  texts_dir — 서비스 전용 문서 폴더(공용 문구를 통째로 대신). 공용 개정이 자동 반영되지 않으므로 점검에서 경고.

practice 키 (모두 선택, 해당 없으면 생략)
  service_desc: str                      서비스 설명(처리 목적 문구)
  collect_items: [str]                   필수 수집 항목
  processors: [{name, task}]             처리 위탁 (제26조)
  third_parties: [{name, purpose, items, retention}]   제3자 제공 (제17조)
  overseas: [{recipient, contact, country, items, when_how, purpose, retention, refuse}]  국외 이전 (제28조의8)
  officer: {name, dept, phone, email}    개인정보 보호책임자 (제31조, 제30조①6)
  cookies: {session: bool, analytics: bool, ads: bool}
  consent_free: [{items, basis}]         동의 없이 처리하는 항목과 법적 근거 (제22조③)
  sensitive / pseudonymous / automated_decision / location / foreign_operator / collects_from_abroad / children: bool
  safeguards: [str]                      안전성 확보 조치 이행 내역 (제29조)
  rights_channel: str                    열람 · 정정 · 삭제 요구 방법 (제35~37조)
  destruction: str                       파기 절차 (제21조)
  handlers_training: str                 개인정보취급자 교육 · 감독 (제28조)
  breach_response: str                   유출 통지 · 신고 절차 (제34조)
  marketing: {channels: [str], reconfirm_every_2y: bool, night_consent: bool}
"""
from __future__ import annotations

import html as _html
import json
from dataclasses import dataclass, field
from pathlib import Path


def _e(v) -> str:
    return _html.escape(str(v), quote=False)


def _lines(rows: list[str]) -> str:
    return "<br>\n".join(f"- {r}" for r in rows)


@dataclass
class ServiceProfile:
    service: str
    practice: dict = field(default_factory=dict)
    values: dict = field(default_factory=dict)
    sections: dict[str, str] = field(default_factory=dict)
    texts_dir: str | None = None

    @classmethod
    def from_dict(cls, d: dict) -> "ServiceProfile":
        unknown = set(d) - {"service", "practice", "values", "sections", "texts_dir"}
        if unknown:
            raise ValueError(f"프로필에 모르는 키: {sorted(unknown)}")
        if not d.get("service"):
            raise ValueError("프로필에 service 가 없습니다")
        return cls(service=d["service"], practice=dict(d.get("practice") or {}),
                   values=dict(d.get("values") or {}), sections=dict(d.get("sections") or {}),
                   texts_dir=d.get("texts_dir"))

    @classmethod
    def load(cls, path) -> "ServiceProfile":
        p = Path(path)
        prof = cls.from_dict(json.loads(p.read_text(encoding="utf-8")))
        if prof.texts_dir and not Path(prof.texts_dir).is_absolute():
            prof.texts_dir = str((p.parent / prof.texts_dir).resolve())
        return prof

    def to_dict(self) -> dict:
        return {"service": self.service, "practice": self.practice, "values": self.values,
                "sections": self.sections, "texts_dir": self.texts_dir}

    # ---- practice → 문구 변수 ----
    def rendered_values(self) -> dict:
        p = self.practice
        v: dict[str, str] = {"service_name": self.service}
        if p.get("service_desc"):
            v["service_desc"] = _e(p["service_desc"])
        if p.get("collect_items"):
            v["collect_items"] = _e(", ".join(p["collect_items"]))
        if p.get("processors"):
            v["processors"] = _lines([f"{_e(r['name'])}: {_e(r['task'])}" for r in p["processors"]])
        if p.get("third_parties"):
            v["third_parties"] = _lines([
                f"제공받는 자: {_e(r['name'])} / 목적: {_e(r['purpose'])} / 항목: {_e(r['items'])} / "
                f"보유 · 이용 기간: {_e(r['retention'])}" for r in p["third_parties"]])
        if p.get("overseas"):
            # 시행령 제31조①2: 국외 이전의 근거와 법 제28조의8② 각 호
            v["overseas_transfer"] = "서비스는 다음과 같이 개인정보를 국외로 이전합니다.<br>\n" + _lines([
                f"이전받는 자: {_e(r['recipient'])}({_e(r.get('contact', ''))}) / 이전되는 국가: {_e(r['country'])} / "
                f"이전 항목: {_e(r['items'])} / 이전 시기 · 방법: {_e(r['when_how'])} / "
                f"이용 목적 · 보유 기간: {_e(r['purpose'])}, {_e(r['retention'])} / 이전 거부 방법 · 효과: {_e(r['refuse'])}"
                for r in p["overseas"]])
        if p.get("officer"):
            o = p["officer"]
            who = " ".join(x for x in (_e(o.get("name", "")), f"({_e(o['dept'])})" if o.get("dept") else "") if x)
            contact = ", ".join(_e(x) for x in (o.get("phone"), o.get("email")) if x)
            v["privacy_officer"] = f"- 개인정보 보호책임자: {who}<br>\n- 연락처: {contact}"
        if "cookies" in p:
            c = p["cookies"] or {}
            uses = [n for k, n in (("session", "로그인 상태 유지(세션 쿠키)"), ("analytics", "이용 통계 분석"),
                                   ("ads", "맞춤형 광고 · 행태 분석")) if c.get(k)]
            not_uses = [n for k, n in (("analytics", "이용 통계 분석"), ("ads", "광고 · 행태 분석")) if not c.get(k)]
            text = f"서비스는 {', '.join(uses)} 목적으로 쿠키를 사용합니다." if uses else "서비스는 쿠키를 사용하지 않습니다."
            if uses and not_uses:
                text += f" {' · '.join(not_uses)} 목적의 쿠키는 사용하지 않습니다."
            v["cookies"] = text
        if p.get("consent_free"):
            v["legal_basis"] = _lines([f"{_e(r['items'])}: {_e(r['basis'])}" for r in p["consent_free"]])
        v.update(self.values)          # 서비스가 직접 준 문구가 최우선
        return v
