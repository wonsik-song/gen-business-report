---
description: AI 기반 맞춤형 기획서 작성 및 평가 서비스의 RLS, 보안, 감사 로그 규칙
---

# PRD Security and RLS Rules

## RLS Rules

### projects
접근 조건:
- auth.uid() = user_id

### evaluation_prompts
접근 조건:
- auth.uid() = user_id

### documents
접근 조건:
- documents.project_id가 속한 project의 user_id = auth.uid()

### evaluations
접근 조건:
- evaluations.document_id가 속한 document의 project owner = auth.uid()

### audit_logs
정책:
- INSERT: 앱 레벨 기록 허용
- SELECT / UPDATE / DELETE: service_role 전용

audit_logs는 일반 사용자 조회 대상으로 노출하지 않는다.

## Security Rules
반드시 구현:
- 프롬프트 인젝션 방어
- API 실패 로그 기록
- 평가 실패 상태 기록
- Rate limit 위반 기록
- 보안 이벤트 audit_logs 적재

권장 action_type 예시:
- GENERATE_SUCCESS
- GENERATE_FAILED
- EVALUATE_SUCCESS
- EVALUATE_PARTIAL_ERROR
- EVALUATE_FAILED
- RATE_LIMIT
- PROMPT_INJECTION

## Logging Rules
- severity는 INFO / WARN / ERROR / SECURITY를 사용한다
- details에는 원인과 payload 일부를 저장한다
- 민감 정보 원문은 직접 저장하지 않는다
- 실패 상태도 정상 흐름처럼 저장 가능해야 한다

## Rate Limit Rules
기준 시간대:
- KST 자정 기준으로 일일 카운트 초기화

### 생성 횟수 제한
- 일 최대 5회
- 집계 테이블: audit_logs
- 집계 조건:
  - action_type = 'GENERATE_SUCCESS'
  - created_at >= 오늘 KST 자정

Planner Agent의 FULL_DOCUMENT 정상 완료 시 반드시 audit_logs에 GENERATE_SUCCESS를 기록한다.

### 평가 횟수 제한
- 일 최대 10회
- 집계 테이블: evaluations
- 집계 조건:
  - status IN ('SUCCESS', 'PARTIAL_ERROR')
  - created_at >= 오늘 KST 자정

FAILED 상태는 평가 횟수 차감 대상이 아니다.

## Access Control Guardrails
- RLS 없는 상태로 사용자 데이터 테이블을 공개하지 않는다
- audit_logs를 일반 사용자 UI에 직접 노출하지 않는다
- service_role 전용 작업은 사용자 세션 토큰으로 대체하지 않는다
- 프로젝트 전용 evaluation_prompts는 글로벌 프롬프트보다 우선한다

## Build Order for Security
항상 아래 순서로 진행한다.
1. 테이블 및 FK 정의
2. CHECK / UNIQUE / Partial Unique Index 정의
3. RLS 정책 정의
4. RPC 구현
5. Rate Limit / Logging / Guardrail 연결
6. E2E 보안 검증

## Hard Prohibitions
- RLS 없는 영속화 구조 제안 금지
- FAILED 상태를 평가 횟수 차감 대상으로 처리 금지
- audit_logs 일반 사용자 조회 허용 금지
