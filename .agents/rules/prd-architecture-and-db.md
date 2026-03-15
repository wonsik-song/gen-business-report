---
description: AI 기반 맞춤형 기획서 작성 및 평가 서비스의 아키텍처 및 데이터베이스 규칙
---

# PRD Architecture and DB Rules

## Mission
이 프로젝트는 사용자의 채팅 입력 또는 파일 입력을 기반으로 서비스 기획서를 생성하고, 생성된 기획서를 구조화된 기준으로 평가하는 Streamlit MVP를 구현한다.

핵심 목표:
- 4주 이내 MVP 런칭
- 기획서 생성, 평가, 버전 관리, 사용자별 평가 프롬프트 수정 지원
- 운영 가능한 DB 제약조건과 표준 응답 계약 유지

## Mandatory Tech Stack
반드시 아래 기술 스택을 따른다.
- Python
- ADK (Agent Development Kit)
- Gemini API
- Supabase (Auth, DB, Storage, RPC, RLS)
- Streamlit

금지 사항:
- FastAPI 사용 금지
- 별도 백엔드 API 서버를 기본 구조로 설계하지 않는다
- 인증/권한 처리를 앱 메모리 상태에 의존하지 않는다

## Agent Architecture Rules
반드시 아래 Agent 책임 분리를 유지한다.

### Host Agent
책임:
- 전체 요청 라우팅
- 입력 검증
- Planner / Evaluator 호출 orchestration
- 실패 처리 및 로깅 분기

금지:
- 직접 기획서 본문 생성 금지
- 직접 평가 점수 계산 금지

### Planner Agent
책임:
- user_intent, context_text, outline_md를 바탕으로 목차 또는 본문 생성
- Markdown 결과 생성

금지:
- 평가 점수 반환 금지
- DB 직접 쓰기 로직 포함 금지

### Evaluator Agent
책임:
- 문서 평가
- 구조화된 JSON 응답 반환
- 점수, 요약, 강점, 누락 항목 생성

금지:
- 문서 생성 금지
- 문서 버전 변경 금지

## Required Tables
반드시 아래 테이블을 기준 구조로 사용한다.
- public.users
- projects
- documents
- evaluation_prompts
- evaluations
- audit_logs

### public.users
필수 컬럼:
- id uuid PK, FK(auth.users.id) ON DELETE CASCADE
- email text UNIQUE NOT NULL
- created_at timestamptz DEFAULT now()

### projects
필수 컬럼:
- id uuid PK DEFAULT gen_random_uuid()
- user_id uuid FK(users.id) ON DELETE CASCADE NOT NULL
- title varchar(100) NOT NULL
- status varchar(20) DEFAULT 'DRAFT'
- created_at timestamptz DEFAULT now()
- updated_at timestamptz DEFAULT now()

status 허용값:
- DRAFT
- COMPLETED
- ARCHIVED
- ERROR

### documents
필수 컬럼:
- id uuid PK DEFAULT gen_random_uuid()
- project_id uuid FK(projects.id) ON DELETE CASCADE NOT NULL
- version_num int NOT NULL CHECK (version_num > 0)
- content_md text NOT NULL
- is_draft boolean DEFAULT true
- created_at timestamptz DEFAULT now()
- updated_at timestamptz DEFAULT now()

필수 제약:
- UNIQUE(project_id, version_num)
- 프로젝트당 draft는 최대 1개만 허용

반드시 아래 Partial Unique Index를 사용한다.
```sql
CREATE UNIQUE INDEX idx_single_draft_per_project
ON documents (project_id)
WHERE is_draft = true;
```

draft의 updated_at은 DB trigger 대신 앱 또는 RPC 레벨에서 명시적으로 갱신한다.

### evaluation_prompts
필수 컬럼:
- id uuid PK DEFAULT gen_random_uuid()
- project_id uuid FK(projects.id) ON DELETE CASCADE NULL
- user_id uuid FK(users.id) ON DELETE CASCADE NOT NULL
- custom_rules text NOT NULL
- updated_at timestamptz DEFAULT now()

필수 제약:
- 사용자별 글로벌 프롬프트는 1개
- 사용자별 프로젝트 전용 프롬프트는 프로젝트당 1개

반드시 아래 Partial Unique Index를 사용한다.
```sql
CREATE UNIQUE INDEX idx_prompt_project
ON evaluation_prompts (user_id, project_id)
WHERE project_id IS NOT NULL;

CREATE UNIQUE INDEX idx_prompt_global
ON evaluation_prompts (user_id)
WHERE project_id IS NULL;
```

### evaluations
필수 컬럼:
- id uuid PK DEFAULT gen_random_uuid()
- document_id uuid FK(documents.id) ON DELETE CASCADE NOT NULL
- status varchar(20) NOT NULL DEFAULT 'SUCCESS'
- total_score int NULL
- feedback_json jsonb NOT NULL
- prompt_tokens int DEFAULT 0
- comp_tokens int DEFAULT 0
- created_at timestamptz DEFAULT now()

status 허용값:
- SUCCESS
- PARTIAL_ERROR
- FAILED

total_score 규칙:
- 0 이상 100 이하
- FAILED 상태에서는 NULL 허용

status 정의:
- SUCCESS: 모델 호출 및 JSON 파싱 성공
- PARTIAL_ERROR: 모델 호출 성공, JSON 파싱 실패, fallback 처리
- FAILED: 모델 호출 자체 실패, 재시도 초과, 타임아웃, 정책 위반

### audit_logs
필수 컬럼:
- id uuid PK DEFAULT gen_random_uuid()
- user_id uuid FK(users.id) ON DELETE SET NULL
- severity varchar(20) DEFAULT 'INFO'
- action_type varchar(50) NOT NULL
- details jsonb NULL
- ip_address inet NULL
- created_at timestamptz DEFAULT now()

severity 허용값:
- INFO
- WARN
- ERROR
- SECURITY

## UI Rules
MVP는 Streamlit 기반으로 구성한다.

필수 화면:
- 로그인/인증 화면
- 프로젝트 목록 / 생성 화면
- 입력 화면
- OUTLINE 생성 및 승인 화면
- 문서 편집기 화면
- 평가 결과 화면
- 평가 프롬프트 관리 화면
- 버전 저장 / 히스토리 화면
- Markdown 내보내기 화면

## Non-Functional Rules
- 생성 결과와 평가 결과는 재현 가능하게 저장한다
- 사용자 데이터는 프로젝트 단위로 분리한다
- 토큰 사용량은 추후 분석 가능하게 저장한다
- 실패 상태도 반드시 기록한다
- 앱은 fallback 가능한 구조여야 한다

## Test Mode Architecture Rule
- `AGENT_TEST_MODE`가 활성화되면 Host Agent를 통해 호출되는 Planner/Evaluator는 mock 결과 반환을 허용한다.
- 테스트 모드에서도 Host → Planner/Evaluator 책임 분리 구조는 유지한다.
- 테스트 모드 응답은 운영 스키마를 준수해야 하며, UI/DB 흐름 검증에 사용 가능해야 한다.
- 운영 환경 기본값은 `AGENT_TEST_MODE=false`를 유지한다.

## Hard Prohibitions
- FastAPI 예제 생성 금지
- Agent 책임 혼합 금지
- 비구조화 평가 응답 금지
- draft 다중 허용 금지
