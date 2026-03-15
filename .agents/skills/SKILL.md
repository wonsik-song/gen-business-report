---
name: prd-writer-evaluator
description: AI 기반 맞춤형 기획서 작성 및 평가 서비스의 구현 워크플로우
---

# Skill: PRD Writer Evaluator

## Purpose
이 skill은 사용자의 채팅 입력 또는 파일 입력을 기반으로
1) 기획서 목차를 생성하고
2) 본문을 생성하고
3) draft 자동 저장과 버전 확정을 수행하며
4) 평가 프롬프트를 적용해 문서를 평가하고
5) 결과를 Streamlit UI에 연결하는 작업 절차를 정의한다.

이 skill은 다음 상황에서 사용한다.
- 프로젝트 초기 구현
- DB/RLS/RPC부터 UI까지 일관된 흐름으로 구현할 때
- Planner / Evaluator / Streamlit 연결 작업을 반복할 때
- 운영 안정성까지 포함한 MVP 작업을 할 때

## Required Inputs
작업 시작 전 아래 정보를 확인한다.
- 현재 프로젝트 폴더 구조
- Supabase 사용 여부
- Streamlit 앱 진입 파일 위치
- ADK Agent 코드 위치
- Planner Agent / Evaluator Agent 분리 여부
- 문서 생성 결과 저장 방식
- 평가 결과 저장 방식
- 평가 프롬프트 저장 방식

## Expected Outputs
이 skill을 수행하면 아래 결과물이 준비되어야 한다.
- DB DDL
- Partial Unique Index
- RLS 정책
- RPC 함수
- Planner Agent 인터페이스
- Evaluator Agent 인터페이스
- Streamlit 화면 연결
- Rate Limit 쿼리
- Logging / Guardrail 처리
- E2E 검증 체크리스트

## Workflow
### Step 1. 프로젝트 구조 탐색
먼저 아래를 확인한다.
- Streamlit 앱 엔트리 파일
- Supabase 연동 코드
- Agent 관련 코드 위치
- 환경변수 관리 방식
- 데이터 모델 정의 파일
- 공통 유틸 위치

출력:
- 현재 구조 요약
- 누락된 디렉토리 / 파일 제안

### Step 2. DB 스키마 설계
다음 테이블을 우선 설계한다.
- public.users
- projects
- documents
- evaluation_prompts
- evaluations
- audit_logs

검증 체크:
- FK 연결이 맞는가
- ON DELETE 정책이 적절한가
- status / severity CHECK 제약이 있는가
- updated_at 컬럼이 필요한 테이블에 포함되었는가

### Step 3. 인덱스 및 제약 추가
반드시 아래 제약을 반영한다.

1. documents
- UNIQUE(project_id, version_num)
- 프로젝트당 draft 1개 제한

2. evaluation_prompts
- 프로젝트별 프롬프트 1개
- 글로벌 프롬프트 1개

검증 체크:
- Partial Unique Index가 정확한 WHERE 절을 가지는가
- 버전 충돌 가능성이 제거되었는가

### Step 4. RLS 정책 작성
다음 순서로 정책을 작성한다.
1. projects → owner only
2. documents → project owner only
3. evaluations → document owner only
4. evaluation_prompts → owner only
5. audit_logs → insert 제한 허용, 조회는 관리자만

검증 체크:
- 직접 테이블 접근 시 다른 사용자 데이터가 보이지 않는가
- JOIN 기반 policy가 정확히 동작하는가

### Step 5. RPC 구현
구현 대상:

#### A. upsert_document_draft
목적:
- 자동 저장
- 기존 draft update 또는 신규 draft insert
- updated_at 갱신

구현 규칙:
- projects row FOR UPDATE
- 기존 draft 있으면 UPDATE
- 없으면 MAX(version_num)+1
- 표준 JSON 반환

#### B. finalize_version
목적:
- draft를 확정본으로 전환

구현 규칙:
- projects row FOR UPDATE
- 현재 draft 조회
- 없으면 error JSON 반환
- 있으면 is_draft=false 처리

검증 체크:
- 동시 저장 시 중복 draft가 생기지 않는가
- 표준 에러 스키마가 유지되는가

### Step 6. Planner Agent 구현
Planner Agent는 두 단계로 동작한다.

#### Mode 1. OUTLINE
입력:
- user_intent
- context_text

출력:
- 목차 중심 Markdown

#### Mode 2. FULL_DOCUMENT
입력:
- user_intent
- context_text
- 승인된 outline_md

출력:
- 완성형 Markdown 문서

구현 규칙:
- 출력은 Markdown 문자열
- 500 오류는 최대 3회 재시도
- 실패 시 상위 레벨에서 핸들링 가능한 에러 반환

예시 입력:
```json
{
  "mode": "OUTLINE",
  "user_intent": "온라인 스터디 매칭 앱 기획해줘",
  "context_text": "사용자 업로드 파일 내용",
  "outline_md": null
}
```

### Step 7. Draft 저장 흐름 연결
문서 편집기 변경 시:
- upsert_document_draft 호출

사용자가 [새 버전으로 저장] 클릭 시:
- finalize_version 호출

구현 규칙:
- autosave와 명시 저장을 분리한다
- 저장 결과를 UI 상태와 DB 상태에 동기화한다
- 실패 메시지는 사용자에게 구조화해서 보여준다

### Step 8. Evaluation Prompt 적용
평가 전 아래 순서로 프롬프트를 조회한다.
1. 프로젝트 전용 evaluation_prompts 조회
2. 없으면 사용자 글로벌 evaluation_prompts 조회
3. 없으면 시스템 기본 프롬프트 사용

검증 체크:
- 프로젝트 전용이 글로벌보다 우선되는가
- 프롬프트 조회 결과가 중복 없이 1건으로 보장되는가

### Step 9. Evaluator Agent 구현
입력:
- 문서 Markdown
- custom_rules
- 평가 기준 rubric

출력:
- JSON 스키마 고정

성공 응답 예시:
```json
{
  "status": "SUCCESS",
  "total_score": 85,
  "category_scores": {
    "logic": 25,
    "feasibility": 28,
    "ux_flow": 15,
    "business": 17
  },
  "summary": "전반적인 설계는 좋지만 예외 흐름이 일부 누락됨",
  "strengths": [
    "핵심 흐름이 명확함",
    "데이터 구조 확장성이 좋음"
  ],
  "missing_points": [
    "비회원 예외 처리",
    "결제 실패 재시도 흐름"
  ]
}
```

Fallback 응답 예시:
```json
{
  "status": "PARTIAL_ERROR",
  "total_score": null,
  "category_scores": null,
  "summary": "평가 결과를 구조화하는 데 실패했습니다. 아래 원문을 확인해주세요.",
  "strengths": [],
  "missing_points": [],
  "raw_text": "모델이 반환한 원문 전체"
}
```

구현 규칙:
- summary 필수
- strengths 필수
- missing_points 필수
- JSON 파싱 실패 시 PARTIAL_ERROR 저장
- 모델 호출 실패 시 FAILED 저장

### Step 10. 평가 결과 저장
평가 완료 후 evaluations에 저장한다.

저장 규칙:
- SUCCESS / PARTIAL_ERROR / FAILED 중 하나로 저장
- prompt_tokens 저장
- comp_tokens 저장
- raw_text fallback이 있으면 feedback_json에 포함
- total_score는 실패 상태에서 NULL 허용

검증 체크:
- UI 렌더링 가능한 구조인가
- 후속 통계 집계 가능한 구조인가

### Step 11. Rate Limit 구현
#### 생성 제한
- audit_logs 기준
- action_type='GENERATE_SUCCESS'
- KST 자정 이후 count
- 일 5회 제한

#### 평가 제한
- evaluations 기준
- status IN ('SUCCESS', 'PARTIAL_ERROR')
- KST 자정 이후 count
- 일 10회 제한

구현 규칙:
- FAILED는 차감하지 않는다
- 제한 초과 시 호출 전에 차단한다
- 초과 시 audit_logs에 RATE_LIMIT 기록

### Step 12. Guardrail 및 Logging 연결
반드시 기록할 이벤트:
- GENERATE_SUCCESS
- GENERATE_FAILED
- EVALUATE_SUCCESS
- EVALUATE_PARTIAL_ERROR
- EVALUATE_FAILED
- RATE_LIMIT
- PROMPT_INJECTION

구현 규칙:
- severity를 상황에 따라 INFO / WARN / ERROR / SECURITY로 구분
- details에 원인과 payload 일부를 저장
- 민감 정보는 직접 저장하지 않는다

### Step 13. Streamlit UI 연결
필수 화면:
1. 로그인
2. 프로젝트 목록
3. 입력 패널
4. OUTLINE 결과 및 승인
5. 문서 에디터
6. 평가 결과 패널
7. 평가 프롬프트 설정 화면
8. 버전 저장 / 이력
9. Markdown export

권장 레이아웃:
- Sidebar: 프로젝트 / 버전 / 설정
- Main left: 채팅 또는 입력
- Main right: 에디터 또는 평가 결과

### Step 14. Export 구현
최종 문서는 Streamlit의 다운로드 버튼으로 `.md` 형식 저장을 지원한다.

검증 체크:
- 현재 확정본 기준으로 다운로드 되는가
- draft와 확정본이 혼동되지 않는가

### Step 15. E2E 검증
최종 확인 항목:

#### 생성 시나리오
- 프로젝트 생성
- OUTLINE 생성
- OUTLINE 승인
- FULL_DOCUMENT 생성
- draft 저장

#### 편집 시나리오
- autosave 동작
- draft 1개 제약 유지
- finalize_version 성공
- 이후 수정 시 새 draft 파생

#### 평가 시나리오
- 커스텀 프롬프트 적용
- SUCCESS 저장
- PARTIAL_ERROR fallback 저장
- FAILED 비차감 확인

#### 보안 시나리오
- 다른 사용자 프로젝트 조회 차단
- audit_logs 직접 조회 차단
- rate limit 초과 차단

## Implementation Hints
작업 시 아래 원칙을 따른다.
- 먼저 스키마를 확정하고 UI를 나중에 붙인다
- 평가 스키마를 먼저 고정한 뒤 Evaluator를 연결한다
- autosave는 RPC 계약이 먼저여야 한다
- Streamlit session state와 DB 상태를 분리해서 생각한다
- 실패 상태도 정상 흐름처럼 저장하고 렌더링한다

## Must Avoid
- FastAPI 코드 생성
- Planner와 Evaluator 역할 혼합
- 자유형 텍스트 평가 응답 저장
- RLS 없이 빠르게 구현하려는 우회
- draft 복수 허용
- FAILED를 평가 횟수 차감으로 처리
- 프로젝트 전용 프롬프트 우선순위 누락
