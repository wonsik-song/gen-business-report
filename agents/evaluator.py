import os
import json
import asyncio

from .prompts import build_evaluator_prompt

try:
    from google.adk.agents import LlmAgent
except Exception:  # pragma: no cover
    LlmAgent = None


def _is_test_mode_enabled() -> bool:
    return os.environ.get("AGENT_TEST_MODE", "").strip().lower() in {"1", "true", "yes", "on"}


class EvaluatorAgent:
    """Evaluator agent implemented with LlmAgent only."""

    def __init__(self, agent_id: str = "evaluator-agent", model_name: str = "gemini-3.1-pro-preview"):
        self.agent_id = agent_id
        self.model_name = model_name
        self.test_mode = _is_test_mode_enabled()
        self.adk_enabled = LlmAgent is not None
        self._llm_subagent = None
        self.skills = [
            "문서 품질 평가",
            "구조화된 JSON 점수 반환",
            "강점/누락 항목 분석",
        ]
        if self.adk_enabled:
            self._llm_subagent = self._build_llm_subagent()

    def _default_agent_card(self) -> dict:
        return {
            "agent_id": self.agent_id,
            "name": "Evaluator Subagent",
            "role": "기획서 평가",
            "input_spec": "document_md, custom_rules, rubric",
            "output_spec": "평가 JSON(status, score, summary, strengths, missing_points)",
        }

    def get_agent_card(self) -> dict:
        return self._default_agent_card()

    def get_agent_skills(self) -> list:
        return list(self.skills)

    def get_llm_subagent(self):
        if not self.adk_enabled:
            return None
        if self._llm_subagent is None:
            self._llm_subagent = self._build_llm_subagent()
        return self._llm_subagent

    def _create_llm_agent(self, name: str, model: str, description: str, instruction: str):
        kwargs = {
            "name": name,
            "model": model,
            "description": description,
            "instruction": instruction,
        }
        return LlmAgent(**kwargs)

    def _build_llm_subagent(self):
        if not self.adk_enabled:
            return None
        return self._create_llm_agent(
            name="evaluator_subagent",
            model=self.model_name,
            description="Evaluates PRD markdown and returns structured JSON result.",
            instruction=(
                "You are an evaluator subagent. "
                "Evaluate the given PRD and return only one JSON object with "
                "status, total_score, category_scores, summary, strengths, missing_points, raw_text."
            ),
        )

    def _extract_event_text(self, event: object) -> str:
        content = getattr(event, "content", None)
        parts = getattr(content, "parts", None) if content is not None else None
        if not parts:
            return ""
        text_parts = []
        for part in parts:
            part_text = getattr(part, "text", None)
            if isinstance(part_text, str) and part_text.strip():
                text_parts.append(part_text.strip())
            function_call = getattr(part, "function_call", None)
            if function_call is not None:
                args = getattr(function_call, "args", None)
                if isinstance(args, dict):
                    text_parts.append(json.dumps(args, ensure_ascii=False))
                elif isinstance(args, str) and args.strip():
                    text_parts.append(args.strip())
        return "\n".join(text_parts).strip()

    async def _collect_async_events(self, async_iterable) -> list:
        events = []
        async for event in async_iterable:
            events.append(event)
        return events

    def _run_coroutine_blocking(self, coroutine_obj):
        try:
            return asyncio.run(coroutine_obj)
        except RuntimeError:
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(coroutine_obj)
            finally:
                loop.close()

    def _invoke_llm_subagent(self, prompt: str) -> str:
        llm_subagent = self.get_llm_subagent()
        if llm_subagent is None:
            return ""
        try:
            from google.adk.runners import Runner  # type: ignore
            from google.adk.sessions.in_memory_session_service import InMemorySessionService  # type: ignore
        except Exception:
            return ""

        try:
            runner = Runner(
                agent=llm_subagent,
                app_name="evaluator-subagent-runtime",
                session_service=InMemorySessionService(),
            )
            if hasattr(runner, "run_debug"):
                events = runner.run_debug(
                    [prompt],
                    user_id="evaluator-user",
                    session_id="evaluator-session",
                    quiet=True,
                )
                if asyncio.iscoroutine(events):
                    events = self._run_coroutine_blocking(events)
                if hasattr(events, "__aiter__"):
                    events = self._run_coroutine_blocking(self._collect_async_events(events))
                if isinstance(events, list):
                    texts = [self._extract_event_text(event) for event in events]
                    texts = [text for text in texts if isinstance(text, str) and text.strip()]
                    if texts:
                        return texts[-1].strip()

            result = None
            if hasattr(runner, "run"):
                try:
                    result = runner.run(prompt)
                except TypeError:
                    result = None
            elif hasattr(runner, "invoke"):
                result = runner.invoke(prompt)
            elif hasattr(runner, "call"):
                result = runner.call(prompt)

            if isinstance(result, str) and result.strip():
                return result.strip()
            if result is not None:
                content = getattr(result, "content", None)
                if isinstance(content, str) and content.strip():
                    return content.strip()
                parts = getattr(content, "parts", None) if content is not None else None
                if parts:
                    text_parts = [
                        getattr(part, "text", "").strip()
                        for part in parts
                        if isinstance(getattr(part, "text", None), str) and getattr(part, "text", "").strip()
                    ]
                    if text_parts:
                        return "\n".join(text_parts).strip()
        except Exception:
            return ""
        return ""

    def _safe_parse_json(self, text: str):
        cleaned = (text or "").strip()
        if not cleaned:
            return None
        try:
            return json.loads(cleaned)
        except Exception:
            start = cleaned.find("{")
            end = cleaned.rfind("}")
            if start >= 0 and end > start:
                candidate = cleaned[start : end + 1]
                try:
                    return json.loads(candidate)
                except Exception:
                    return None
        return None

    def _require_llm_runtime(self) -> None:
        if self.test_mode:
            return
        if not self.adk_enabled:
            raise RuntimeError("LlmAgent 런타임이 비활성화되어 Evaluator를 실행할 수 없습니다.")

    def run(self, document_md: str, custom_rules: str, rubric: str) -> dict:
        self._require_llm_runtime()
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

        prompt = build_evaluator_prompt(
            document_md=document_md or "",
            custom_rules=custom_rules or "",
            rubric=rubric or "",
        )
        adk_text = self._invoke_llm_subagent(prompt)
        parsed = self._safe_parse_json(adk_text)
        if isinstance(parsed, dict):
            if isinstance(parsed.get("evaluation"), dict):
                parsed = parsed["evaluation"]
            if isinstance(parsed.get("output"), dict):
                parsed = parsed["output"]
            return parsed

        return {
            "status": "FAILED",
            "total_score": None,
            "category_scores": None,
            "summary": "Evaluator LlmAgent 응답을 JSON으로 파싱하지 못했습니다.",
            "strengths": [],
            "missing_points": [],
            "prompt_tokens": 0,
            "comp_tokens": 0,
        }
