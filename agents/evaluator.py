import os

from .a2a_adk import A2AClient


def _is_test_mode_enabled() -> bool:
    return os.environ.get("AGENT_TEST_MODE", "").strip().lower() in {"1", "true", "yes", "on"}


class EvaluatorAgent:
    """Evaluator agent implemented via A2A transport + ADK runtime."""

    def __init__(self, agent_id: str = "evaluator-agent", model_name: str = "gemini-3.1-pro-preview"):
        self.agent_id = agent_id
        self.model_name = model_name
        self.test_mode = _is_test_mode_enabled()
        self.a2a = None if self.test_mode else A2AClient()
        self.skills = [
            "문서 품질 평가",
            "구조화된 JSON 점수 반환",
            "강점/누락 항목 분석",
        ]

    def _default_agent_card(self) -> dict:
        return {
            "agent_id": self.agent_id,
            "name": "Evaluator Subagent",
            "role": "기획서 평가",
            "input_spec": "document_md, custom_rules, rubric",
            "output_spec": "평가 JSON(status, score, summary, strengths, missing_points)",
        }

    def get_agent_card(self) -> dict:
        if self.test_mode or self.a2a is None:
            return self._default_agent_card()
        try:
            raw = self.a2a.fetch_agent_card(self.agent_id)
            card = self._default_agent_card()
            card["agent_id"] = str(raw.get("agent_id") or raw.get("id") or card["agent_id"])
            card["name"] = str(raw.get("name") or card["name"])
            card["role"] = str(raw.get("role") or raw.get("description") or card["role"])
            card["input_spec"] = str(raw.get("input_spec") or raw.get("input") or card["input_spec"])
            card["output_spec"] = str(raw.get("output_spec") or raw.get("output") or card["output_spec"])
            return card
        except Exception:
            return self._default_agent_card()

    def get_agent_skills(self) -> list:
        return list(self.skills)

    def run(self, document_md: str, custom_rules: str, rubric: str) -> dict:
        if self.test_mode:
            length = len(document_md or "")
            base_score = 96 if length > 200 else 88
            return {
                "status": "SUCCESS",
                "total_score": base_score,
                "category_scores": {
                    "logic": base_score,
                    "feasibility": base_score - 2,
                    "ux_flow": base_score - 1,
                    "business": base_score,
                },
                "summary": "테스트 모드 평가 결과입니다. 실제 LLM 호출 없이 mock 데이터를 반환했습니다.",
                "strengths": ["테스트 파이프라인이 정상 동작합니다.", "문서 구조가 안정적으로 유지됩니다."],
                "missing_points": ["실운영 모델 평가와의 차이를 검증하세요."],
                "prompt_tokens": 0,
                "comp_tokens": 0,
            }

        try:
            response = self.a2a.call(
                self.agent_id,
                {
                    "document_md": document_md,
                    "custom_rules": custom_rules,
                    "rubric": rubric,
                    "model": self.model_name,
                },
            )
            # Normalize common wrapper shapes for evaluator payload.
            if isinstance(response.get("evaluation"), dict):
                response = response["evaluation"]
            if isinstance(response.get("output"), dict):
                response = response["output"]
            return response
        except Exception as e:
            return {
                "status": "FAILED",
                "total_score": None,
                "category_scores": None,
                "summary": f"모델 호출 자체에 실패했습니다: {str(e)}",
                "strengths": [],
                "missing_points": [],
                "prompt_tokens": 0,
                "comp_tokens": 0,
            }
