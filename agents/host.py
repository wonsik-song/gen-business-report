import json
import asyncio
import time
import warnings
from typing import Callable, Dict, Optional

from .prompts import HOST_ORCHESTRATION_SYSTEM
from .evaluator import EvaluatorAgent
from .planner import PlannerAgent
from .tools import DraftSaveTool

try:
    # Optional ADK integration.
    from google.adk.agents import LlmAgent
except Exception:  # pragma: no cover
    LlmAgent = None

warnings.filterwarnings(
    "ignore",
    message=r".*non-text parts in the response.*",
)
warnings.filterwarnings(
    "ignore",
    message=r".*MALFORMED_RESPONSE is not a valid FinishReason.*",
)


class HostAgent:
    """
    Host agent that orchestrates planner/evaluator subagents.
    """

    def __init__(
        self,
        planner_model: str = "gemini-3.1-pro-preview",
        evaluator_model: str = "gemini-3.1-pro-preview",
        host_model: str = "gemini-3.1-pro-preview",
    ):
        self.planner_model = planner_model
        self.evaluator_model = evaluator_model
        self.host_model = host_model
        self.save_tool = DraftSaveTool()
        self.planner_agent = PlannerAgent(model_name=planner_model)
        self.planner_agent.set_save_tool(self.save_tool)
        self.evaluator_agent = EvaluatorAgent(model_name=evaluator_model)
        self.adk_enabled = LlmAgent is not None
        self._adk_host_agent = None
        self._adk_planner_agent = None
        self._adk_evaluator_agent = None
        if self.adk_enabled:
            self._build_adk_topology()

    def _adk_save_current_draft(self, content_md: str, source: str = "adk_planner") -> str:
        """
        Save the latest markdown draft for the current project.
        Call this tool after generating or revising document content.
        """
        ok = self.save_tool.save(content_md, source=source)
        if ok:
            return "SAVED"
        return "SAVE_SKIPPED_OR_FAILED"

    def _adk_finalize_current_version(self, source: str = "adk_host") -> str:
        """
        Finalize current project draft as a fixed version.
        Use this tool only after user confirmation.
        """
        ok = self.save_tool.finalize_version(source=source)
        if ok:
            return "FINALIZED"
        return "FINALIZE_SKIPPED_OR_FAILED"

    def _adk_load_current_draft(self) -> Dict[str, object]:
        """
        Load current draft markdown for the active project.
        """
        return self.save_tool.load_current_draft()

    def _adk_load_finalized_document(self, version_num: Optional[int] = None) -> Dict[str, object]:
        """
        Load finalized markdown by version number.
        If version_num is omitted, load the latest finalized version.
        """
        return self.save_tool.load_finalized_document(version_num=version_num)

    def _create_llm_agent_with_tools(
        self,
        name: str,
        model: str,
        description: str,
        instruction: str,
        tools: Optional[list] = None,
        sub_agents: Optional[list] = None,
    ):
        kwargs = {
            "name": name,
            "model": model,
            "description": description,
            "instruction": instruction,
        }
        if sub_agents is not None:
            kwargs["sub_agents"] = sub_agents
        if tools:
            kwargs["tools"] = tools
        try:
            return LlmAgent(**kwargs)
        except TypeError:
            # Fallback for older ADK signatures.
            kwargs.pop("tools", None)
            return LlmAgent(**kwargs)

    def configure_document_context(self, project_id: Optional[str], user_id: Optional[str] = None) -> None:
        self.save_tool.configure(project_id=project_id, user_id=user_id)

    def load_current_draft(self) -> Dict[str, object]:
        return self.save_tool.load_current_draft()

    def load_finalized_document(self, version_num: Optional[int] = None) -> Dict[str, object]:
        return self.save_tool.load_finalized_document(version_num=version_num)

    def load_document_with_fallback(
        self,
        target: str = "finalized",
        version_num: Optional[int] = None,
    ) -> Dict[str, object]:
        normalized_target = (target or "finalized").lower()

        if normalized_target == "draft":
            ordered_loaders = [
                lambda: self.load_current_draft(),
                lambda: self.load_finalized_document(version_num=version_num),
            ]
        else:
            ordered_loaders = [
                lambda: self.load_finalized_document(version_num=version_num),
                lambda: self.load_current_draft(),
            ]

        last_error = {"status": "NOT_FOUND", "reason": "DOCUMENT_NOT_FOUND"}
        for loader in ordered_loaders:
            payload = loader()
            if str(payload.get("status") or "") == "SUCCESS":
                return payload
            last_error = payload
        return last_error

    def finalize_current_version(self, source: str = "host_chat_intent") -> bool:
        return self.save_tool.finalize_version(source=source)

    def orchestrate_chat(
        self,
        user_input: str,
        phase: str,
        current_md: str,
        latest_eval: Optional[Dict[str, object]] = None,
        rubric: str = "",
        progress_callback: Optional[Callable[[str], None]] = None,
    ) -> Dict[str, object]:
        eval_snapshot = latest_eval if isinstance(latest_eval, dict) else {}
        adk_res = self._try_adk_invoke(
            task_type="chat_orchestrate",
            payload={
                "user_input": user_input,
                "phase": phase,
                "current_md": current_md or "",
                "latest_eval": eval_snapshot,
                "rubric": rubric or "",
                "decision_schema": {
                    "selected_subagent": "planner_subagent|evaluator_subagent|none",
                    "selected_tool": (
                        "_adk_load_current_draft|_adk_load_finalized_document|"
                        "_adk_finalize_current_version|empty"
                    ),
                    "tool_args": {},
                    "planner_mode": "OUTLINE|FULL_DOCUMENT|REVISE|empty",
                    "needs_clarification": False,
                    "message": "assistant message",
                },
                "execution_rule": (
                    "Decide by selecting selected_subagent and selected_tool. "
                    "Do not return action field. "
                    "Use planner_subagent for outline/full_document/revise and set planner_mode. "
                    "Use evaluator_subagent when evaluation should run. "
                    "Use host tool selection only for load/finalize. "
                    "If only explanation/summary is needed, choose selected_subagent=none and provide message. "
                    "Return JSON only."
                ),
            },
            max_attempts=3,
            progress_callback=progress_callback,
        )
        if not isinstance(adk_res, dict):
            return {"handled": False}

        selected_subagent = str(adk_res.get("selected_subagent") or "none").strip().lower()
        selected_tool = str(adk_res.get("selected_tool") or "").strip()
        planner_mode = str(adk_res.get("planner_mode") or "").strip().upper()
        message = str(adk_res.get("message") or "").strip()
        needs_clarification = bool(adk_res.get("needs_clarification"))
        tool_args = adk_res.get("tool_args") if isinstance(adk_res.get("tool_args"), dict) else {}

        # Normalize common sentinel values emitted by models.
        if selected_subagent in {"", "empty", "null"}:
            selected_subagent = "none"
        if selected_tool.lower() in {"empty", "none", "null"}:
            selected_tool = ""
        if planner_mode in {"EMPTY", "NONE", "NULL"}:
            planner_mode = ""

        valid_subagents = {"planner_subagent", "evaluator_subagent", "none"}
        valid_tools = {
            "",
            "_adk_load_current_draft",
            "_adk_load_finalized_document",
            "_adk_finalize_current_version",
        }
        valid_planner_modes = {"", "OUTLINE", "FULL_DOCUMENT", "REVISE"}

        if selected_subagent not in valid_subagents:
            return {"handled": False}
        if selected_tool not in valid_tools:
            return {"handled": False}
        if planner_mode not in valid_planner_modes:
            return {"handled": False}

        return {
            "handled": True,
            "selected_subagent": selected_subagent,
            "selected_tool": selected_tool,
            "planner_mode": planner_mode,
            "tool_args": tool_args,
            "needs_clarification": needs_clarification,
            "message": message,
        }

    def _build_eval_result_message(self, latest_eval: Dict[str, object]) -> str:
        status = str(latest_eval.get("status") or "")
        total_score = latest_eval.get("total_score")
        summary = str(latest_eval.get("summary") or "").strip()
        category_scores = latest_eval.get("category_scores") or {}
        if not isinstance(category_scores, dict):
            category_scores = {}
        missing_points = latest_eval.get("missing_points") or []
        if not isinstance(missing_points, list):
            missing_points = []

        header = "최근 평가 결과를 기준으로 안내드릴게요."
        if status == "SUCCESS":
            score_text = f"{total_score}/100" if isinstance(total_score, (int, float)) else "점수 미확인"
            lines = [header, f"- 종합 점수: {score_text}"]
            if category_scores:
                logic = category_scores.get("logic", "?")
                feasibility = category_scores.get("feasibility", "?")
                ux_flow = category_scores.get("ux_flow", "?")
                business = category_scores.get("business", "?")
                lines.append(
                    f"- 카테고리 점수: 논리성 {logic}, 실현성 {feasibility}, UX {ux_flow}, 비즈니스 {business}"
                )
            if summary:
                lines.append(f"- 요약: {summary}")
            if missing_points:
                preview = ", ".join(str(item) for item in missing_points[:3] if str(item).strip())
                if preview:
                    lines.append(f"- 주요 보완 포인트: {preview}")
            return "\n".join(lines)

        if status == "PARTIAL_ERROR":
            return (
                f"{header}\n- 상태: PARTIAL_ERROR\n"
                f"- 요약: {summary or '평가 구조화 중 일부 오류가 있었습니다.'}"
            )
        return (
            f"{header}\n- 상태: {status or 'FAILED'}\n"
            f"- 요약: {summary or '평가가 정상 완료되지 않았습니다.'}"
        )

    def _build_document_summary_message(self, current_md: str, latest_eval: Optional[Dict[str, object]] = None) -> str:
        text = (current_md or "").strip()
        if not text:
            return "현재 요약할 문서가 없습니다. 먼저 기획서를 생성하거나 불러와 주세요."

        lines = [line.strip() for line in text.splitlines() if line.strip()]
        headings = [line.lstrip("#").strip() for line in lines if line.startswith("#")]
        bullets = [line.lstrip("-* ").strip() for line in lines if line.startswith(("-", "*"))]
        preview_headings = headings[:5]
        preview_bullets = bullets[:5]

        result_lines = ["현재 Planner 결과(문서)를 Host가 요약해드릴게요."]
        if preview_headings:
            result_lines.append("- 주요 섹션: " + ", ".join(preview_headings))
        if preview_bullets:
            result_lines.append("- 핵심 포인트: " + "; ".join(preview_bullets))
        if not preview_headings and not preview_bullets:
            snippet = " ".join(lines[:3])[:220]
            if snippet:
                result_lines.append(f"- 요약: {snippet}")

        if isinstance(latest_eval, dict) and latest_eval:
            score = latest_eval.get("total_score")
            status = latest_eval.get("status")
            if isinstance(score, (int, float)):
                result_lines.append(f"- 참고: 최신 평가 점수 {score}/100 (상태: {status})")
        else:
            result_lines.append("- 참고: 최신 평가 결과는 없어 문서 내용 기준으로만 요약했습니다.")
        return "\n".join(result_lines)

    def _build_adk_topology(self) -> None:
        """Build ADK-style hierarchy: host coordinator -> planner/evaluator sub_agents."""
        self._adk_planner_agent = self.planner_agent.get_llm_subagent()
        self._adk_evaluator_agent = self.evaluator_agent.get_llm_subagent()
        self._adk_host_agent = self._create_llm_agent_with_tools(
            name="host_coordinator",
            model=self.host_model,
            description="Coordinates planner and evaluator subagents.",
            instruction=(
                f"{HOST_ORCHESTRATION_SYSTEM} "
                "When the user explicitly confirms completion, finalize with "
                "`_adk_finalize_current_version`. "
                "When context is needed, load documents with `_adk_load_current_draft` "
                "or `_adk_load_finalized_document`. "
                "For planning and evaluation, delegate to planner_subagent/evaluator_subagent. "
                "Use host tools only for save/load/finalize. Return only the response JSON "
                "defined by the orchestration response schema."
            ),
            tools=[
                self._adk_save_current_draft,
                self._adk_finalize_current_version,
                self._adk_load_current_draft,
                self._adk_load_finalized_document,
            ],
            sub_agents=[
                agent
                for agent in [self._adk_planner_agent, self._adk_evaluator_agent]
                if agent is not None
            ],
        )

    def get_agent_cards(self) -> Dict[str, Dict[str, str]]:
        cards = {
            "planner": self.planner_agent.get_agent_card(),
            "evaluator": self.evaluator_agent.get_agent_card(),
            "host": {
                "agent_id": "host-coordinator",
                "name": "Host Coordinator",
                "role": "요청 의도 확인, 상태 기반 단계 전이, Planner/Evaluator 오케스트레이션",
                "input_spec": (
                    "user_input, phase, current_doc(optional), latest_eval(optional), "
                    "completion_status(optional), user_confirmation(optional)"
                ),
                "output_spec": (
                    "next_action(REQUEST_INPUT|REQUEST_EVALUATION|REQUEST_REVISION|REQUEST_CONFIRM), "
                    "stepwise status, subagent result, tool usage(save/finalize/load)"
                ),
            },
        }
        return cards

    def get_tool_cards(self) -> Dict[str, Dict[str, str]]:
        return {
            "_adk_save_current_draft": {
                "name": "_adk_save_current_draft",
                "purpose": "현재 문서를 draft로 저장",
                "input_spec": "content_md, source(optional)",
                "output_spec": "SAVED | SAVE_SKIPPED_OR_FAILED",
            },
            "_adk_finalize_current_version": {
                "name": "_adk_finalize_current_version",
                "purpose": "현재 draft를 확정 버전으로 저장",
                "input_spec": "source(optional)",
                "output_spec": "FINALIZED | FINALIZE_SKIPPED_OR_FAILED",
            },
            "_adk_load_current_draft": {
                "name": "_adk_load_current_draft",
                "purpose": "현재 프로젝트의 draft 문서 로드",
                "input_spec": "none",
                "output_spec": "status, document_id, version_num, content_md",
            },
            "_adk_load_finalized_document": {
                "name": "_adk_load_finalized_document",
                "purpose": "확정 버전 문서 로드(버전 지정 가능)",
                "input_spec": "version_num(optional)",
                "output_spec": "status, document_id, version_num, content_md",
            },
        }

    def get_agent_skills(self) -> Dict[str, list]:
        skills = {
            "planner": self.planner_agent.get_agent_skills(),
            "evaluator": self.evaluator_agent.get_agent_skills(),
        }
        if self.adk_enabled:
            skills["host"] = [
                "사용자 의도 확인 질문",
                "Planner/Evaluator 단계 오케스트레이션",
                "완료 상태 판정 및 컨펌 게이팅",
                "문서 변경 시 저장 툴 호출",
                "사용자 컨펌 후 확정 툴 호출",
                "평가 결과 기반 다음 단계 결정",
                "현재 초안/확정본 문서 로드",
            ]
        return skills

    def get_adk_topology(self) -> Dict[str, object]:
        return {
            "enabled": self.adk_enabled,
            "host": "host_coordinator" if self.adk_enabled else None,
            "sub_agents": ["planner_subagent", "evaluator_subagent"] if self.adk_enabled else [],
        }

    def get_intent_question(self, user_input: str, phase: str) -> Optional[str]:
        text = (user_input or "").strip()
        if len(text) < 6:
            return "요청 의도를 조금 더 구체적으로 알려주세요. (예: 어떤 사용자/기능/목표를 보완할지)"
        if phase == "IDLE" and "기획서" not in text and "서비스" not in text and "prd" not in text.lower():
            return "어떤 서비스 기획서를 만들지 한 줄로 알려주세요. (예: AI 기반 학습 앱 기획서)"
        return None

    def _try_adk_invoke(
        self,
        task_type: str,
        payload: Dict[str, object],
        max_attempts: int = 1,
        progress_callback: Optional[Callable[[str], None]] = None,
    ) -> Optional[Dict[str, object]]:
        """
        Try ADK host invocation. If runtime/API shape is unavailable, return None
        and let fallback path run via existing subagent wrappers.
        """
        if not self.adk_enabled or self._adk_host_agent is None:
            return None

        request_obj = {"task_type": task_type, **payload}
        prompt = (
            "Handle this orchestration request and return JSON only.\n"
            f"{json.dumps(request_obj, ensure_ascii=False)}"
        )
        attempts = max(1, int(max_attempts))
        for idx in range(attempts):
            try:
                # Runtime API can vary by ADK versions.
                from google.adk.runners import Runner  # type: ignore
                from google.adk.sessions.in_memory_session_service import (  # type: ignore
                    InMemorySessionService,
                )

                runner = Runner(
                    agent=self._adk_host_agent,
                    app_name="host-adk-runtime",
                    session_service=InMemorySessionService(),
                )

                # New ADK API path.
                if hasattr(runner, "run_debug"):
                    events = runner.run_debug(
                        [prompt],
                        user_id="host-user",
                        session_id="host-session",
                        quiet=True,
                    )
                    if asyncio.iscoroutine(events):
                        events = self._run_coroutine_blocking(events)
                    if hasattr(events, "__aiter__"):
                        events = self._run_coroutine_blocking(self._collect_async_events(events))
                    if isinstance(events, list):
                        latest_parsed = None
                        for event in events:
                            if progress_callback is not None:
                                self._emit_adk_progress(event, progress_callback)
                            parsed = self._extract_adk_response(event)
                            if isinstance(parsed, dict):
                                latest_parsed = parsed
                        if isinstance(latest_parsed, dict):
                            return latest_parsed

                # Legacy API compatibility path.
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
                parsed = self._extract_adk_response(result)
                if isinstance(parsed, dict):
                    return parsed
            except Exception:
                pass
            if idx < attempts - 1:
                time.sleep(0.2 * (idx + 1))
        return None

    async def _collect_async_events(self, async_iterable) -> list:
        events = []
        async for event in async_iterable:
            events.append(event)
        return events

    def _run_coroutine_blocking(self, coroutine_obj):
        try:
            return asyncio.run(coroutine_obj)
        except RuntimeError:
            # Fallback when an event loop is already active in runtime.
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(coroutine_obj)
            finally:
                loop.close()

    def _emit_adk_progress(self, event: object, callback: Callable[[str], None]) -> None:
        content = getattr(event, "content", None)
        parts = getattr(content, "parts", None) if content is not None else None
        if not parts:
            return
        for part in parts:
            function_call = getattr(part, "function_call", None)
            if function_call is not None:
                name = str(getattr(function_call, "name", "") or "").strip()
                if name:
                    callback(f"tool 호출: `{name}`")
                continue

            function_response = getattr(part, "function_response", None)
            if function_response is not None:
                name = str(getattr(function_response, "name", "") or "").strip()
                response = getattr(function_response, "response", None)
                if isinstance(response, dict):
                    status = str(response.get("status") or "").strip()
                    if name and status:
                        callback(f"tool 응답: `{name}` ({status})")
                    elif name:
                        callback(f"tool 응답: `{name}`")
                    elif status:
                        callback(f"tool 응답 상태: {status}")
                elif name:
                    callback(f"tool 응답: `{name}`")
                continue

            part_text = getattr(part, "text", None)
            if isinstance(part_text, str):
                text = part_text.strip()
                if not text:
                    continue
                if text.startswith("{") and text.endswith("}"):
                    continue
                preview = text[:140]
                callback(preview)

    def _extract_adk_response(self, result: object) -> Optional[Dict[str, object]]:
        if result is None:
            return None
        if isinstance(result, dict):
            return result
        if isinstance(result, str):
            text = result.strip()
        else:
            text = None
            content = getattr(result, "content", None)
            parts = getattr(content, "parts", None) if content is not None else None
            if parts:
                text_parts = []
                function_parts = []
                for part in parts:
                    part_text = getattr(part, "text", None)
                    if isinstance(part_text, str) and part_text.strip():
                        text_parts.append(part_text.strip())
                    function_call = getattr(part, "function_call", None)
                    if function_call is not None:
                        args = getattr(function_call, "args", None)
                        if isinstance(args, dict):
                            function_parts.append(json.dumps(args, ensure_ascii=False))
                        elif isinstance(args, str) and args.strip():
                            function_parts.append(args.strip())
                if function_parts:
                    text = "\n".join(function_parts).strip()
                elif text_parts:
                    text = "\n".join(text_parts).strip()
            if text is None:
                fallback_content = getattr(result, "content", None)
                text = fallback_content if isinstance(fallback_content, str) else None
        if isinstance(text, str):
            text = text.strip()
            try:
                return json.loads(text)
            except Exception:
                start = text.find("{")
                end = text.rfind("}")
                if start >= 0 and end > start:
                    try:
                        return json.loads(text[start : end + 1])
                    except Exception:
                        return None
        return None

    def plan(self, mode: str, user_intent: str, context_text: str, outline_md: str = None) -> str:
        adk_res = self._try_adk_invoke(
            task_type="plan",
            payload={
                "mode": mode,
                "user_intent": user_intent,
                "context_text": context_text,
                "outline_md": outline_md,
            },
        )
        if isinstance(adk_res, dict):
            adk_markdown = adk_res.get("markdown") or adk_res.get("content_md") or adk_res.get("text")
            if isinstance(adk_markdown, str) and adk_markdown.strip():
                self.save_tool.save(adk_markdown, source="host_adk_plan")
                return adk_markdown

        return self.planner_agent.run(
            mode=mode,
            user_intent=user_intent,
            context_text=context_text,
            outline_md=outline_md,
        )

    def revise(self, current_md: str, revision_request: str) -> str:
        adk_res = self._try_adk_invoke(
            task_type="revise",
            payload={
                "current_md": current_md,
                "revision_request": revision_request,
            },
        )
        if isinstance(adk_res, dict):
            adk_markdown = adk_res.get("markdown") or adk_res.get("content_md") or adk_res.get("text")
            if isinstance(adk_markdown, str) and adk_markdown.strip():
                self.save_tool.save(adk_markdown, source="host_adk_revise")
                return adk_markdown

        return self.planner_agent.revise(
            current_md=current_md,
            revision_request=revision_request,
        )

    def evaluate(self, document_md: str, custom_rules: str, rubric: str) -> dict:
        adk_res = self._try_adk_invoke(
            task_type="evaluate",
            payload={
                "document_md": document_md,
                "custom_rules": custom_rules,
                "rubric": rubric,
            },
        )
        if isinstance(adk_res, dict):
            # Preserve existing evaluator schema contract.
            if "evaluation" in adk_res and isinstance(adk_res["evaluation"], dict):
                return adk_res["evaluation"]
            if "status" in adk_res and "summary" in adk_res:
                return adk_res

        return self.evaluator_agent.run(
            document_md=document_md,
            custom_rules=custom_rules,
            rubric=rubric,
        )

    def _normalize_evaluation_result(self, evaluation: Dict[str, object]) -> Dict[str, object]:
        out = dict(evaluation or {})
        raw_status = str(out.get("status") or "").strip().upper()
        score = out.get("total_score")

        if raw_status in {"SUCCESS", "PARTIAL_ERROR", "FAILED"}:
            normalized = raw_status
        elif "PARTIAL" in raw_status:
            normalized = "PARTIAL_ERROR"
        elif any(token in raw_status for token in ["FAIL", "ERROR"]):
            normalized = "FAILED"
        elif any(token in raw_status for token in ["SUCCESS", "PASS", "APPROVED", "OK", "DONE"]):
            normalized = "SUCCESS"
        elif isinstance(score, (int, float)):
            normalized = "SUCCESS"
        else:
            normalized = "FAILED"

        def _to_int(value):
            if isinstance(value, bool):
                return None
            if isinstance(value, (int, float)):
                return int(value)
            if isinstance(value, str):
                digits = "".join(ch for ch in value if ch.isdigit())
                if digits:
                    try:
                        return int(digits)
                    except Exception:
                        return None
            return None

        # Normalize category score schema for UI contract:
        # {logic, feasibility, ux_flow, business}
        raw_categories = out.get("category_scores")
        canonical_categories = {
            "logic": None,
            "feasibility": None,
            "ux_flow": None,
            "business": None,
        }

        alias_to_canonical = {
            "logic": "logic",
            "logicality": "logic",
            "논리성": "logic",
            "논리": "logic",
            "feasibility": "feasibility",
            "실현성": "feasibility",
            "실행성": "feasibility",
            "실행가능성": "feasibility",
            "ux_flow": "ux_flow",
            "ux": "ux_flow",
            "uxflow": "ux_flow",
            "user_flow": "ux_flow",
            "user_experience": "ux_flow",
            "ux 흐름": "ux_flow",
            "사용자경험": "ux_flow",
            "비즈니스": "business",
            "business": "business",
            "biz": "business",
            "사업성": "business",
        }

        def _apply_category_mapping(source):
            if not isinstance(source, dict):
                return
            for key, value in source.items():
                norm_key = str(key).strip().lower().replace("-", "_")
                canonical_key = alias_to_canonical.get(norm_key)
                if canonical_key is None:
                    canonical_key = alias_to_canonical.get(str(key).strip())
                if canonical_key is None:
                    continue
                parsed_score = _to_int(value)
                if parsed_score is not None:
                    canonical_categories[canonical_key] = parsed_score

        _apply_category_mapping(raw_categories)
        _apply_category_mapping(out)
        if any(v is not None for v in canonical_categories.values()):
            out["category_scores"] = canonical_categories

        parsed_total = _to_int(out.get("total_score"))
        if parsed_total is None and all(
            isinstance(canonical_categories[k], int) for k in ["logic", "feasibility", "ux_flow", "business"]
        ):
            parsed_total = int(
                (
                    canonical_categories["logic"]
                    + canonical_categories["feasibility"]
                    + canonical_categories["ux_flow"]
                    + canonical_categories["business"]
                )
                / 4.0
            )
        if parsed_total is not None:
            out["total_score"] = parsed_total

        out["status"] = normalized
        out["raw_status"] = evaluation.get("status") if isinstance(evaluation, dict) else None
        return out

    def evaluate_completion(
        self,
        document_md: str,
        custom_rules: str,
        rubric: str,
        confirm_threshold: int = 95,
    ) -> Dict[str, object]:
        evaluation = self.evaluate(
            document_md=document_md,
            custom_rules=custom_rules,
            rubric=rubric,
        )
        evaluation = self._normalize_evaluation_result(evaluation)
        score = evaluation.get("total_score")
        is_success = evaluation.get("status") == "SUCCESS"
        is_ready = is_success and isinstance(score, (int, float)) and score >= confirm_threshold
        return {
            "evaluation": evaluation,
            "completion_status": "READY_FOR_CONFIRM" if is_ready else "NEEDS_REVISION",
            "confirm_threshold": confirm_threshold,
        }

    def get_ui_step_state(
        self,
        phase: str,
        next_action: str,
        completion_status: str,
        requires_confirmation: bool,
    ) -> Dict[str, str]:
        phase_label = {
            "IDLE": "초기",
            "OUTLINE_READY": "목차 준비",
            "DOCUMENT_READY": "본문 준비",
        }.get(phase, "진행 중")

        if requires_confirmation or completion_status == "READY_FOR_CONFIRM":
            current_step = "사용자 컨펌 대기"
            next_step = "컨펌 후 저장"
        elif next_action == "REQUEST_EVALUATION":
            current_step = "평가 필요"
            next_step = "평가 실행"
        elif next_action == "REQUEST_REVISION":
            current_step = "수정 필요"
            next_step = "수정 요청 입력"
        else:
            current_step = "요청 대기"
            next_step = "요청 입력"

        return {
            "phase_label": phase_label,
            "current_step": current_step,
            "next_step": next_step,
        }

    def get_revision_guidance_from_evaluation(self, evaluation: Optional[Dict[str, object]]) -> Dict[str, object]:
        if not isinstance(evaluation, dict):
            return {"has_guidance": False, "summary": "", "missing_points": [], "category_scores": {}}

        summary = str(evaluation.get("summary") or "").strip()
        missing_points = evaluation.get("missing_points") or []
        if not isinstance(missing_points, list):
            missing_points = [str(missing_points)]
        missing_points = [str(item).strip() for item in missing_points if str(item).strip()]

        category_scores = evaluation.get("category_scores") or {}
        if not isinstance(category_scores, dict):
            category_scores = {}

        has_guidance = bool(summary or missing_points)
        return {
            "has_guidance": has_guidance,
            "summary": summary,
            "missing_points": missing_points,
            "category_scores": category_scores,
        }

    def compose_revision_request(
        self,
        user_request: str,
        evaluation: Optional[Dict[str, object]],
        selected_missing_points: Optional[list] = None,
        include_summary: bool = True,
    ) -> str:
        guidance = self.get_revision_guidance_from_evaluation(evaluation)
        summary = guidance["summary"] if include_summary else ""
        missing_points = guidance["missing_points"]
        if isinstance(selected_missing_points, list):
            selected_set = {str(item).strip() for item in selected_missing_points if str(item).strip()}
            missing_points = [point for point in missing_points if point in selected_set]

        has_guidance = bool(summary or missing_points)
        if not has_guidance:
            return user_request

        lines = [
            "### 사용자 수정 요청",
            user_request.strip() or "(요청 없음)",
            "",
            "### 평가 에이전트 피드백 반영 지침",
        ]
        if summary:
            lines.append(f"- 평가 요약: {summary}")
        if missing_points:
            lines.append("- 누락/개선 포인트:")
            for point in missing_points:
                lines.append(f"  - {point}")
        category_scores = guidance["category_scores"]
        if category_scores:
            score_items = ", ".join(f"{k}:{v}" for k, v in category_scores.items())
            lines.append(f"- 카테고리 점수: {score_items}")
        lines.extend(
            [
                "",
                "### 수정 원칙",
                "- 사용자 요청을 우선 반영하되, 평가 피드백에서 지적된 누락 항목을 반드시 보완할 것",
                "- 문서의 기존 구조와 문맥을 유지하며 필요한 부분만 명확히 수정할 것",
            ]
        )
        return "\n".join(lines).strip()

    def decide_next_interaction(
        self,
        phase: str,
        has_document: bool,
        latest_eval: Optional[Dict[str, object]] = None,
        completion_status: str = "",
        requires_confirmation: bool = False,
        user_confirmed: bool = False,
    ) -> Dict[str, str]:
        if not has_document:
            return {
                "action": "REQUEST_INPUT",
                "message": "요청을 입력하면 Host가 현재 상태를 바탕으로 다음 단계를 안내합니다.",
            }

        if requires_confirmation or completion_status == "READY_FOR_CONFIRM":
            if user_confirmed:
                return {
                    "action": "REQUEST_CONFIRM",
                    "message": "컨펌이 완료되었습니다. 저장 또는 확정 저장을 진행하세요.",
                }
            return {
                "action": "REQUEST_CONFIRM",
                "message": "완료 상태입니다. 컨펌 여부를 결정해주세요.",
            }

        if phase == "OUTLINE_READY":
            return {
                "action": "REQUEST_INPUT",
                "message": "목차 상태입니다. 본문 생성 또는 목차 수정 요청 중 하나를 선택해주세요.",
            }

        if latest_eval:
            status = latest_eval.get("status")
            score = latest_eval.get("total_score")
            if status == "SUCCESS" and isinstance(score, (int, float)):
                if score >= 95:
                    return {
                        "action": "REQUEST_CONFIRM",
                        "message": "평가 점수가 기준을 충족했습니다. 컨펌 후 저장을 결정해주세요.",
                    }
                return {
                    "action": "REQUEST_REVISION",
                    "message": "평가 결과가 기준 미달입니다. 사용자 수정 요청 후 재평가를 진행하세요.",
                }
            if status in ("PARTIAL_ERROR", "FAILED"):
                return {
                    "action": "REQUEST_EVALUATION",
                    "message": "평가 결과가 불완전합니다. 문서를 보완하거나 평가를 다시 실행해주세요.",
                }

        return {
            "action": "REQUEST_EVALUATION",
            "message": "현재 문서를 기준으로 평가를 진행해 다음 단계를 판단합니다.",
        }
