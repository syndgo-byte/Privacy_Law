# Privacy_Law MCP: 자동 법령 변경 감지 및 동의 문서 관리

**작성일:** 2026-09-28  
**상태:** 설계 검토 완료  
**담당자:** Claude Haiku 4.5 (설계), User (검증)

---

## 개요

`Privacy_Law`를 **Master Control Platform (MCP) 통합 시스템**으로 발전시킵니다. 법제처 Open API를 통해 법령 변경을 **자동으로 감지**하고, Claude AI의 제안을 받아 관리자가 **승인·적용**하는 완전 자동화 워크플로우입니다.

### 목표
- 개인정보보호법 등 법령 변경 시 동의 문서를 **즉시 업데이트**
- 모든 변경의 **감사 추적** (who, what, when, why)
- EMSv3, 향후 카드/장례 서비스 등 **다중 서비스 지원**
- 관리자의 **수동 검토 필요 최소화**

---

## 아키텍처

### 시스템 구성도

```
[법제처 Open API]
       ↓
[mcp_hub 백그라운드 작업]
  ├─ law_fetcher (일일 스케줄)
  ├─ change_detector
  ├─ suggestion_engine (Claude API 또는 수동)
  └─ approval_workflow
       ↓
[Privacy_Law 패키지]
  ├─ texts/*.html (동의 문서)
  ├─ documents.json (메타데이터 + 버전)
  ├─ .audit/ (감사 로그)
  ├─ snapshots/ (법령 스냅샷)
  └─ revisions/ (제안 브랜치)
       ↓
[GitHub 저장소]
  ├─ main (승인된 최신 버전)
  ├─ suggest/* (대기 중인 제안)
  └─ commit history (감사 증거)
       ↓
[auth_core → ConsentBook]
  └─ EMSv3 (사용자 동의 수집)
```

### 핵심 컴포넌트

**1. law_fetcher.py**
- 법제처 국가법령정보 Open API 폴링
- 관심 법령 목록: 개인정보보호법, 정보통신망법, 신용정보법
- `privacy_law/snapshots/{date}-{law_id}.txt` 저장
- 실패 시 다음 실행까지 대기 (로그 기록)

**2. change_detector.py**
- 이전 snapshot과 현재 버전 비교
- 조문 단위 diff 생성
- 영향 범위 판단: 어느 서비스, 어느 동의 문서 (terms/privacy/collect/marketing)
- `privacy_law/.audit/changes/{date}-{law_id}.json` 기록

**3. suggestion_engine.py**
- Claude API 호출: "이 조문 변경을 반영해서 collect.html을 수정해줄래?"
  - 입력: 원문 HTML, 변경된 조문, CONSENT_VALUES (service_name, service_desc, collect_items)
  - 출력: 제안된 수정 HTML
- 또는 수동 모드: Claude Code에서 직접 수정 요청
- `privacy_law/revisions/{date}-{doc_key}/suggested.html` 저장
- git 브랜치 자동 생성: `suggest/{date}-{doc_key}`

**4. approval_workflow.py**
- mcp_hub 대시보드 연동
- 관리자가 제안 검토 (변경 전/후 비교)
- 승인 시:
  - 제안 → main 브랜치 커밋
  - `documents.json` 버전 업데이트
  - `auth_consents` 테이블에 '재동의 필요' 플래그
  - `.audit/{date}-approval.json` 기록
- 거절 시:
  - 제안 브랜치 삭제
  - `.audit/{date}-rejection.json` 기록

---

## 데이터 흐름

### 1단계: 자동 감지 (매일/매시간)

```
mcp_hub 백그라운드 작업 트리거
  → law_fetcher.fetch(법령_ID_목록)
  → API 응답 → privacy_law/snapshots/{date}-{law_id}.txt
  → change_detector.detect(이전_snapshot, 현재_snapshot)
  → 변경 감지 시:
    ├─ diff 생성
    ├─ 영향받는 문서 식별 (예: collect.html)
    └─ mcp_hub notification: "변경 감지: 개인정보보호법 §15"
```

### 2단계: 수정안 생성

**자동 모드** (API 키 설정 시):
```
mcp_hub 자동 트리거
  → suggestion_engine.suggest_with_claude(
      original_html="<table>...",
      changed_provision="§15 개정...",
      service_context=CONSENT_VALUES
    )
  → Claude API: GPT 수준의 수정안 생성
  → privacy_law/revisions/{date}-{doc_key}/suggested.html
  → git branch suggest/{date}-{doc_key} 생성 + push
  → mcp_hub: "검토 대기" 신호
```

**수동 모드** (기본값):
```
mcp_hub: "변경 감지" 알림 → 관리자 확인
  → Claude Code에서 "Privacy_Law 제안 만들어줘" 요청
  → 수정안 작성 + privacy_law/revisions/{...}에 저장
  → git branch 생성 (또는 직접 커밋)
  → mcp_hub: "검토 대기"
```

### 3단계: 관리자 승인

```
mcp_hub 대시보드: "검토 대기 1개"
  → 제안 클릭: 변경 전/후 HTML 비교 보기
  → "승인" 또는 "거절" 선택

승인 시:
  → approval_workflow.approve({revision_id}, {admin_user})
  → git: suggest/* 브랜치 → main으로 merge
  → documents.json: 버전 증가 (예: 1.2 → 1.3)
  → auth_consents: 기존 사용자에게 'outdated' 플래그
  → .audit/{date}-approval.json 기록
  → EMSv3에서 ConsentBook.outdated() 감지 → 사용자에게 "재동의 필요" 안내

거절 시:
  → approval_workflow.reject({revision_id}, {reason})
  → git: 제안 브랜치 삭제
  → .audit/{date}-rejection.json 기록
```

---

## 에러 처리

| 시나리오 | 처리 방식 |
|---------|---------|
| 법제처 API 다운 | 로그 기록 → 다음 실행 시점 대기 → mcp_hub 알림 |
| 변경 감지 실패 | snapshot 재다운로드 (atomic write로 손상 방지) |
| Claude 제안 생성 실패 (API error) | 재시도 큐 저장 → 1시간 뒤 재시도 |
| Claude 제안 공란/이상 | "제안 실패" 로그 → 수동 모드 전환 |
| API 비용 초과 | 관리자 알림 → 자동 API 호출 중단 (수동만 허용) |
| 같은 문서에 제안 2개 이상 | 순차 처리 (먼저 들어온 것부터) |
| 승인 중 새 변경 감지 | 다음 사이클로 미루기 |
| git 커밋/푸시 실패 | 재시도 + 로그 → 관리자 검토 필요 |

---

## 데이터 구조

### documents.json (기존 + 버전 메타)

```json
{
  "documents": [
    {
      "key": "collect",
      "title": "개인정보 수집·이용 동의",
      "required": true,
      "version": "1.3",
      "file": "collect.html",
      "last_updated": "2026-09-28",
      "updated_by": "system",
      "related_laws": ["개인정보보호법 §15"]
    }
  ]
}
```

### .audit/changes/{date}-{law_id}.json

```json
{
  "law_id": "개인정보보호법",
  "detected_at": "2026-09-28T10:30:00Z",
  "changes": [
    {
      "article": "§15",
      "before": "개인정보 처리 방침은 ...",
      "after": "개인정보 처리 방침은 ... (개정)",
      "affected_documents": ["collect", "privacy"]
    }
  ]
}
```

### .audit/{date}-approval.json

```json
{
  "revision_id": "suggest/2026-09-28-collect",
  "document_key": "collect",
  "approved_at": "2026-09-28T14:20:00Z",
  "approved_by": "admin@example.com",
  "new_version": "1.3",
  "change_summary": "§15 개정 반영: 개인정보 처리 방침 명시화",
  "users_flagged_for_reconsent": 15420
}
```

---

## 통합 포인트

### auth_core ↔ ConsentBook

```python
# auth_core/__init__.py
def attach_consents(connect, values):
    return ConsentBook(
        connect=connect,
        values=values,  # service_name, service_desc, collect_items
        texts_dir="privacy_law/texts",
        enabled=not self.is_closed  # 폐쇄망 제외
    )

# ConsentBook.outdated(username) → [doc_keys_needing_reconsent]
# EMSv3에서 호출 → "이 문서들은 재동의가 필요합니다" 안내
```

### EMSv3 ↔ 재동의 워크플로우

```python
# EMSv3/app.py
CONSENTS = identity.AUTH.attach_consents(get_db, values=CONSENT_VALUES)

# 사용자 로그인 또는 계약서 접근 시
outdated = CONSENTS.outdated(username)
if outdated:
    return render('reconsent.html', documents=CONSENTS.form_documents())

# 재동의 수집
CONSENTS.record(username, agreements, client_ip, conn=conn)
# → auth_consents 업데이트 + 'outdated' 플래그 제거
```

---

## 테스팅 전략

### 단위 테스트
- `law_fetcher`: API 모킹 → snapshot 생성 확인
- `change_detector`: 알려진 변경 케이스 → 정확한 diff 검출
- `suggestion_engine`: Claude 응답 파싱 + HTML validation
- `approval_workflow`: git 커밋/브랜치 + 로그 기록

### 통합 테스트
- E2E: 가짜 법령 변경 → 제안 생성 → 승인 → 최종 커밋
- 실제 법제처 API (느슨한 연결): 격주 회귀 테스트

### 수동 테스트 (배포 초기)
- 관리자가 mcp_hub 대시보드에서 제안 승인/거절 동작 검증
- Claude API 없이 (수동 모드): Claude Code에서 제안 작성 후 적용 확인

### 모니터링
- mcp_hub 대시보드: 최근 30일 변경 감지/제안/승인 통계
- `.audit/` 로그: 모든 작업 기록 (감사 증거)
- 에러 알림: mcp_hub notification 또는 관리자 이메일

---

## 구현 순서

1. **privacy_law 핵심 모듈** (law_fetcher, change_detector, suggestion_engine, approval_workflow)
2. **mcp_hub 통합** (백그라운드 작업 스케줄링, 대시보드 신호)
3. **auth_core 업데이트** (outdated() 메서드, 재동의 workflow)
4. **EMSv3 업데이트** (재동의 UI, 승인 처리)
5. **통합 테스트 & 배포**

---

## 성공 기준

✅ 법령 변경 자동 감지 (24시간 내)  
✅ 모든 변경 사항이 감사 로그에 기록됨  
✅ Claude 제안 또는 수동 수정 모두 지원  
✅ 관리자 승인 후 EMSv3에서 즉시 반영  
✅ 사용자에게 "재동의 필요" 안내 및 수집  
✅ GitHub 커밋 이력으로 법규 변경 추적 가능
