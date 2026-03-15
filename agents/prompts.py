PLANNER_OUTLINE_SYSTEM = (
    "당신은 전문 프로덕트 매니저입니다. "
    "서비스 기획서(PRD)의 상세 목차를 작성하는 것이 임무입니다. "
    "반드시 한국어로 작성하세요. "
    "유효한 Markdown 형식의 목차만 출력하세요. 인사말이나 대화체 텍스트는 포함하지 마세요."
)

PLANNER_FULL_DOC_SYSTEM = (
    "당신은 전문 프로덕트 매니저입니다. "
    "승인된 목차를 바탕으로 포괄적이고 완전한 서비스 기획서(PRD)를 작성하는 것이 임무입니다. "
    "반드시 한국어로 작성하세요. "
    "최종 Markdown 텍스트만 출력하세요. 인사말이나 대화체 텍스트는 포함하지 마세요."
)

PLANNER_REVISE_SYSTEM = (
    "당신은 전문 프로덕트 매니저입니다. 사용자가 이미 작성된 기획서 문서를 가지고 있습니다. "
    "사용자의 수정 요청을 반영하여 전체 업데이트된 Markdown 문서를 반환하세요. "
    "반드시 한국어로 작성하세요. "
    "수정된 전체 Markdown만 출력하세요. 대화체 텍스트는 포함하지 마세요."
)

EVALUATOR_SYSTEM = (
    "당신은 전문 평가자입니다. 제공된 기획서를 평가 기준과 커스텀 규칙에 따라 평가하는 것이 임무입니다. "
    "반드시 한국어로 평가 내용을 작성하세요. "
    "반환 형식은 JSON 한 개만 출력하세요."
)

HOST_ORCHESTRATION_SYSTEM = (
    "당신은 Host Coordinator입니다. 사용자 의도를 파악하고 Planner/Evaluator 하위 에이전트를 "
    "상태 기반으로 오케스트레이션하세요. 반드시 한국어로 응답하고, 단계 전이를 명확히 유지하세요. "
    "기본 흐름은 다음과 같습니다: 요청 수집 -> 문서 생성/수정 -> 평가 -> 완료 판정 -> 사용자 컨펌 -> 저장/확정. "
    "평가 점수와 누락 포인트를 바탕으로 다음 단계를 결정하고, 사용자의 최신 요청을 항상 우선 반영하세요. "
    "문서가 생성/수정되어 내용이 바뀌면 저장 툴을 사용해 초안을 저장하세요. "
    "사용자가 명시적으로 컨펌한 경우에만 확정 툴을 사용해 버전을 확정하세요. "
    "컨펌 전에는 확정을 시도하지 마세요."
)

_PROMPT_KEYS = {
    "host_orchestration_system": "HOST_ORCHESTRATION_SYSTEM",
    "planner_outline_system": "PLANNER_OUTLINE_SYSTEM",
    "planner_full_doc_system": "PLANNER_FULL_DOC_SYSTEM",
    "planner_revise_system": "PLANNER_REVISE_SYSTEM",
    "evaluator_system": "EVALUATOR_SYSTEM",
}

_DEFAULT_PROMPTS = {
    "host_orchestration_system": HOST_ORCHESTRATION_SYSTEM,
    "planner_outline_system": PLANNER_OUTLINE_SYSTEM,
    "planner_full_doc_system": PLANNER_FULL_DOC_SYSTEM,
    "planner_revise_system": PLANNER_REVISE_SYSTEM,
    "evaluator_system": EVALUATOR_SYSTEM,
}


def get_agent_prompt_templates() -> dict:
    return {
        "host_orchestration_system": HOST_ORCHESTRATION_SYSTEM,
        "planner_outline_system": PLANNER_OUTLINE_SYSTEM,
        "planner_full_doc_system": PLANNER_FULL_DOC_SYSTEM,
        "planner_revise_system": PLANNER_REVISE_SYSTEM,
        "evaluator_system": EVALUATOR_SYSTEM,
    }


def apply_agent_prompt_templates(overrides: dict) -> dict:
    if not isinstance(overrides, dict):
        return get_agent_prompt_templates()

    for key, var_name in _PROMPT_KEYS.items():
        value = overrides.get(key)
        if isinstance(value, str) and value.strip():
            globals()[var_name] = value.strip()
    return get_agent_prompt_templates()


def reset_agent_prompt_templates() -> dict:
    for key, value in _DEFAULT_PROMPTS.items():
        var_name = _PROMPT_KEYS[key]
        globals()[var_name] = value
    return get_agent_prompt_templates()


def build_outline_prompt(user_intent: str, context_text: str) -> str:
    return (
        f"{PLANNER_OUTLINE_SYSTEM}\n\n"
        f"사용자 의도: {user_intent}\n\n"
        f"추가 맥락:\n{context_text}\n\n"
        "구조화된 Markdown 목차를 한국어로 생성해주세요."
    )


def build_full_doc_prompt(user_intent: str, context_text: str, outline_md: str) -> str:
    return (
        f"{PLANNER_FULL_DOC_SYSTEM}\n\n"
        f"사용자 의도: {user_intent}\n\n"
        f"추가 맥락:\n{context_text}\n\n"
        f"승인된 목차:\n{outline_md}\n\n"
        "위 목차를 기반으로 상세한 Markdown 기획서를 한국어로 작성해주세요."
    )


def build_revise_prompt(current_md: str, revision_request: str) -> str:
    return (
        f"{PLANNER_REVISE_SYSTEM}\n\n"
        f"현재 문서:\n{current_md}\n\n"
        f"수정 요청:\n{revision_request}\n\n"
        "수정된 전체 Markdown 문서를 한국어로 반환해주세요."
    )


def build_evaluator_prompt(document_md: str, custom_rules: str, rubric: str) -> str:
    schema = (
        '{'
        '"status":"SUCCESS|PARTIAL_ERROR|FAILED",'
        '"total_score":0,'
        '"category_scores":{"logic":0,"feasibility":0,"ux_flow":0,"business":0},'
        '"summary":"",'
        '"strengths":[""],'
        '"missing_points":[""],'
        '"raw_text":""'
        "}"
    )
    return (
        f"{EVALUATOR_SYSTEM}\n\n"
        f"커스텀 규칙:\n{custom_rules}\n\n"
        f"평가 기준(Rubric):\n{rubric}\n\n"
        f"평가할 문서:\n{document_md}\n\n"
        "아래 JSON 스키마를 반드시 지켜서 응답하세요. "
        "JSON 이외 텍스트는 출력하지 마세요.\n"
        f"{schema}"
    )
