import asyncio
import os
from typing import Optional

from tenacity import retry, stop_after_attempt, wait_exponential

from .prompts import build_full_doc_prompt, build_outline_prompt, build_revise_prompt
from .tools import DraftSaveTool

try:
    from google.adk.agents import LlmAgent
except Exception:  # pragma: no cover
    LlmAgent = None


def _is_test_mode_enabled() -> bool:
    return os.environ.get("AGENT_TEST_MODE", "").strip().lower() in {"1", "true", "yes", "on"}


class PlannerAgent:
    """Planner agent implemented with LlmAgent only."""

    def __init__(self, agent_id: str = "planner-agent", model_name: str = "gemini-3.1-pro-preview"):
        self.agent_id = agent_id
        self.model_name = model_name
        self.test_mode = _is_test_mode_enabled()
        self.adk_enabled = LlmAgent is not None
        self.save_tool: Optional[DraftSaveTool] = None
        self._last_saved_md = ""
        self._llm_subagent = None
        self.skills = [
            "OUTLINE 생성",
            "FULL_DOCUMENT 생성",
            "기존 문서 REVISION",
        ]
        if self.adk_enabled:
            self._llm_subagent = self._build_llm_subagent()

    def _default_agent_card(self) -> dict:
        return {
            "agent_id": self.agent_id,
            "name": "Planner Subagent",
            "role": "기획서 생성/수정",
            "input_spec": "mode, user_intent, context_text, outline_md/current_md, revision_request",
            "output_spec": "Markdown 문서",
        }

    def get_agent_card(self) -> dict:
        return self._default_agent_card()

    def get_agent_skills(self) -> list:
        return list(self.skills)

    def set_save_tool(self, save_tool: Optional[DraftSaveTool]) -> None:
        self.save_tool = save_tool
        if self.adk_enabled:
            self._llm_subagent = self._build_llm_subagent()

    def get_llm_subagent(self):
        if not self.adk_enabled:
            return None
        if self._llm_subagent is None:
            self._llm_subagent = self._build_llm_subagent()
        return self._llm_subagent

    def _create_llm_agent_with_tools(
        self,
        name: str,
        model: str,
        description: str,
        instruction: str,
        tools: Optional[list] = None,
    ):
        kwargs = {
            "name": name,
            "model": model,
            "description": description,
            "instruction": instruction,
        }
        if tools:
            kwargs["tools"] = tools
        try:
            return LlmAgent(**kwargs)
        except TypeError:
            kwargs.pop("tools", None)
            return LlmAgent(**kwargs)

    def _build_llm_subagent(self):
        if not self.adk_enabled:
            return None
        tools = [self._adk_save_current_draft] if self.save_tool is not None else None
        return self._create_llm_agent_with_tools(
            name="planner_subagent",
            model=self.model_name,
            description="Generates and revises PRD markdown documents.",
            instruction=(
                "You are a planner subagent. "
                "Generate valid markdown only. "
                "For outline requests, return markdown outline. "
                "For full-document requests, return complete markdown document. "
                "For revise requests, return fully revised markdown document. "
                "If markdown is newly generated or revised, call `_adk_save_current_draft` "
                "with the full markdown."
            ),
            tools=tools,
        )

    def _adk_save_current_draft(self, content_md: str, source: str = "planner_subagent") -> str:
        if self.save_tool is None:
            return "SAVE_SKIPPED_OR_FAILED"
        ok = self.save_tool.save(content_md, source=source)
        if ok:
            self._last_saved_md = content_md
            return "SAVED"
        return "SAVE_SKIPPED_OR_FAILED"

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
                app_name="planner-subagent-runtime",
                session_service=InMemorySessionService(),
            )
            if hasattr(runner, "run_debug"):
                events = runner.run_debug(
                    [prompt],
                    user_id="planner-user",
                    session_id="planner-session",
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

    def _require_llm_runtime(self) -> None:
        if self.test_mode:
            return
        if not self.adk_enabled:
            raise RuntimeError("LlmAgent 런타임이 비활성화되어 Planner를 실행할 수 없습니다.")

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
        self._require_llm_runtime()
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

        prompt = build_outline_prompt(
            user_intent=user_intent or "",
            context_text=context_text or "",
        )
        adk_markdown = self._invoke_llm_subagent(prompt)
        if adk_markdown:
            self._save_if_changed(adk_markdown, source="planner_outline")
            return adk_markdown

        raise RuntimeError("planner_subagent 응답에 markdown 결과가 없습니다.")

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def _execute_full_doc(self, user_intent: str, context_text: str, outline_md: str) -> str:
        self._require_llm_runtime()
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

        prompt = build_full_doc_prompt(
            user_intent=user_intent or "",
            context_text=context_text or "",
            outline_md=outline_md or "",
        )
        adk_markdown = self._invoke_llm_subagent(prompt)
        if adk_markdown:
            self._save_if_changed(adk_markdown, source="planner_full_document")
            return adk_markdown

        raise RuntimeError("planner_subagent 응답에 markdown 결과가 없습니다.")

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def revise(self, current_md: str, revision_request: str) -> str:
        self._require_llm_runtime()
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

        prompt = build_revise_prompt(
            current_md=current_md or "",
            revision_request=revision_request or "",
        )
        adk_markdown = self._invoke_llm_subagent(prompt)
        if adk_markdown:
            self._save_if_changed(adk_markdown, source="planner_revise")
            return adk_markdown

        raise RuntimeError("planner_subagent 응답에 markdown 결과가 없습니다.")

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
