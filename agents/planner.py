import os
from typing import Optional

from tenacity import retry, stop_after_attempt, wait_exponential

from .a2a_adk import A2AClient, extract_markdown
from .tools import DraftSaveTool


def _is_test_mode_enabled() -> bool:
    return os.environ.get("AGENT_TEST_MODE", "").strip().lower() in {"1", "true", "yes", "on"}


class PlannerAgent:
    """Planner agent implemented via A2A transport + ADK runtime."""

    def __init__(self, agent_id: str = "planner-agent", model_name: str = "gemini-3.1-pro-preview"):
        self.agent_id = agent_id
        self.model_name = model_name
        self.test_mode = _is_test_mode_enabled()
        self.a2a = None if self.test_mode else A2AClient()
        self.save_tool: Optional[DraftSaveTool] = None
        self._last_saved_md = ""
        self.skills = [
            "OUTLINE 생성",
            "FULL_DOCUMENT 생성",
            "기존 문서 REVISION",
        ]

    def _default_agent_card(self) -> dict:
        return {
            "agent_id": self.agent_id,
            "name": "Planner Subagent",
            "role": "기획서 생성/수정",
            "input_spec": "mode, user_intent, context_text, outline_md/current_md, revision_request",
            "output_spec": "Markdown 문서",
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

    def set_save_tool(self, save_tool: Optional[DraftSaveTool]) -> None:
        self.save_tool = save_tool

    def _save_if_changed(self, markdown: str, source: str) -> None:
        if not isinstance(markdown, str) or not markdown.strip():
            return
        if markdown == self._last_saved_md:
            return
        if self.save_tool is None:
            return
        if self.save_tool.save(markdown, source=source):
            self._last_saved_md = markdown

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def _execute_outline(self, user_intent: str, context_text: str) -> str:
        if self.test_mode:
            markdown = (
                "# 1. 개요\n"
                f"- 사용자 요청: {user_intent or '테스트 요청'}\n\n"
                "# 2. 문제 정의\n"
                "# 3. 목표 및 KPI\n"
                "# 4. 핵심 기능\n"
                "# 5. 사용자 플로우\n"
                "# 6. 리스크 및 대응\n"
                "# 7. 일정/우선순위\n"
            )
            self._save_if_changed(markdown, source="planner_outline")
            return markdown

        res = self.a2a.call(
            self.agent_id,
            {
                "mode": "OUTLINE",
                "user_intent": user_intent,
                "context_text": context_text,
                "model": self.model_name,
            },
        )
        markdown = extract_markdown(res)
        if not markdown:
            raise RuntimeError("planner-agent 응답에 markdown/content가 없습니다.")
        self._save_if_changed(markdown, source="planner_outline")
        return markdown

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def _execute_full_doc(self, user_intent: str, context_text: str, outline_md: str) -> str:
        if self.test_mode:
            markdown = (
                f"{outline_md}\n\n"
                "## 상세 기획 (MOCK)\n"
                f"- 사용자 의도: {user_intent or '테스트 의도'}\n"
                "- 본문은 테스트 모드에서 생성된 샘플입니다.\n"
                "- 실제 LLM 호출 없이 동작 검증을 위한 결과입니다.\n"
            )
            self._save_if_changed(markdown, source="planner_full_document")
            return markdown

        res = self.a2a.call(
            self.agent_id,
            {
                "mode": "FULL_DOCUMENT",
                "user_intent": user_intent,
                "context_text": context_text,
                "outline_md": outline_md,
                "model": self.model_name,
            },
        )
        markdown = extract_markdown(res)
        if not markdown:
            raise RuntimeError("planner-agent 응답에 markdown/content가 없습니다.")
        self._save_if_changed(markdown, source="planner_full_document")
        return markdown

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def revise(self, current_md: str, revision_request: str) -> str:
        if self.test_mode:
            base = current_md or "# 기획서 (MOCK)\n"
            markdown = (
                f"{base}\n\n"
                "## 수정 반영 내역 (MOCK)\n"
                f"- 요청: {revision_request}\n"
                "- 테스트 모드에서 mock 수정 결과를 반환했습니다.\n"
            )
            self._save_if_changed(markdown, source="planner_revise")
            return markdown

        res = self.a2a.call(
            self.agent_id,
            {
                "mode": "REVISE",
                "current_md": current_md,
                "revision_request": revision_request,
                "model": self.model_name,
            },
        )
        markdown = extract_markdown(res)
        if not markdown:
            raise RuntimeError("planner-agent 응답에 markdown/content가 없습니다.")
        self._save_if_changed(markdown, source="planner_revise")
        return markdown

    def run(self, mode: str, user_intent: str, context_text: str, outline_md: str = None) -> str:
        try:
            if mode == "OUTLINE":
                return self._execute_outline(user_intent, context_text)
            elif mode == "FULL_DOCUMENT":
                if not outline_md:
                    raise ValueError("outline_md must be provided for FULL_DOCUMENT mode.")
                return self._execute_full_doc(user_intent, context_text, outline_md)
            else:
                raise ValueError(f"Unknown mode: {mode}")
        except Exception as e:
            raise RuntimeError(f"Planner Agent Failed: {str(e)}")
