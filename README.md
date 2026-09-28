# privacy_law — 개인정보 법정 문구 · 가입 동의 공용 모듈

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
# pip install -e D:\Vibe_coding\Privacy_Law
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
