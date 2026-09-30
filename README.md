# privacy_law — 개인정보 법정 문구 · 가입 동의 공용 모듈

마지막 업데이트: 2026-09-30 21:50

> 개인정보 보호법 등 법령 바뀔 때 마다 검증하고 반영하는 MCP

Master MCP Hub 아래 웹 서비스(EMS, 이후 카드·장례 등)가 같이 쓰는 **이용약관 · 개인정보 처리방침 · 개인정보 수집·이용 동의 · 마케팅 수신** 문구와 동의 기록입니다.
개인정보 관련 법령이 바뀌면 **이 저장소의 `privacy_law/texts/` 만 고치면** 모든 서비스에 한 번에 반영됩니다.

## 법령이 바뀌었을 때 — 수정 화면

`editor.bat` 더블클릭 → 브라우저에 **개인정보 문구 수정** 화면이 열린다 (이 PC 에서만, 실행할 때마다 새 접속 토큰).

- 왼쪽에서 문서 선택 → 원문(HTML) 수정, 오른쪽 미리보기(가입 화면과 같은 모양)
- 내용이 바뀌면 **[오늘 날짜로]** 로 버전 올리기 → **[저장]** (서비스 화면에 바로 반영)
- 변경 내용 한 줄 적고 **[GitHub 반영]** → 커밋 · push. 각 서비스 서버는 `git pull`

직접 파일을 고쳐도 된다:

1. `privacy_law/texts/*.html` 문구를 고친다 — 재시작 없이 다음 요청부터 반영(파일 수정 시각 확인)
2. 내용이 바뀐 문서는 `privacy_law/texts/documents.json` 의 `version` 을 올린다 (예: `"2027-01-01"`)
   → 동의 기록(`auth_consents`)의 버전과 달라진 회원은 `outdated(아이디)` 로 재동의 대상 조회
3. 커밋 · push → 각 서비스 서버에서 pull (editable 설치면 pull 만으로 반영)

## 구성

| 파일 | 역할 |
|---|---|
| `texts/documents.json` | 문서 목록: 키 · 제목 · 필수 여부 · 버전 · 본문 파일 |
| `texts/terms.html` · `privacy.html` · `collect.html` | 본문. `{{ service_name }}` `{{ service_desc }}` `{{ collect_items }}` 는 서비스가 값으로 채움 |
| `editor.py` + `editor.bat` | 문구 수정 화면(브라우저, 127.0.0.1 전용): 수정 · 미리보기 · 버전 · GitHub 반영 |
| `__init__.py` | `ConsentBook`: 문구 읽기(즉시 반영) · 필수 동의 검증 · 동의 기록 · 재동의 대상 조회 |

서비스는 **문구를 덮어쓸 수 없고** 값(서비스명 · 설명 · 수집 항목)만 넘깁니다.

## 서비스에서 쓰는 법

```python
# pip install -e D:\Vibe_coding\modules\Privacy_Law
# 보통은 auth_core 를 통해 쓴다 (에디션에 따라 자동으로 켜고 끔)
consents = auth.attach_consents(get_db, values={"service_desc": "...", "collect_items": "..."})
consents.form_documents()          # 가입 화면에 보일 동의 항목
consents.missing({"terms": ..., "privacy": ..., "collect": ..., "marketing": ...})   # 빠진 필수 동의
consents.record(username, agreements, ip, conn=conn)   # 가입과 같은 트랜잭션에 기록
consents.outdated(username)        # 문구 개정 후 재동의가 필요한 문서
```

## 폐쇄망 에디션

폐쇄망은 개인정보 처리자가 **고객 사업장**이므로 가입 화면 동의를 받지 않습니다(`enabled=False`, auth_core 가 라이선스로 자동 판단).
판매 시점의 `texts/` 문구를 참고용으로 함께 전달하고, 이후 개정은 사업장이 맡습니다.

## 인증키

```
python -m privacy_law.keys set LAW_OC <법제처 OC>     # .secrets.json 에 저장 (git 제외). 환경변수 LAW_OC 가 우선
python -m privacy_law.keys check                      # 실제로 법제처를 불러 확인
python -m privacy_law.keys set ANTHROPIC_API_KEY <키>  # 선택: 수정안 자동 초안
```

LAW_OC 는 https://open.law.go.kr 회원가입 → OPEN API 신청에서 받는다(가입 이메일 @ 앞부분). **신청 때 이 PC/서버의 공인 IP·도메인을 등록해야 한다** — 등록 안 된 곳에서는 "사용자 정보 검증에 실패하였습니다"가 온다.

## 서비스별 문구 — 프로필

서비스마다 다른 사실(위탁 · 제3자 제공 · 국외 이전 · 보호책임자 · 쿠키 · 동의 없이 처리하는 항목 …)은 `ServiceProfile` 의 `practice` 로 넘기면 문구가 자동으로 채워진다. 서비스 고유 조항은 `sections`, 전용 문서 폴더는 `texts_dir`(공용 개정이 자동 반영되지 않아 점검에서 경고). 키 목록은 `privacy_law/profile.py` 머리말.

```
python -m privacy_law.requirements                 # 공용 문구 점검
python -m privacy_law.requirements profile.json    # 서비스 프로필로 점검 (오류가 있으면 종료코드 1)
```

점검 규칙(`requirements.py`)은 법 제30조 · 시행령 제31조(처리방침 기재 사항), 제22조③(동의 없는 처리 근거), 제31조의2, 제37조의2, 제22조⑤(마케팅 선택), 정보통신망법 제50조⑧ · 시행령 제62조의3(2년마다 수신동의 확인) 등을 현행 조문 기준으로 옮긴 것이다. 규칙의 근거 조문 해시는 `requirements_basis.json` 에 있고, 조문이 바뀌면 감시가 `stale_rules` 로 알린다 — 규칙을 검토한 뒤 `python -m privacy_law.requirements rebase`.

## 법령 · 외부 기준 감시 (mcp_hub 가 12시간마다 실행)

`python -m privacy_law.watch [profile.json ...]` — 결과는 `reports/latest.json`. 서비스 프로필은 인자 또는 `profiles/*.json`.

1. **법령** (`watch.json`): 개인정보 보호법 · 시행령, 정보통신망법, 전자상거래법 시행령(거래기록 보존), 통신비밀보호법 시행령(접속기록). 개정되면 조문 비교 → 영향 문서 수정 제안. 매핑마다 기대 제목을 적어 두고 실제 제목과 다르면 `mapping_issues` (예: 제39조의6 은 지금 '소송기록 열람 등의 청구 통지 등' 이라 매핑에서 뺐다).
2. **규칙 근거**: `stale_rules`.
3. **문구 점검**: 공용 문구 + 서비스 프로필 → `findings`.
4. **개인정보보호위원회** (`sources.py`): 보도자료 · 결과의 공표 · 고시 · 안내서 · 결정문 새 글(`notices`, 관련 주제 표시). 결과의 공표 첨부(PDF · HWPX)에서 위반 조항을 뽑아 `violations`(舊법 조항 제외) → 서비스가 그 조항의 이행 내역을 밝히지 않았으면 `case_gaps`. 첨부가 이미지뿐이면 `manual_review`.
5. **다른 회사 처리방침** (`benchmarks.json`: 네이버 · 카카오): 버전 변화(`policy_changes`)와 다른 곳은 다루는데 우리는 없는 주제(`benchmark_gaps`, 해당 여부는 practice 로 판단).

PDF 추출은 `pip install -e .[pdf]` (pypdf). 테스트 fixture(`tests/fixtures/`)는 실제 응답을 잘라 둔 것이고, `PRIVACY_LAW_LIVE=1 pytest -k live` 는 실제 사이트를 부른다.

### 수정 제안 흐름

| 파일 | 역할 |
|---|---|
| `watch.json` | 감시할 법령과 조문 → 영향 문서 · 기대 제목 매핑 |
| `law_fetcher.py` | 법제처 Open API(DRF): 현행 법령 버전(MST) · 조문 |
| `change_detector.py` | 조 단위 비교, 영향 문서 선택 |
| `suggestion_engine.py` | 수정 요청문 · 수정안(자동: Claude API / 수동: Claude Code) · `proposals/` 저장 |
| `approval_workflow.py` | 승인(문구 반영 + 버전 올림 → 재동의 발생 + git 커밋) · 거절 · 감사 기록 |
| `watch.py` | 한 바퀴 실행 (위 1~5) |
| `keys.py` · `profile.py` · `requirements.py` · `sources.py` | 인증키 · 서비스 프로필 · 점검 규칙 · 개인정보위/처리방침 수집 |

- 인증키는 위 [인증키] 참고. `PRIVACY_LAW_MODEL`: 자동 초안 모델(기본 claude-sonnet-5)
- 처음 실행은 기준 스냅샷(`snapshots/`)만 저장. 이후 법령일련번호가 바뀌면 조문을 비교해 제안을 만든다.
- 제안 상태: `needs_draft`(수정안 없음) → `pending`(검토 대기) → `approved` / `rejected`. 기록은 `.audit/audit.jsonl`.
- 수동 모드: Claude Code 에 "Privacy_Law 수정안 만들어줘" → `proposals/<id>/prompt.md` 대로 수정안을 쓰고 허브의 초안 입력으로 넣는다.
- 승인 시 원문이 제안 이후 바뀌었으면(editor 로 누가 고침) 승인을 거부한다 — 거절 후 다시 감지되게 한다.
