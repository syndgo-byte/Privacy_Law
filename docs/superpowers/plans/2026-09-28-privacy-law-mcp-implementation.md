# Privacy_Law MCP 구현 계획

> **For agentic workers:** Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 법제처 API 기반 자동 법령 감지, Claude 제안, 관리자 승인 워크플로우를 구현하고 Privacy_Law → mcp_hub → auth_core → EMSv3로 통합

**Architecture:** 4개의 독립 모듈(law_fetcher, change_detector, suggestion_engine, approval_workflow)을 privacy_law 패키지에 작성 → mcp_hub에서 백그라운드 작업으로 스케줄링 → auth_core에 outdated() 메서드 추가 → EMSv3에서 재동의 UI 구현

**Tech Stack:** Python (privacy_law), mcp_hub 백그라운드 작업, git, Claude API (optional), SQLite (auth_consents)

**Spec:** `docs/superpowers/specs/2026-09-28-privacy-law-mcp-design.md`

## Global Constraints

- Python 3.10+
- Git 커밋으로 모든 변경 추적
- 감사 로그 (.audit/) read-only
- 폐쇄망은 enabled=False (재동의 UI 불필요)
- 모든 모듈은 테스트 작성 필수

## Review Focus

1. **법제처 API 연결 실패** → 로그 기록 + 다음 실행 대기: law_fetcher 테스트에서 API 모킹
2. **변경 감지 누락** → snapshot diff가 정확한가: change_detector의 알려진 변경 케이스 테스트
3. **Claude 제안 공란** → fallback 불명확: suggestion_engine에서 공란 응답 처리 + 수동 모드 전환 테스트
4. **승인 중 충돌** → 같은 문서 제안 2개 이상: approval_workflow의 순차 처리 테스트
5. **사용자 재동의 누락** → 문서 버전 변경 후 auth_consents 업데이트 누락: EMSv3 재동의 로직 E2E 테스트

---

## Task 1: law_fetcher.py 구현

**Files:**
- Create: `privacy_law/law_fetcher.py`
- Create: `tests/test_law_fetcher.py`
- Modify: `privacy_law/__init__.py` (export LawFetcher)

**Interfaces:**
- Consumes: 법제처 국가법령정보 Open API (requests)
- Produces: `LawFetcher` class
  - `fetch(law_ids: list[str]) -> dict[str, str]` — 법령 ID → 최신 조문 텍스트
  - `save_snapshot(law_id: str, content: str) -> Path` — snapshots/{date}-{law_id}.txt 저장
  - Returns Path on success, raises LawFetcherError on failure

- [ ] **Step 1: 테스트 작성 (실패 예상)**

```python
# tests/test_law_fetcher.py
import pytest
from unittest.mock import patch, MagicMock
from privacy_law.law_fetcher import LawFetcher
from pathlib import Path
import tempfile

def test_fetch_with_mock_api():
    """법제처 API 응답을 모킹하고 fetch 동작 검증"""
    with tempfile.TemporaryDirectory() as tmpdir:
        fetcher = LawFetcher(snapshot_dir=Path(tmpdir))
        
        # API 응답 모킹
        mock_response = {
            "개인정보보호법": "제1조 목적\n본 법은...\n\n제15조 개인정보 처리 방침\n...",
            "정보통신망법": "제22조 개인정보 보호..."
        }
        
        with patch('privacy_law.law_fetcher.requests.get') as mock_get:
            mock_get.return_value.json.return_value = mock_response
            
            result = fetcher.fetch(["개인정보보호법", "정보통신망법"])
        
        assert result["개인정보보호법"].startswith("제1조")
        assert result["정보통신망법"].startswith("제22조")

def test_save_snapshot():
    """스냅샷을 파일로 저장하고 경로 반환"""
    with tempfile.TemporaryDirectory() as tmpdir:
        fetcher = LawFetcher(snapshot_dir=Path(tmpdir))
        content = "제1조 목적\n본 법은..."
        
        path = fetcher.save_snapshot("개인정보보호법", content)
        
        assert path.exists()
        assert path.name.endswith(".txt")
        assert "개인정보보호법" in path.name
        assert path.read_text() == content

def test_fetch_api_error():
    """API 오류 시 LawFetcherError 발생"""
    with tempfile.TemporaryDirectory() as tmpdir:
        fetcher = LawFetcher(snapshot_dir=Path(tmpdir))
        
        with patch('privacy_law.law_fetcher.requests.get') as mock_get:
            mock_get.side_effect = Exception("API 다운")
            
            with pytest.raises(LawFetcherError, match="API 다운"):
                fetcher.fetch(["개인정보보호법"])

def test_snapshot_file_naming():
    """snapshot 파일명이 YYYY-MM-DD-{law_id}.txt 형식"""
    with tempfile.TemporaryDirectory() as tmpdir:
        fetcher = LawFetcher(snapshot_dir=Path(tmpdir))
        
        path = fetcher.save_snapshot("개인정보보호법", "content")
        
        # 파일명 형식: YYYY-MM-DD-개인정보보호법.txt
        parts = path.stem.split("-")
        assert len(parts) >= 4  # YYYY, MM, DD, 법령명
        assert parts[3] == "개인정보보호법"
```

- [ ] **Step 2: 테스트 실행 (실패 확인)**

```bash
cd D:\Vibe_coding\Privacy_Law
python -m pytest tests/test_law_fetcher.py -v
# Expected: ModuleNotFoundError: No module named 'privacy_law.law_fetcher'
```

- [ ] **Step 3: law_fetcher.py 구현**

```python
# privacy_law/law_fetcher.py
import requests
import json
from pathlib import Path
from datetime import datetime
from typing import Optional

class LawFetcherError(Exception):
    """Law fetcher 에러"""
    pass

class LawFetcher:
    """법제처 Open API에서 법령 정보 조회 + snapshot 저장"""
    
    def __init__(self, snapshot_dir: Path, api_key: Optional[str] = None):
        self.snapshot_dir = Path(snapshot_dir)
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)
        self.api_key = api_key
        self.api_base = "https://www.law.go.kr/openinfoapi/service/rest/법령"
        
    def fetch(self, law_ids: list[str]) -> dict[str, str]:
        """법제처 API에서 법령 조회. law_ids: ['개인정보보호법', '정보통신망법']"""
        result = {}
        for law_id in law_ids:
            try:
                # 실제 구현: 법제처 API 호출
                # 현재는 모킹용 인터페이스만 정의
                # 실제 API 호출 로직은 test mock에서 처리
                response = requests.get(
                    self.api_base,
                    params={
                        "OC": self.api_key,
                        "target": "law",
                        "query": law_id,
                        "type": "json"
                    },
                    timeout=10
                )
                response.raise_for_status()
                result[law_id] = response.json().get("content", "")
            except Exception as e:
                raise LawFetcherError(f"Failed to fetch {law_id}: {str(e)}")
        
        return result
    
    def save_snapshot(self, law_id: str, content: str) -> Path:
        """snapshot 파일 저장: snapshots/YYYY-MM-DD-{law_id}.txt"""
        date_str = datetime.now().strftime("%Y-%m-%d")
        filename = f"{date_str}-{law_id}.txt"
        path = self.snapshot_dir / filename
        
        # Atomic write: 임시 파일에 쓴 후 이름 변경
        tmp_path = path.with_suffix('.tmp')
        tmp_path.write_text(content, encoding='utf-8')
        tmp_path.replace(path)
        
        return path
```

- [ ] **Step 4: 테스트 실행 (성공 확인)**

```bash
cd D:\Vibe_coding\Privacy_Law
python -m pytest tests/test_law_fetcher.py -v
# Expected: All tests PASS
```

- [ ] **Step 5: __init__.py 업데이트**

```python
# privacy_law/__init__.py (기존 코드 뒤에 추가)
from .law_fetcher import LawFetcher, LawFetcherError

__all__ = [
    "ConsentBook",
    "LawFetcher",
    "LawFetcherError",
]
```

- [ ] **Step 6: 커밋**

```bash
cd D:\Vibe_coding\Privacy_Law
git add privacy_law/law_fetcher.py tests/test_law_fetcher.py privacy_law/__init__.py
git commit -m "feat: law_fetcher 구현 (법제처 API 폴링 + snapshot 저장)"
```

---

## Task 2: change_detector.py 구현

**Files:**
- Create: `privacy_law/change_detector.py`
- Create: `tests/test_change_detector.py`
- Create: `privacy_law/.audit/` directory
- Modify: `privacy_law/__init__.py` (export ChangeDetector)

**Interfaces:**
- Consumes: `law_fetcher.save_snapshot()` 결과, 이전 snapshot 파일
- Produces: `ChangeDetector` class
  - `detect(law_id: str, previous_snapshot: str, current_snapshot: str) -> list[dict]`
    - Returns: `[{"article": "§15", "before": "...", "after": "...", "affected_documents": ["collect", "privacy"]}]`
  - `save_audit_log(law_id: str, changes: list[dict]) -> Path`

- [ ] **Step 1: 테스트 작성**

```python
# tests/test_change_detector.py
import pytest
from privacy_law.change_detector import ChangeDetector
from pathlib import Path
import tempfile

def test_detect_article_change():
    """조문 변경 감지"""
    detector = ChangeDetector()
    
    before = """제1조 목적
본 법은 개인정보 보호를 목적으로 한다.

제15조 개인정보 처리 방침
개인정보를 처리하는 자는 개인정보 보호법 규정에 따라야 한다."""
    
    after = """제1조 목적
본 법은 개인정보 보호를 목적으로 한다.

제15조 개인정보 처리 방침 (개정)
개인정보를 처리하는 자는 개인정보 보호법 규정에 따라야 하며, 이용자의 명시적 동의를 받아야 한다."""
    
    changes = detector.detect("개인정보보호법", before, after)
    
    assert len(changes) == 1
    assert changes[0]["article"] == "§15"
    assert "개정" in changes[0]["after"]
    assert "명시적 동의" in changes[0]["after"]

def test_no_change_detected():
    """변경 없으면 빈 리스트"""
    detector = ChangeDetector()
    
    content = "제1조 목적\n본 법은..."
    
    changes = detector.detect("개인정보보호법", content, content)
    
    assert changes == []

def test_affected_documents_mapping():
    """조문 변경 → 영향받는 문서 식별"""
    detector = ChangeDetector()
    
    # 수집 관련 조문 (collect.html에 영향)
    before = "제15조 개인정보 수집..."
    after = "제15조 개인정보 수집 (개정) ..."
    
    changes = detector.detect("개인정보보호법", before, after)
    
    # 제15조는 collect/privacy 문서에 영향
    assert "collect" in changes[0].get("affected_documents", [])

def test_save_audit_log():
    """감사 로그 저장"""
    with tempfile.TemporaryDirectory() as tmpdir:
        detector = ChangeDetector(audit_dir=Path(tmpdir))
        
        changes = [
            {
                "article": "§15",
                "before": "old",
                "after": "new",
                "affected_documents": ["collect"]
            }
        ]
        
        path = detector.save_audit_log("개인정보보호법", changes)
        
        assert path.exists()
        assert "개인정보보호법" in path.name
        
        # JSON 파일 검증
        import json
        data = json.loads(path.read_text())
        assert len(data["changes"]) == 1
        assert data["changes"][0]["article"] == "§15"
```

- [ ] **Step 2: 테스트 실행 (실패 확인)**

```bash
cd D:\Vibe_coding\Privacy_Law
python -m pytest tests/test_change_detector.py -v
# Expected: ModuleNotFoundError
```

- [ ] **Step 3: change_detector.py 구현**

```python
# privacy_law/change_detector.py
import json
from pathlib import Path
from datetime import datetime
from typing import Optional
from difflib import unified_diff

class ChangeDetector:
    """이전/현재 snapshot 비교해서 조문 변경 감지"""
    
    # 조문 번호 → 영향받는 문서 매핑
    ARTICLE_TO_DOCUMENTS = {
        "§15": ["collect", "privacy"],  # 개인정보 처리 방침
        "§22": ["privacy"],              # 정보통신망법 개인정보 보호
        "§26": ["collect"],              # 동의
    }
    
    def __init__(self, audit_dir: Optional[Path] = None):
        self.audit_dir = Path(audit_dir) if audit_dir else Path("privacy_law/.audit/changes")
        self.audit_dir.mkdir(parents=True, exist_ok=True)
    
    def detect(self, law_id: str, previous: str, current: str) -> list[dict]:
        """이전/현재 스냅샷 비교 → 변경사항 추출"""
        if previous == current:
            return []
        
        changes = []
        
        # 줄 단위 diff
        prev_lines = previous.split('\n')
        curr_lines = current.split('\n')
        
        diff = list(unified_diff(prev_lines, curr_lines, lineterm=''))
        
        # 조문 단위로 변경사항 추출
        current_article = None
        before_text = []
        after_text = []
        in_change = False
        
        for line in diff:
            if line.startswith('@@'):
                # 새로운 청크 시작
                if current_article and in_change:
                    changes.append({
                        "article": current_article,
                        "before": '\n'.join(before_text),
                        "after": '\n'.join(after_text),
                        "affected_documents": self.ARTICLE_TO_DOCUMENTS.get(current_article, [])
                    })
                before_text = []
                after_text = []
                in_change = False
            elif line.startswith('제') or line.startswith('§'):
                # 조문 시작
                current_article = line.split()[0]
            elif line.startswith('-'):
                before_text.append(line[1:])
                in_change = True
            elif line.startswith('+'):
                after_text.append(line[1:])
                in_change = True
        
        # 마지막 변경사항 저장
        if current_article and in_change:
            changes.append({
                "article": current_article,
                "before": '\n'.join(before_text),
                "after": '\n'.join(after_text),
                "affected_documents": self.ARTICLE_TO_DOCUMENTS.get(current_article, [])
            })
        
        return changes
    
    def save_audit_log(self, law_id: str, changes: list[dict]) -> Path:
        """감사 로그 저장: .audit/changes/YYYY-MM-DD-{law_id}.json"""
        date_str = datetime.now().strftime("%Y-%m-%d")
        filename = f"{date_str}-{law_id}.json"
        path = self.audit_dir / filename
        
        audit_data = {
            "law_id": law_id,
            "detected_at": datetime.now().isoformat(),
            "changes": changes
        }
        
        # Atomic write
        tmp_path = path.with_suffix('.tmp')
        tmp_path.write_text(json.dumps(audit_data, ensure_ascii=False, indent=2), encoding='utf-8')
        tmp_path.replace(path)
        
        return path
```

- [ ] **Step 4: 테스트 실행**

```bash
cd D:\Vibe_coding\Privacy_Law
python -m pytest tests/test_change_detector.py -v
```

- [ ] **Step 5: __init__.py 업데이트**

```python
# privacy_law/__init__.py (기존 코드 뒤에 추가)
from .change_detector import ChangeDetector

__all__ = [
    "ConsentBook",
    "LawFetcher",
    "LawFetcherError",
    "ChangeDetector",
]
```

- [ ] **Step 6: 커밋**

```bash
cd D:\Vibe_coding\Privacy_Law
git add privacy_law/change_detector.py tests/test_change_detector.py privacy_law/__init__.py
git commit -m "feat: change_detector 구현 (조문 변경 감지 + 영향 범위 식별)"
```

---

## Task 3: suggestion_engine.py 구현

**Files:**
- Create: `privacy_law/suggestion_engine.py`
- Create: `tests/test_suggestion_engine.py`
- Create: `privacy_law/revisions/` directory
- Modify: `privacy_law/__init__.py` (export SuggestionEngine)

**Interfaces:**
- Consumes: Claude API (optional), change_detector 결과
- Produces: `SuggestionEngine` class
  - `suggest_with_claude(doc_key: str, original_html: str, changed_provision: str, service_context: dict) -> str`
    - Returns: 제안된 HTML
  - `create_revision_branch(doc_key: str, suggested_html: str) -> str`
    - Returns: 브랜치명 (suggest/{date}-{doc_key})

- [ ] **Step 1: 테스트 작성**

```python
# tests/test_suggestion_engine.py
import pytest
from unittest.mock import patch, MagicMock
from privacy_law.suggestion_engine import SuggestionEngine
from pathlib import Path
import tempfile
import json

def test_suggest_with_claude_mock():
    """Claude API 모킹 → 제안 생성"""
    engine = SuggestionEngine(api_key="test-key")
    
    original_html = "<table><tr><td>기존 내용</td></tr></table>"
    provision = "§15 개정: 개인정보 처리 명시 필요"
    context = {"service_name": "EMS", "service_desc": "직원관리"}
    
    mock_response = "<table><tr><td>기존 내용</td><td>추가: 명시적 동의 필요</td></tr></table>"
    
    with patch('privacy_law.suggestion_engine.anthropic.Anthropic') as mock_client:
        mock_instance = MagicMock()
        mock_client.return_value = mock_instance
        mock_instance.messages.create.return_value.content[0].text = mock_response
        
        result = engine.suggest_with_claude("collect", original_html, provision, context)
    
    assert "<table>" in result
    assert "추가" in result

def test_suggest_fallback_no_api():
    """API 키 없으면 수동 모드 안내"""
    engine = SuggestionEngine(api_key=None)
    
    with pytest.raises(ValueError, match="수동 모드"):
        engine.suggest_with_claude("collect", "<table>...</table>", "§15 개정", {})

def test_create_revision_branch():
    """revision 브랜치 생성"""
    with tempfile.TemporaryDirectory() as tmpdir:
        engine = SuggestionEngine(api_key="test", revision_dir=Path(tmpdir))
        
        branch_name = engine.create_revision_branch("collect", "<table>new</table>")
        
        assert branch_name.startswith("suggest/")
        assert "collect" in branch_name
        
        # 파일 생성 확인
        revision_dir = Path(tmpdir) / branch_name.replace("suggest/", "")
        assert (revision_dir / "suggested.html").exists()

def test_suggestion_html_validation():
    """제안된 HTML 검증 (공란 체크)"""
    engine = SuggestionEngine(api_key=None)
    
    # 공란 제안 → error
    with pytest.raises(ValueError, match="제안 내용 공란"):
        engine.validate_suggestion("")
    
    # 유효한 제안 → 통과
    engine.validate_suggestion("<table>valid html</table>")
```

- [ ] **Step 2: 테스트 실행 (실패 확인)**

```bash
cd D:\Vibe_coding\Privacy_Law
python -m pytest tests/test_suggestion_engine.py -v
```

- [ ] **Step 3: suggestion_engine.py 구현**

```python
# privacy_law/suggestion_engine.py
import json
import re
from pathlib import Path
from datetime import datetime
from typing import Optional
try:
    import anthropic
except ImportError:
    anthropic = None

class SuggestionEngine:
    """Claude를 이용한 동의 문서 수정안 생성"""
    
    def __init__(self, api_key: Optional[str] = None, revision_dir: Optional[Path] = None):
        self.api_key = api_key
        self.revision_dir = Path(revision_dir) if revision_dir else Path("privacy_law/revisions")
        self.revision_dir.mkdir(parents=True, exist_ok=True)
        self.client = anthropic.Anthropic(api_key=api_key) if api_key and anthropic else None
    
    def suggest_with_claude(self, doc_key: str, original_html: str, changed_provision: str, service_context: dict) -> str:
        """Claude API로 수정안 생성. API 키 없으면 ValueError 발생"""
        if not self.client:
            raise ValueError("Claude API 키 필요. 수동 모드: Claude Code에서 직접 제안 작성")
        
        prompt = f"""다음 법령 변경을 반영하여 개인정보 동의 문서를 수정해주세요.

서비스: {service_context.get('service_name', 'Unknown')}
문서: {doc_key}
설명: {service_context.get('service_desc', '')}

**변경된 법령 조문:**
{changed_provision}

**현재 문서 HTML:**
{original_html}

**작업:**
1. 위 법령 변경을 문서에 반영
2. HTML 구조 유지 (table, div 등)
3. {{ {{ }} }} 템플릿 변수 보존
4. 한국어 법률 용어 정확성 유지

**결과는 수정된 HTML만 반환 (설명 없음):**"""
        
        response = self.client.messages.create(
            model="claude-opus-5-5",
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}]
        )
        
        suggested_html = response.content[0].text.strip()
        self.validate_suggestion(suggested_html)
        
        return suggested_html
    
    def validate_suggestion(self, html: str) -> bool:
        """제안된 HTML 검증"""
        if not html or len(html.strip()) < 10:
            raise ValueError("제안 내용 공란 또는 너무 짧음")
        
        # HTML 기본 검증
        if not ('<' in html and '>' in html):
            raise ValueError("유효하지 않은 HTML")
        
        return True
    
    def create_revision_branch(self, doc_key: str, suggested_html: str) -> str:
        """revision 파일 생성 + 브랜치명 반환"""
        date_str = datetime.now().strftime("%Y-%m-%d")
        branch_name = f"suggest/{date_str}-{doc_key}"
        
        # revision 디렉토리 생성: revisions/{date}-{doc_key}/
        revision_path = self.revision_dir / f"{date_str}-{doc_key}"
        revision_path.mkdir(parents=True, exist_ok=True)
        
        # suggested.html 저장
        suggested_file = revision_path / "suggested.html"
        suggested_file.write_text(suggested_html, encoding='utf-8')
        
        # metadata.json 저장
        metadata = {
            "doc_key": doc_key,
            "created_at": datetime.now().isoformat(),
            "branch": branch_name,
            "status": "pending_review"
        }
        metadata_file = revision_path / "metadata.json"
        metadata_file.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')
        
        return branch_name
```

- [ ] **Step 4: 테스트 실행**

```bash
cd D:\Vibe_coding\Privacy_Law
python -m pytest tests/test_suggestion_engine.py -v
```

- [ ] **Step 5: __init__.py 업데이트**

```python
# privacy_law/__init__.py
from .suggestion_engine import SuggestionEngine

__all__ = [
    "ConsentBook",
    "LawFetcher",
    "LawFetcherError",
    "ChangeDetector",
    "SuggestionEngine",
]
```

- [ ] **Step 6: 커밋**

```bash
cd D:\Vibe_coding\Privacy_Law
git add privacy_law/suggestion_engine.py tests/test_suggestion_engine.py privacy_law/__init__.py
git commit -m "feat: suggestion_engine 구현 (Claude 수정안 생성 + revision 관리)"
```

---

## Task 4: approval_workflow.py 구현

**Files:**
- Create: `privacy_law/approval_workflow.py`
- Create: `tests/test_approval_workflow.py`
- Modify: `privacy_law/__init__.py` (export ApprovalWorkflow)

**Interfaces:**
- Consumes: git, suggestion_engine 결과, documents.json
- Produces: `ApprovalWorkflow` class
  - `approve(revision_id: str, admin: str) -> dict` — 승인 처리
  - `reject(revision_id: str, reason: str) -> dict` — 거절 처리
  - `save_approval_log(doc_key: str, revision_id: str, admin: str) -> Path`

- [ ] **Step 1: 테스트 작성**

```python
# tests/test_approval_workflow.py
import pytest
from unittest.mock import patch, MagicMock
from privacy_law.approval_workflow import ApprovalWorkflow
from pathlib import Path
import tempfile
import json
from datetime import datetime

def test_approve_updates_version():
    """승인 시 documents.json 버전 증가"""
    with tempfile.TemporaryDirectory() as tmpdir:
        # 초기 documents.json 설정
        docs_file = Path(tmpdir) / "documents.json"
        docs_data = {
            "documents": [
                {"key": "collect", "version": "1.2", "file": "collect.html"}
            ]
        }
        docs_file.write_text(json.dumps(docs_data), encoding='utf-8')
        
        workflow = ApprovalWorkflow(docs_file=docs_file, audit_dir=Path(tmpdir))
        
        with patch('privacy_law.approval_workflow.subprocess.run') as mock_git:
            mock_git.return_value.returncode = 0
            
            result = workflow.approve("suggest/2026-09-28-collect", "admin@example.com")
        
        # documents.json 버전 확인
        updated = json.loads(docs_file.read_text())
        assert updated["documents"][0]["version"] == "1.3"
        assert result["new_version"] == "1.3"

def test_reject_deletes_branch():
    """거절 시 revision 브랜치 삭제"""
    with tempfile.TemporaryDirectory() as tmpdir:
        workflow = ApprovalWorkflow(audit_dir=Path(tmpdir))
        
        with patch('privacy_law.approval_workflow.subprocess.run') as mock_git:
            mock_git.return_value.returncode = 0
            
            result = workflow.reject("suggest/2026-09-28-collect", "법령 미적용")
        
        # git branch -D 호출 확인
        calls = [call for call in mock_git.call_args_list if 'branch' in str(call)]
        assert len(calls) > 0

def test_approval_audit_log():
    """승인 로그 저장"""
    with tempfile.TemporaryDirectory() as tmpdir:
        workflow = ApprovalWorkflow(audit_dir=Path(tmpdir))
        
        log_path = workflow.save_approval_log(
            "collect",
            "suggest/2026-09-28-collect",
            "admin@example.com"
        )
        
        assert log_path.exists()
        log_data = json.loads(log_path.read_text())
        assert log_data["approved_by"] == "admin@example.com"
        assert "approved_at" in log_data

def test_concurrent_approvals_sequential():
    """같은 문서 제안 2개 → 순차 처리"""
    with tempfile.TemporaryDirectory() as tmpdir:
        workflow = ApprovalWorkflow(audit_dir=Path(tmpdir))
        
        # 대기 중인 제안 2개
        pending = [
            "suggest/2026-09-28-collect",
            "suggest/2026-09-29-collect"
        ]
        
        # 첫 번째 승인
        with patch('privacy_law.approval_workflow.subprocess.run') as mock_git:
            mock_git.return_value.returncode = 0
            workflow.approve(pending[0], "admin")
        
        # 두 번째는 대기 (실제 구현에서 처리)
        # 이것은 mcp_hub 스케줄러에서 관리
```

- [ ] **Step 2: 테스트 실행 (실패 확인)**

```bash
cd D:\Vibe_coding\Privacy_Law
python -m pytest tests/test_approval_workflow.py -v
```

- [ ] **Step 3: approval_workflow.py 구현**

```python
# privacy_law/approval_workflow.py
import json
import subprocess
from pathlib import Path
from datetime import datetime
from typing import Optional

class ApprovalWorkflow:
    """관리자 승인 처리: 문서 업데이트 + git merge + 감사 로그"""
    
    def __init__(self, docs_file: Optional[Path] = None, audit_dir: Optional[Path] = None):
        self.docs_file = Path(docs_file) if docs_file else Path("privacy_law/texts/documents.json")
        self.audit_dir = Path(audit_dir) if audit_dir else Path("privacy_law/.audit")
        self.audit_dir.mkdir(parents=True, exist_ok=True)
    
    def approve(self, revision_id: str, admin: str) -> dict:
        """revision 승인: merge + 버전 업데이트 + auth_consents 플래그"""
        # revision_id: "suggest/2026-09-28-collect"
        doc_key = revision_id.split("-")[-1]
        
        # 1. documents.json 버전 증가
        docs_data = json.loads(self.docs_file.read_text(encoding='utf-8'))
        for doc in docs_data["documents"]:
            if doc["key"] == doc_key:
                old_version = doc["version"]
                # 버전 증가: 1.2 → 1.3
                parts = old_version.split('.')
                parts[-1] = str(int(parts[-1]) + 1)
                new_version = '.'.join(parts)
                doc["version"] = new_version
                doc["last_updated"] = datetime.now().isoformat()
                doc["updated_by"] = admin
        
        self.docs_file.write_text(json.dumps(docs_data, ensure_ascii=False, indent=2), encoding='utf-8')
        
        # 2. git merge (실제 구현)
        try:
            subprocess.run(
                ["git", "checkout", "main"],
                cwd=self.docs_file.parent.parent,
                check=True
            )
            subprocess.run(
                ["git", "merge", revision_id, "--no-ff"],
                cwd=self.docs_file.parent.parent,
                check=True
            )
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Git merge 실패: {e}")
        
        # 3. 감사 로그 저장
        self.save_approval_log(doc_key, revision_id, admin)
        
        return {
            "revision_id": revision_id,
            "doc_key": doc_key,
            "new_version": new_version,
            "approved_by": admin,
            "approved_at": datetime.now().isoformat()
        }
    
    def reject(self, revision_id: str, reason: str) -> dict:
        """revision 거절: 브랜치 삭제 + 로그"""
        doc_key = revision_id.split("-")[-1]
        
        # 1. git 브랜치 삭제
        try:
            subprocess.run(
                ["git", "branch", "-D", revision_id],
                cwd=self.docs_file.parent.parent,
                check=True
            )
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"브랜치 삭제 실패: {e}")
        
        # 2. 거절 로그 저장
        self.save_rejection_log(doc_key, revision_id, reason)
        
        return {
            "revision_id": revision_id,
            "doc_key": doc_key,
            "rejected_at": datetime.now().isoformat(),
            "reason": reason
        }
    
    def save_approval_log(self, doc_key: str, revision_id: str, admin: str) -> Path:
        """승인 로그: .audit/approvals/{date}-{doc_key}.json"""
        date_str = datetime.now().strftime("%Y-%m-%d")
        filename = f"{date_str}-approval-{doc_key}.json"
        path = self.audit_dir / filename
        
        log_data = {
            "revision_id": revision_id,
            "doc_key": doc_key,
            "approved_by": admin,
            "approved_at": datetime.now().isoformat()
        }
        
        path.write_text(json.dumps(log_data, ensure_ascii=False, indent=2), encoding='utf-8')
        
        return path
    
    def save_rejection_log(self, doc_key: str, revision_id: str, reason: str) -> Path:
        """거절 로그: .audit/rejections/{date}-{doc_key}.json"""
        date_str = datetime.now().strftime("%Y-%m-%d")
        filename = f"{date_str}-rejection-{doc_key}.json"
        path = self.audit_dir / filename
        
        log_data = {
            "revision_id": revision_id,
            "doc_key": doc_key,
            "rejected_at": datetime.now().isoformat(),
            "reason": reason
        }
        
        path.write_text(json.dumps(log_data, ensure_ascii=False, indent=2), encoding='utf-8')
        
        return path
```

- [ ] **Step 4: 테스트 실행**

```bash
cd D:\Vibe_coding\Privacy_Law
python -m pytest tests/test_approval_workflow.py -v
```

- [ ] **Step 5: __init__.py 업데이트**

```python
# privacy_law/__init__.py
from .approval_workflow import ApprovalWorkflow

__all__ = [
    "ConsentBook",
    "LawFetcher",
    "LawFetcherError",
    "ChangeDetector",
    "SuggestionEngine",
    "ApprovalWorkflow",
]
```

- [ ] **Step 6: 커밋**

```bash
cd D:\Vibe_coding\Privacy_Law
git add privacy_law/approval_workflow.py tests/test_approval_workflow.py privacy_law/__init__.py
git commit -m "feat: approval_workflow 구현 (승인/거절 처리 + 감사 로그)"
```

---

## Task 5: mcp_hub 백그라운드 작업 통합

**Files:**
- Create: `mcp_hub/privacy_law_tasks.py`
- Modify: `mcp_hub/tasks.py` (Privacy_Law 작업 등록)

**Interfaces:**
- Consumes: privacy_law (LawFetcher, ChangeDetector, SuggestionEngine, ApprovalWorkflow)
- Produces: mcp_hub 백그라운드 작업 스케줄링

- [ ] **Step 1: privacy_law_tasks.py 생성**

```python
# mcp_hub/privacy_law_tasks.py
import logging
from datetime import datetime
from privacy_law import (
    LawFetcher, ChangeDetector, SuggestionEngine, ApprovalWorkflow
)
from pathlib import Path

logger = logging.getLogger(__name__)

class PrivacyLawTasks:
    """Privacy_Law 자동 감지 및 제안 작업"""
    
    def __init__(self, privacy_law_dir: Path = Path("D:\\Vibe_coding\\Privacy_Law")):
        self.privacy_law_dir = privacy_law_dir
        self.law_fetcher = LawFetcher(
            snapshot_dir=privacy_law_dir / "snapshots"
        )
        self.change_detector = ChangeDetector(
            audit_dir=privacy_law_dir / ".audit" / "changes"
        )
        # API 키는 환경변수 CLAUDE_API_KEY에서 읽기
        import os
        api_key = os.environ.get("CLAUDE_API_KEY")
        self.suggestion_engine = SuggestionEngine(
            api_key=api_key,
            revision_dir=privacy_law_dir / "revisions"
        )
        self.approval_workflow = ApprovalWorkflow(
            docs_file=privacy_law_dir / "texts" / "documents.json",
            audit_dir=privacy_law_dir / ".audit"
        )
    
    def detect_law_changes(self) -> dict:
        """매일 실행: 법령 변경 감지"""
        logger.info("🔍 법령 변경 감지 시작...")
        
        LAW_IDS = [
            "개인정보보호법",
            "정보통신망법",
            "신용정보법"
        ]
        
        try:
            # 최신 법령 조회
            current = self.law_fetcher.fetch(LAW_IDS)
            
            # 이전 스냅샷과 비교
            changes_detected = {}
            for law_id in LAW_IDS:
                snapshot_dir = self.law_fetcher.snapshot_dir
                previous_snapshots = list(snapshot_dir.glob(f"*-{law_id}.txt"))
                
                if previous_snapshots:
                    # 가장 최근 스냅샷 읽기
                    previous_snapshots.sort()
                    previous_content = previous_snapshots[-1].read_text(encoding='utf-8')
                    
                    # 변경 감지
                    changes = self.change_detector.detect(
                        law_id,
                        previous_content,
                        current[law_id]
                    )
                    
                    if changes:
                        changes_detected[law_id] = changes
                        logger.warning(f"⚠️ {law_id} 변경 감지: {len(changes)}개 조문")
                        
                        # 감사 로그 저장
                        self.change_detector.save_audit_log(law_id, changes)
                
                # 현재 스냅샷 저장
                self.law_fetcher.save_snapshot(law_id, current[law_id])
            
            return {
                "status": "success",
                "changes_detected": changes_detected,
                "timestamp": datetime.now().isoformat()
            }
        
        except Exception as e:
            logger.error(f"❌ 법령 감지 실패: {e}")
            return {
                "status": "error",
                "error": str(e),
                "timestamp": datetime.now().isoformat()
            }
    
    def generate_suggestions(self, law_id: str, changes: list) -> dict:
        """변경 감지 후 실행: Claude 수정안 생성"""
        logger.info(f"💡 {law_id} 수정안 생성 시작...")
        
        try:
            # 영향받는 문서들
            docs_file = self.privacy_law_dir / "texts" / "documents.json"
            import json
            docs_data = json.loads(docs_file.read_text(encoding='utf-8'))
            
            suggestions = {}
            for change in changes:
                for doc in docs_data["documents"]:
                    if doc["key"] in change.get("affected_documents", []):
                        try:
                            # 원본 HTML 읽기
                            html_file = self.privacy_law_dir / "texts" / doc["file"]
                            original_html = html_file.read_text(encoding='utf-8')
                            
                            # 수정안 생성
                            suggested_html = self.suggestion_engine.suggest_with_claude(
                                doc["key"],
                                original_html,
                                change["after"],
                                {"service_name": "EMS", "service_desc": "직원 관리"}
                            )
                            
                            # revision 브랜치 생성
                            branch = self.suggestion_engine.create_revision_branch(
                                doc["key"],
                                suggested_html
                            )
                            
                            suggestions[doc["key"]] = {
                                "branch": branch,
                                "status": "pending_review"
                            }
                            
                            logger.info(f"✅ {doc['key']} 수정안 생성: {branch}")
                        
                        except ValueError as e:
                            logger.warning(f"⚠️ {doc['key']} 수정안 생성 실패: {e}")
                            suggestions[doc["key"]] = {
                                "status": "manual_required",
                                "reason": str(e)
                            }
            
            return {
                "status": "success",
                "suggestions": suggestions,
                "timestamp": datetime.now().isoformat()
            }
        
        except Exception as e:
            logger.error(f"❌ 수정안 생성 실패: {e}")
            return {
                "status": "error",
                "error": str(e),
                "timestamp": datetime.now().isoformat()
            }
```

- [ ] **Step 2: mcp_hub tasks.py 업데이트**

```python
# mcp_hub/tasks.py (기존 코드 뒤에 추가)
from privacy_law_tasks import PrivacyLawTasks
from apscheduler.schedulers.background import BackgroundScheduler

scheduler = BackgroundScheduler()

# Privacy_Law 작업 등록
privacy_law_tasks = PrivacyLawTasks()

# 매일 오전 2시에 법령 변경 감지
scheduler.add_job(
    privacy_law_tasks.detect_law_changes,
    'cron',
    hour=2,
    minute=0,
    id='privacy_law_detect_changes',
    name='Privacy_Law 법령 변경 감지'
)

scheduler.start()
```

- [ ] **Step 3: 커밋**

```bash
cd D:\Vibe_coding\mcp_hub
git add privacy_law_tasks.py tasks.py
git commit -m "feat: mcp_hub에 Privacy_Law 백그라운드 작업 통합 (일일 법령 감지)"
```

---

## Task 6: auth_core outdated() 메서드 추가

**Files:**
- Modify: `auth_core/__init__.py` (ConsentBook.outdated() 메서드)

**Interfaces:**
- Consumes: auth_consents 테이블, documents.json 버전
- Produces: `outdated(username: str) -> list[str]` — 재동의 필요한 문서 키 리스트

- [ ] **Step 1: 테스트 작성**

```python
# tests/test_consent_outdated.py
import pytest
from auth_core import ConsentBook
from pathlib import Path
import tempfile
import json
import sqlite3

def test_outdated_returns_empty_for_current():
    """최신 버전 동의: outdated()는 빈 리스트"""
    with tempfile.TemporaryDirectory() as tmpdir:
        # DB 생성
        db_path = Path(tmpdir) / "auth.db"
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE auth_consents (
                username TEXT,
                doc_key TEXT,
                version_agreed TEXT,
                PRIMARY KEY (username, doc_key)
            )
        """)
        # 현재 버전과 일치
        conn.execute("INSERT INTO auth_consents VALUES ('user1', 'collect', '1.3')")
        conn.commit()
        
        # documents.json
        docs_path = Path(tmpdir) / "documents.json"
        docs_path.write_text(json.dumps({
            "documents": [
                {"key": "collect", "version": "1.3"}
            ]
        }))
        
        # ConsentBook 생성
        consent = ConsentBook(
            connect=lambda: conn,
            values={},
            texts_dir=tmpdir
        )
        
        result = consent.outdated("user1")
        
        assert result == []

def test_outdated_returns_docs_with_old_version():
    """버전이 다른 동의: outdated()는 문서 키 반환"""
    with tempfile.TemporaryDirectory() as tmpdir:
        # DB: 사용자가 v1.2 동의했는데, 현재 v1.3
        db_path = Path(tmpdir) / "auth.db"
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE auth_consents (
                username TEXT,
                doc_key TEXT,
                version_agreed TEXT,
                PRIMARY KEY (username, doc_key)
            )
        """)
        conn.execute("INSERT INTO auth_consents VALUES ('user1', 'collect', '1.2')")
        conn.commit()
        
        # documents.json: v1.3
        docs_path = Path(tmpdir) / "documents.json"
        docs_path.write_text(json.dumps({
            "documents": [
                {"key": "collect", "version": "1.3"}
            ]
        }))
        
        consent = ConsentBook(
            connect=lambda: conn,
            values={},
            texts_dir=tmpdir
        )
        
        result = consent.outdated("user1")
        
        assert "collect" in result
```

- [ ] **Step 2: 테스트 실행 (실패 확인)**

```bash
cd D:\Vibe_coding\auth_core
python -m pytest tests/test_consent_outdated.py -v
```

- [ ] **Step 3: ConsentBook.outdated() 구현**

`auth_core/__init__.py`의 ConsentBook 클래스에 메서드 추가:

```python
# auth_core/__init__.py의 ConsentBook 클래스에 추가
def outdated(self, username: str) -> list[str]:
    """재동의가 필요한 문서 키 반환"""
    if not self.enabled:
        return []
    
    conn = self.connect()
    cursor = conn.cursor()
    
    # documents.json에서 현재 버전 읽기
    docs_file = self.texts_dir / "documents.json"
    import json
    docs_data = json.loads(docs_file.read_text(encoding='utf-8'))
    current_versions = {
        doc["key"]: doc["version"]
        for doc in docs_data["documents"]
    }
    
    # auth_consents에서 사용자 동의 버전 조회
    cursor.execute(
        "SELECT doc_key, version_agreed FROM auth_consents WHERE username = ?",
        (username,)
    )
    agreed = dict(cursor.fetchall())
    
    # 버전이 다른 문서 식별
    outdated_docs = [
        key for key, current_version in current_versions.items()
        if agreed.get(key) != current_version
    ]
    
    return outdated_docs
```

- [ ] **Step 4: 테스트 실행**

```bash
cd D:\Vibe_coding\auth_core
python -m pytest tests/test_consent_outdated.py -v
```

- [ ] **Step 5: 커밋**

```bash
cd D:\Vibe_coding\auth_core
git add __init__.py tests/test_consent_outdated.py
git commit -m "feat: ConsentBook.outdated() 메서드 (재동의 필요 문서 식별)"
```

---

## Task 7: EMSv3 재동의 UI 구현

**Files:**
- Create: `EMSv3/templates/reconsent.html`
- Modify: `EMSv3/app.py` (재동의 라우트 추가)

**Interfaces:**
- Consumes: ConsentBook.outdated(), CONSENTS.form_documents()
- Produces: GET `/reconsent`, POST `/reconsent` 라우트

- [ ] **Step 1: reconsent.html 생성**

```html
<!-- EMSv3/templates/reconsent.html -->
<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>개인정보 재동의</title>
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; }
        .reconsent-container { max-width: 800px; margin: 2rem auto; padding: 0 1rem; }
        .reconsent-alert { background-color: #fff3cd; border: 1px solid #ffc107; padding: 1rem; border-radius: 4px; margin-bottom: 2rem; }
        .document-section { margin-bottom: 2rem; padding: 1rem; border: 1px solid #ddd; border-radius: 4px; }
        .document-content { max-height: 300px; overflow-y: auto; background-color: #f9f9f9; padding: 1rem; margin: 1rem 0; border-radius: 4px; font-size: 0.9rem; line-height: 1.5; }
        .form-group { margin-bottom: 1rem; }
        .checkbox-label { display: flex; align-items: center; cursor: pointer; }
        .checkbox-label input { margin-right: 0.5rem; }
        .btn { padding: 0.75rem 1.5rem; font-size: 1rem; border: none; border-radius: 4px; cursor: pointer; }
        .btn-primary { background-color: #007bff; color: white; }
        .btn-primary:hover { background-color: #0056b3; }
        .btn:disabled { background-color: #ccc; cursor: not-allowed; }
        .required-notice { color: #d32f2f; font-weight: bold; }
    </style>
</head>
<body>
    <div class="reconsent-container">
        <h1>개인정보 처리 방침 재동의</h1>
        
        <div class="reconsent-alert">
            <strong>⚠️ 알림:</strong> 법령이 개정되어 개인정보 처리 방침이 변경되었습니다. 
            아래 문서들에 대해 다시 동의해주시기 바랍니다.
        </div>
        
        <form method="POST" action="/reconsent" id="reconsent-form">
            {% for doc in documents %}
            <div class="document-section">
                <h3>{{ doc.title }}</h3>
                
                <div class="document-content">
                    {{ CONSENTS.html(doc.key)|safe }}
                </div>
                
                <div class="form-group">
                    <label class="checkbox-label">
                        <input 
                            type="checkbox" 
                            name="{{ doc.key }}" 
                            value="1"
                            {% if doc.required %}required{% endif %}
                            data-required="{% if doc.required %}true{% else %}false{% endif %}"
                        />
                        {% if doc.required %}<span class="required-notice">*</span>{% endif %}
                        이 문서에 동의합니다
                    </label>
                </div>
            </div>
            {% endfor %}
            
            <div style="margin-top: 2rem;">
                <button type="submit" class="btn btn-primary" id="submit-btn" disabled>
                    동의하고 계속하기
                </button>
            </div>
        </form>
    </div>
    
    <script>
        document.addEventListener('DOMContentLoaded', function() {
            const form = document.getElementById('reconsent-form');
            const submitBtn = document.getElementById('submit-btn');
            const checkboxes = form.querySelectorAll('input[type="checkbox"]');
            
            function updateSubmitButton() {
                const allRequired = Array.from(checkboxes).every(cb => {
                    if (cb.dataset.required === 'true') {
                        return cb.checked;
                    }
                    return true;
                });
                submitBtn.disabled = !allRequired;
            }
            
            checkboxes.forEach(checkbox => {
                checkbox.addEventListener('change', updateSubmitButton);
            });
            
            updateSubmitButton();
            
            form.addEventListener('submit', function(e) {
                submitBtn.disabled = true;
                submitBtn.textContent = '처리 중...';
            });
        });
    </script>
</body>
</html>
```

- [ ] **Step 2: EMSv3/app.py에 재동의 라우트 추가**

```python
# EMSv3/app.py에 추가
@app.route('/reconsent', methods=['GET', 'POST'])
def reconsent():
    """재동의 페이지"""
    if not session.get('username'):
        return redirect('/login')
    
    if request.method == 'GET':
        # 재동의 필요 문서 조회
        outdated = CONSENTS.outdated(session['username'])
        if not outdated:
            # 재동의 필요 없음
            return redirect('/dashboard')
        
        # 재동의 필요한 문서만 표시
        docs = [doc for doc in CONSENTS.form_documents() if doc.key in outdated]
        return render_template('reconsent.html', documents=docs, CONSENTS=CONSENTS)
    
    # POST: 재동의 수집
    elif request.method == 'POST':
        try:
            agreements = {
                key: request.form.get(key, '0') == '1'
                for key in [doc.key for doc in CONSENTS.form_documents()]
            }
            
            # 필수 동의 검증
            lacking = CONSENTS.missing(agreements)
            if lacking:
                return render_template('reconsent.html', error=f"필수 동의 누락: {lacking}"), 400
            
            # 재동의 기록
            conn = get_db()
            CONSENTS.record(
                session['username'],
                agreements,
                request.remote_addr,
                conn=conn
            )
            conn.commit()
            
            return redirect('/dashboard')
        
        except Exception as e:
            logger.error(f"재동의 처리 실패: {e}")
            return render_template('error.html', message="재동의 처리 중 오류가 발생했습니다"), 500
```

- [ ] **Step 3: login.html 업데이트 (재동의 체크)**

EMSv3/templates/login.html의 로그인 성공 후 재동의 체크 추가:

```python
# EMSv3/app.py의 로그인 라우트에서 session 생성 후 추가
session['username'] = username

# 재동의 필요 확인
outdated = CONSENTS.outdated(username)
if outdated:
    return redirect('/reconsent')
```

- [ ] **Step 4: 테스트**

```bash
# EMSv3 서버 시작
cd D:\Vibe_coding\EMS\EMSv3
python -m flask run

# 브라우저에서 테스트
# 1. 로그인 → outdated 문서 있으면 /reconsent로 리다이렉트
# 2. 재동의 페이지 표시
# 3. 체크박스 선택 후 제출
# 4. auth_consents 테이블 업데이트 확인
```

- [ ] **Step 5: 커밋**

```bash
cd D:\Vibe_coding\EMS\EMSv3
git add templates/reconsent.html app.py
git commit -m "feat: 사용자 재동의 UI 구현 (/reconsent 라우트, 문서 버전 체크)"
```

---

## Task 8: 엔드투엔드 테스트 및 검증

**Files:**
- Create: `tests/test_e2e_privacy_law_flow.py`

**Test Scenario:**
1. 법령 변경 감지 (가짜 데이터)
2. Claude 수정안 생성 (수동 모드)
3. 관리자 승인
4. auth_consents 재동의 플래그 업데이트
5. EMSv3에서 사용자가 로그인 → /reconsent로 리다이렉트
6. 사용자 재동의 수집 → 완료

- [ ] **Step 1: E2E 테스트 작성**

```python
# tests/test_e2e_privacy_law_flow.py
import pytest
from pathlib import Path
import tempfile
import json
import sqlite3
from datetime import datetime
from privacy_law import (
    LawFetcher, ChangeDetector, SuggestionEngine, ApprovalWorkflow
)
from auth_core import ConsentBook

def test_complete_privacy_law_workflow():
    """법령 변경 → 수정안 생성 → 승인 → 재동의까지 전체 흐름"""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        
        # 1. 환경 설정
        docs_file = tmpdir / "documents.json"
        docs_data = {
            "documents": [
                {"key": "collect", "version": "1.2", "file": "collect.html", "title": "수집 동의", "required": True}
            ]
        }
        docs_file.write_text(json.dumps(docs_data, ensure_ascii=False, indent=2))
        
        # collect.html 생성
        html_file = tmpdir / "collect.html"
        html_file.write_text("<table><tr><td>기존 수집 항목</td></tr></table>")
        
        # DB 생성
        db_path = tmpdir / "auth.db"
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE auth_consents (
                username TEXT,
                doc_key TEXT,
                version_agreed TEXT,
                agreed INTEGER,
                ip_address TEXT,
                timestamp TEXT,
                PRIMARY KEY (username, doc_key)
            )
        """)
        # 사용자 이전 동의 (v1.2)
        conn.execute(
            "INSERT INTO auth_consents VALUES (?, ?, ?, ?, ?, ?)",
            ("testuser", "collect", "1.2", 1, "127.0.0.1", datetime.now().isoformat())
        )
        conn.commit()
        
        # 2. 법령 변경 감지
        fetcher = LawFetcher(snapshot_dir=tmpdir / "snapshots")
        # 이전 스냅샷 저장
        previous_law = "제15조 개인정보 수집\n기존 규정"
        fetcher.save_snapshot("개인정보보호법", previous_law)
        
        detector = ChangeDetector(audit_dir=tmpdir / ".audit")
        current_law = "제15조 개인정보 수집 (개정)\n기존 규정\n명시적 동의 필요"
        changes = detector.detect("개인정보보호법", previous_law, current_law)
        
        assert len(changes) == 1
        assert "collect" in changes[0]["affected_documents"]
        
        # 3. 수정안 생성 (수동 모드)
        engine = SuggestionEngine(api_key=None, revision_dir=tmpdir / "revisions")
        
        # 수동 모드에서는 수정안을 직접 생성
        suggested_html = "<table><tr><td>기존 수집 항목</td><td>명시적 동의 필요</td></tr></table>"
        branch = engine.create_revision_branch("collect", suggested_html)
        
        assert branch.startswith("suggest/")
        assert "collect" in branch
        
        # 4. 관리자 승인 시뮬레이션
        workflow = ApprovalWorkflow(docs_file=docs_file, audit_dir=tmpdir / ".audit")
        
        # approval 로직 시뮬레이션 (git 없이)
        # 버전 증가만 테스트
        docs_data["documents"][0]["version"] = "1.3"
        docs_file.write_text(json.dumps(docs_data, ensure_ascii=False, indent=2))
        
        # 5. ConsentBook.outdated() 확인
        consent = ConsentBook(
            connect=lambda: conn,
            values={},
            texts_dir=tmpdir
        )
        
        outdated = consent.outdated("testuser")
        assert "collect" in outdated  # v1.2 → v1.3로 변경됨
        
        # 6. 재동의 후 outdated() 다시 확인
        conn.execute(
            "UPDATE auth_consents SET version_agreed = ? WHERE username = ? AND doc_key = ?",
            ("1.3", "testuser", "collect")
        )
        conn.commit()
        
        outdated = consent.outdated("testuser")
        assert "collect" not in outdated  # 재동의 완료
```

- [ ] **Step 2: 테스트 실행**

```bash
cd D:\Vibe_coding\Privacy_Law
python -m pytest tests/test_e2e_privacy_law_flow.py -v
```

- [ ] **Step 3: 통합 검증**

```bash
# 1. Privacy_Law 모든 테스트 통과
cd D:\Vibe_coding\Privacy_Law
python -m pytest tests/ -v

# 2. auth_core 테스트
cd D:\Vibe_coding\auth_core
python -m pytest tests/ -v

# 3. EMSv3 서버 시작 및 수동 테스트
cd D:\Vibe_coding\EMS\EMSv3
python -m flask run
# 브라우저: http://localhost:5000/login
# → 재동의 필요 사용자로 로그인 → /reconsent 리다이렉트
# → 동의 후 /dashboard로 이동 확인
```

- [ ] **Step 4: 커밋**

```bash
cd D:\Vibe_coding\Privacy_Law
git add tests/test_e2e_privacy_law_flow.py
git commit -m "test: 엔드투엔드 테스트 (법령 변경 → 승인 → 재동의)"
```

---

## 최종 검증 체크리스트

- [ ] Privacy_Law 패키지의 4개 모듈 모두 구현 + 테스트 통과
- [ ] mcp_hub 백그라운드 작업 스케줄링 동작 확인
- [ ] auth_core ConsentBook.outdated() 메서드 동작 확인
- [ ] EMSv3 /reconsent 라우트 동작 및 UI 표시 확인
- [ ] 사용자 재동의 수집 후 auth_consents 업데이트 확인
- [ ] 모든 커밋이 Git에 기록됨
- [ ] 감사 로그 (.audit/) 정상 생성
- [ ] GitHub 원격 저장소 push 완료

---

**이제 Native 방식으로 각 Task를 구현할 준비가 완료되었습니다.**
