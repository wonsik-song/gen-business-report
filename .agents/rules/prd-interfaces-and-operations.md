---
description: AI 기반 맞춤형 기획서 작성 및 평가 서비스의 RPC, 인터페이스, 운영 규칙
---

# PRD Interfaces and Operations Rules

## RPC Contract Rules

### finalize_version
설명:
- 현재 프로젝트의 draft 문서를 확정 저장한다.
- 동시성 제어를 위해 projects row를 FOR UPDATE로 잠근다.
- 확정 대상 draft가 없으면 표준 에러 JSON을 반환한다.

성공 응답 예시:
```json
{
  "status": "success",
  "data": {
    "document_id": "uuid-...",
    "version_num": 2,
    "is_draft": false
  }
}
```

실패 응답 예시:
```json
{
  "status": "error",
  "error_type": "NO_DRAFT_FOUND",
  "message": "확정할 draft 문서가 없습니다.",
  "data": null
}
```

### upsert_document_draft
설명:
- 프로젝트 row를 FOR UPDATE로 잠근다.
- draft가 있으면 UPDATE
- 없으면 MAX(version_num)+1로 INSERT
- updated_at = now()를 명시적으로 기록한다

성공 응답 예시:
```json
{
  "status": "success",
  "data": {
    "document_id": "uuid-...",
    "version_num": 3,
    "is_draft": true
  }
}
```

모든 RPC는 성공/실패 모두 표준 JSON 객체를 반환해야 한다.

## Planner Agent Interface Rules

### Input Parameters
- mode: OUTLINE | FULL_DOCUMENT
- user_intent: 사용자 직접 입력 요구사항
- context_text: 업로드 파일 파싱 원문
- outline_md: FULL_DOCUMENT 모드에서만 사용

### Output
- Markdown String

예외 처리:
- 500 계열 실패 시 최대 3회 재시도
- Exponential Backoff 적용
- 최종 실패 시 구조화된 오류 반환

## Evaluator Response Rules
성공 응답은 반드시 아래 필드를 포함해야 한다.

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
  "summary": "전반적인 흐름은 좋지만 예외 처리가 일부 누락되어 있습니다.",
  "strengths": [
    "핵심 기능 흐름이 명확함",
    "확장 가능한 데이터 구조"
  ],
  "missing_points": [
    "예외 플로우 보완 필요",
    "실패 재시도 정책 명시 필요"
  ]
}
```

PARTIAL_ERROR 응답은 반드시 fallback 원문을 유지한다.

```json
{
  "status": "PARTIAL_ERROR",
  "total_score": null,
  "category_scores": null,
  "summary": "평가 결과를 구조화하는 데 실패했습니다. 아래 원문 결과를 확인해주세요.",
  "strengths": [],
  "missing_points": [],
  "raw_text": "LLM raw text..."
}
```

규칙:
- summary는 필수
- strengths는 필수 배열
- missing_points는 필수 배열
- JSON 스키마를 벗어난 자유 서술형 반환 금지

## Operational Rules
- 생성 결과와 평가 결과는 재현 가능하게 저장한다
- 사용자 입력 파일 원문과 생성 결과의 연결 관계를 추적 가능하게 유지한다
- 실패 상태도 반드시 기록하고 UI에서 식별 가능해야 한다
- autosave는 RPC 계약 이후 연결한다
- Streamlit session state와 DB 상태를 구분한다

## AGENT_TEST_MODE Rules
목적:
- UI/흐름 테스트 시 실제 LLM 호출 없이 Planner/Evaluator 결과를 검증한다.

환경변수:
- `AGENT_TEST_MODE`를 사용한다.
- 활성값: `1`, `true`, `yes`, `on` (대소문자 무시)
- 비활성값: 그 외 모든 값

동작 규칙:
- `AGENT_TEST_MODE` 활성 시 Planner Agent는 mock Markdown을 반환한다.
- `AGENT_TEST_MODE` 활성 시 Evaluator Agent는 mock JSON을 반환한다.
- 테스트 모드에서는 외부 LLM/A2A 호출을 강제하지 않는다.
- 테스트 모드 결과의 `prompt_tokens`, `comp_tokens`는 `0`을 기본값으로 허용한다.

품질 규칙:
- 테스트 모드 응답도 운영 스키마를 반드시 준수해야 한다.
- Evaluator mock 응답은 `status`, `summary`, `strengths`, `missing_points` 필드를 반드시 포함한다.
- PARTIAL_ERROR/FAILED 시 UI에서 원인 메시지를 식별 가능하게 노출한다.

## Build Order
항상 아래 순서로 진행한다.
1. DB DDL 정의
2. Index / Constraint 정의
3. RLS 정책 정의
4. RPC 구현
5. Planner / Evaluator 인터페이스 구현
6. Streamlit UI 연결
7. Rate Limit / Logging / Guardrail 연결
8. E2E 테스트

## Hard Prohibitions
- 비표준 RPC 응답 금지
- 비구조화 Evaluator 응답 저장 금지
- 프로젝트 전용 프롬프트 우선순위 누락 금지
