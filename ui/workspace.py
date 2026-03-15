import streamlit as st
import time

from agents.prompts import apply_agent_prompt_templates
from utils.prompt_store import load_user_agent_prompt_templates
from utils.db import (
    upsert_draft,
    get_document_draft,
    finalize_document,
    get_finalized_documents,
    get_evaluations,
    save_audit_log,
    get_daily_generate_count,
    save_evaluation,
    get_project_prompt,
    get_global_prompts,
    get_daily_evaluate_count,
)


def _init_state():
    defaults = {
        "chat_messages": [],
        "chat_preview_md": "",
        "chat_phase": "IDLE",          # IDLE → OUTLINE_READY → DOCUMENT_READY
        "doc_view_mode": "preview",    # "preview" | "edit"
        "latest_eval_result": None,
        "planner_model_name": "gemini-3.1-pro-preview",
        "evaluator_model_name": "gemini-3.1-pro-preview",
        "host_model_name": "gemini-3.1-pro-preview",
        "host_requires_confirmation": False,
        "host_user_confirmed": False,
        "host_completion_status": "",
        "host_next_action": "REQUEST_INPUT",
        "host_next_message": "요청을 입력하면 Host가 다음 단계를 안내합니다.",
        "host_selected_missing_points": [],
        "host_include_eval_summary": True,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


def _ensure_agents():
    user = st.session_state.get("user")
    user_id = str(user.id) if user else ""
    if user_id and st.session_state.get("agent_prompt_templates_user_id") != user_id:
        persisted = load_user_agent_prompt_templates(user_id)
        if persisted:
            st.session_state.agent_prompt_templates = persisted
        else:
            st.session_state.agent_prompt_templates = {}
        st.session_state.agent_prompt_templates_user_id = user_id

    prompt_overrides = st.session_state.get("agent_prompt_templates")
    if isinstance(prompt_overrides, dict):
        apply_agent_prompt_templates(prompt_overrides)

    selected_planner = st.session_state.get("planner_model_name", "gemini-3.1-pro-preview")
    selected_evaluator = st.session_state.get("evaluator_model_name", "gemini-3.1-pro-preview")
    selected_host = st.session_state.get("host_model_name", "gemini-3.1-pro-preview")
    current_signature = f"{selected_planner}::{selected_evaluator}::{selected_host}"

    if "host_agent" not in st.session_state or st.session_state.get("host_agent_signature") != current_signature:
        from agents.host import HostAgent

        st.session_state.host_agent = HostAgent(
            planner_model=selected_planner,
            evaluator_model=selected_evaluator,
            host_model=selected_host,
        )
        st.session_state.host_agent_signature = current_signature
    user = st.session_state.get("user")
    proj = st.session_state.get("current_project")
    user_id = str(user.id) if user else None
    project_id = proj["id"] if isinstance(proj, dict) and "id" in proj else None
    st.session_state.host_agent.configure_document_context(project_id=project_id, user_id=user_id)


def _add_msg(role: str, content: str):
    st.session_state.chat_messages.append({"role": role, "content": content})


def _set_host_next_action(action: str, message: str, announce: bool = False):
    st.session_state.host_next_action = action
    st.session_state.host_next_message = message
    if announce:
        _add_msg("assistant", f"Host 단계 안내: {message}")


def _sync_host_guidance(announce: bool = False, prefix: str = ""):
    host = st.session_state.host_agent
    guidance = host.decide_next_interaction(
        phase=st.session_state.get("chat_phase", "IDLE"),
        has_document=bool((st.session_state.get("chat_preview_md") or "").strip()),
        latest_eval=st.session_state.get("latest_eval_result"),
        completion_status=st.session_state.get("host_completion_status", ""),
        requires_confirmation=st.session_state.get("host_requires_confirmation", False),
        user_confirmed=st.session_state.get("host_user_confirmed", False),
    )
    message = guidance.get("message", "")
    if prefix:
        message = f"{prefix} {message}".strip()
    _set_host_next_action(guidance.get("action", "REQUEST_INPUT"), message, announce=announce)


def _build_revision_request_with_eval(user_request: str) -> str:
    host = st.session_state.host_agent
    latest_eval = st.session_state.get("latest_eval_result")
    selected_points = st.session_state.get("host_selected_missing_points")
    include_summary = bool(st.session_state.get("host_include_eval_summary", True))
    return host.compose_revision_request(
        user_request=user_request,
        evaluation=latest_eval,
        selected_missing_points=selected_points,
        include_summary=include_summary,
    )


def _try_load_document_from_intent(host, intent: dict) -> bool:
    target = intent.get("target")
    version_num = intent.get("version_num")
    payload = host.load_document_with_fallback(target=target, version_num=version_num)

    status = str(payload.get("status") or "")
    if status != "SUCCESS":
        reason = payload.get("reason", "UNKNOWN")
        _add_msg("assistant", f"요청한 기획서를 불러오지 못했습니다. 사유: {reason}")
        return True

    doc = {
        "id": payload.get("document_id"),
        "content_md": payload.get("content_md", "") or "",
    }
    _load_doc_to_editor(doc, phase="DOCUMENT_READY")
    if payload.get("is_draft"):
        _add_msg("assistant", "현재 초안을 불러왔습니다. 필요하면 바로 수정 또는 평가를 진행하세요.")
    else:
        ver = payload.get("version_num", "-")
        _add_msg("assistant", f"확정 버전(v{ver}) 기획서를 불러왔습니다.")
    return True


def _sync_revision_preferences_from_eval():
    latest_eval = st.session_state.get("latest_eval_result") or {}
    missing_points = latest_eval.get("missing_points") or []
    if not isinstance(missing_points, list):
        missing_points = []
    for key in list(st.session_state.keys()):
        if str(key).startswith("host_revision_point_"):
            del st.session_state[key]
    if "host_include_eval_summary_checkbox" in st.session_state:
        del st.session_state["host_include_eval_summary_checkbox"]
    st.session_state.host_selected_missing_points = [str(item) for item in missing_points if str(item).strip()]
    st.session_state.host_include_eval_summary = True


def _load_doc_to_editor(doc: dict, phase: str = "DOCUMENT_READY"):
    """Load selected document content into editor."""
    st.session_state.chat_preview_md = doc.get("content_md", "") or ""
    st.session_state.doc_view_mode = "edit"
    st.session_state.chat_phase = phase
    st.session_state.host_requires_confirmation = False
    st.session_state.host_user_confirmed = False
    st.session_state.host_completion_status = ""
    eval_rows = get_evaluations(doc["id"])
    if eval_rows:
        latest = eval_rows[0]
        feedback = latest.get("feedback_json") or {}
        loaded_eval = {
            "status": latest.get("status"),
            "total_score": latest.get("total_score"),
            "category_scores": feedback.get("category_scores"),
            "summary": feedback.get("summary"),
            "strengths": feedback.get("strengths", []),
            "missing_points": feedback.get("missing_points", []),
            "raw_text": feedback.get("raw_text"),
        }
        if hasattr(st.session_state.host_agent, "_normalize_evaluation_result"):
            loaded_eval = st.session_state.host_agent._normalize_evaluation_result(loaded_eval)
        st.session_state.latest_eval_result = loaded_eval
    else:
        st.session_state.latest_eval_result = None
        st.session_state.host_selected_missing_points = []
        st.session_state.host_include_eval_summary = True
    if st.session_state.latest_eval_result:
        _sync_revision_preferences_from_eval()
    _sync_host_guidance(announce=True, prefix="문서를 불러왔습니다.")


def _score_color_and_label(doc_id: str):
    eval_rows = get_evaluations(doc_id)
    if not eval_rows:
        return "#94a3b8", "-"

    latest = eval_rows[0]
    status = latest.get("status")
    score = latest.get("total_score")

    if status == "SUCCESS" and isinstance(score, (int, float)):
        return ("#22c55e", f"{score}/100") if score >= 95 else ("#9ca3af", f"{score}/100")
    if status == "PARTIAL_ERROR":
        return "#f59e0b", "PARTIAL_ERROR"
    if status == "FAILED":
        return "#ef4444", "FAILED"
    return "#94a3b8", str(status or "-")


def _evaluate_document(user, proj, md: str, trigger: str = "manual"):
    if get_daily_evaluate_count(user.id) >= 10:
        st.error("일일 평가 한도(10회)에 도달했습니다. 내일 다시 시도해주세요.")
        save_audit_log(user.id, "RATE_LIMIT", "WARN", {"feature": "evaluate"})
        return None

    proj_prompt = get_project_prompt(proj["id"])
    global_prompts = get_global_prompts(user.id)

    if proj_prompt:
        active_rubric = proj_prompt["custom_rules"]
    elif global_prompts:
        active_rubric = global_prompts[0]["custom_rules"]
    else:
        active_rubric = "표준 평가 기준: 명확성, 실행 가능한 항목, 적절한 에러 처리, 지표 정의, 비즈니스 정당성을 확인합니다."

    upsert_draft(proj["id"], md)
    draft = get_document_draft(proj["id"])

    result = st.session_state.host_agent.evaluate(
        document_md=md,
        custom_rules="",
        rubric=active_rubric,
    )
    if hasattr(st.session_state.host_agent, "_normalize_evaluation_result"):
        result = st.session_state.host_agent._normalize_evaluation_result(result)

    if draft:
        save_evaluation(draft["id"], result)

    action = f"EVALUATE_{result.get('status', 'FAILED')}"
    sev = "INFO" if result.get("status") == "SUCCESS" else "WARN"
    save_audit_log(user.id, action, sev, {"project_id": proj["id"], "trigger": trigger})

    st.session_state.latest_eval_result = result
    return result


def _resolve_active_rubric(user, proj) -> str:
    proj_prompt = get_project_prompt(proj["id"])
    global_prompts = get_global_prompts(user.id)

    if proj_prompt:
        return proj_prompt["custom_rules"]
    if global_prompts:
        return global_prompts[0]["custom_rules"]
    return "표준 평가 기준: 명확성, 실행 가능한 항목, 적절한 에러 처리, 지표 정의, 비즈니스 정당성을 확인합니다."


def _run_host_completion_cycle(user, proj, md: str, trigger: str = "host_auto"):
    if get_daily_evaluate_count(user.id) >= 10:
        st.error("일일 평가 한도(10회)에 도달했습니다. 내일 다시 시도해주세요.")
        save_audit_log(user.id, "RATE_LIMIT", "WARN", {"feature": "evaluate"})
        return None

    upsert_draft(proj["id"], md)
    draft = get_document_draft(proj["id"])

    completion = st.session_state.host_agent.evaluate_completion(
        document_md=md,
        custom_rules="",
        rubric=_resolve_active_rubric(user, proj),
        confirm_threshold=95,
    )
    result = completion["evaluation"]
    completion_status = completion["completion_status"]

    if draft:
        save_evaluation(draft["id"], result)

    action = f"EVALUATE_{result.get('status', 'FAILED')}"
    sev = "INFO" if result.get("status") == "SUCCESS" else "WARN"
    save_audit_log(user.id, action, sev, {"project_id": proj["id"], "trigger": trigger})

    st.session_state.latest_eval_result = result
    _sync_revision_preferences_from_eval()
    st.session_state.host_completion_status = completion_status
    st.session_state.host_requires_confirmation = completion_status == "READY_FOR_CONFIRM"
    st.session_state.host_user_confirmed = False
    _sync_host_guidance(announce=True, prefix="평가가 완료되었습니다.")
    return completion


def _handle_chat_input(user_input: str, user, proj):
    """Process chat input through host -> planner subagent."""
    _add_msg("user", user_input)

    phase = st.session_state.chat_phase
    host = st.session_state.host_agent
    current_md = st.session_state.get("chat_preview_md") or ""
    latest_eval = st.session_state.get("latest_eval_result")
    progress_lines = []

    with st.chat_message("assistant", avatar="🤖"):
        progress_placeholder = st.empty()
        progress_placeholder.markdown("실행 중...")

        def _on_progress(line: str):
            text = str(line or "").strip()
            if not text:
                return
            if progress_lines and progress_lines[-1] == text:
                return
            progress_lines.append(text)
            if len(progress_lines) > 10:
                progress_lines.pop(0)
            progress_placeholder.markdown(
                "**중간 실행 로그**\n" + "\n".join(f"- {item}" for item in progress_lines)
            )

        orchestrated = host.orchestrate_chat(
            user_input=user_input,
            phase=phase,
            current_md=current_md,
            latest_eval=latest_eval,
            rubric=_resolve_active_rubric(user, proj),
            progress_callback=_on_progress,
        )

    if progress_lines:
        _add_msg(
            "assistant",
            "**중간 실행 로그**\n" + "\n".join(f"- {item}" for item in progress_lines),
        )
    if isinstance(orchestrated, dict) and orchestrated.get("handled"):
        selected_subagent = str(orchestrated.get("selected_subagent") or "none").strip().lower()
        selected_tool = str(orchestrated.get("selected_tool") or "").strip()
        planner_mode = str(orchestrated.get("planner_mode") or "").strip().upper()
        tool_args = orchestrated.get("tool_args") if isinstance(orchestrated.get("tool_args"), dict) else {}
        needs_clarification = bool(orchestrated.get("needs_clarification"))
        message = str(orchestrated.get("message") or "").strip()

        if needs_clarification:
            _add_msg("assistant", message or "요청 의도를 조금 더 구체적으로 알려주세요.")
            st.rerun()
            return

        if selected_tool in {"_adk_load_current_draft", "_adk_load_finalized_document"}:
            load_target = "draft" if selected_tool == "_adk_load_current_draft" else "finalized"
            version_num = tool_args.get("version_num")
            if not isinstance(version_num, int):
                version_num = None
            load_payload = host.load_document_with_fallback(target=load_target, version_num=version_num)
            if str(load_payload.get("status") or "") == "SUCCESS":
                _load_doc_to_editor(
                    {
                        "id": load_payload.get("document_id"),
                        "content_md": load_payload.get("content_md", "") or "",
                    },
                    phase="DOCUMENT_READY",
                )
                _add_msg("assistant", message or "요청한 문서를 불러왔습니다.")
            else:
                _add_msg("assistant", message or "요청한 문서를 불러오지 못했습니다.")
            st.rerun()
            return

        if selected_tool == "_adk_finalize_current_version":
            latest_eval = st.session_state.get("latest_eval_result") or {}
            score = latest_eval.get("total_score")
            can_finalize = (
                latest_eval.get("status") == "SUCCESS"
                and isinstance(score, (int, float))
                and score >= 95
            )
            if not can_finalize:
                _add_msg("assistant", "확정은 최신 평가 점수가 95점 이상일 때 가능합니다. 먼저 평가를 진행해주세요.")
                st.rerun()
                return
            if st.session_state.get("host_requires_confirmation") and not st.session_state.get("host_user_confirmed"):
                _add_msg("assistant", "먼저 컨펌이 필요합니다. 우측 Host 제어센터에서 컨펌 후 다시 요청해주세요.")
                st.rerun()
                return
            if st.session_state.chat_preview_md:
                upsert_draft(proj["id"], st.session_state.chat_preview_md)
            ok = host.finalize_current_version(source="host_chat_intent")
            if ok:
                save_audit_log(user.id, "FINALIZE_VERSION", "INFO", {"project_id": proj["id"], "trigger": "chat_intent"})
                st.session_state.host_requires_confirmation = False
                st.session_state.host_user_confirmed = False
                st.session_state.host_completion_status = ""
                _sync_host_guidance(announce=True, prefix="Host가 확정 저장을 완료했습니다.")
                _add_msg("assistant", message or "확정 저장을 완료했습니다.")
            else:
                _add_msg("assistant", message or "확정 저장에 실패했습니다.")
            st.rerun()
            return

        if selected_subagent == "evaluator_subagent":
            if phase == "OUTLINE_READY":
                _add_msg("assistant", "현재는 목차 단계입니다. 본문 생성 후 평가를 진행할 수 있습니다.")
                st.rerun()
                return
            completion = _run_host_completion_cycle(
                user=user,
                proj=proj,
                md=st.session_state.get("chat_preview_md") or "",
                trigger="host_orchestrated",
            )
            if completion:
                _add_msg("assistant", message or "평가를 완료했습니다.")
            else:
                _add_msg("assistant", message or "평가를 완료하지 못했습니다.")
            st.rerun()
            return

        if selected_subagent == "planner_subagent":
            if not planner_mode:
                planner_mode = "OUTLINE" if phase == "IDLE" else "REVISE"

            try:
                if planner_mode == "OUTLINE":
                    if get_daily_generate_count(user.id) >= 50:
                        _add_msg("assistant", "일일 생성 한도(50회)에 도달했습니다. 내일 다시 시도해주세요.")
                        save_audit_log(user.id, "RATE_LIMIT", "WARN", {"feature": "generate"})
                        st.rerun()
                        return
                    outline = host.plan(mode="OUTLINE", user_intent=user_input, context_text="")
                    st.session_state.chat_preview_md = outline
                    st.session_state.chat_phase = "OUTLINE_READY"
                    st.session_state.host_requires_confirmation = False
                    st.session_state.host_user_confirmed = False
                    st.session_state.host_completion_status = ""
                    _sync_host_guidance(announce=True, prefix="목차 생성이 완료되었습니다.")
                    _add_msg("assistant", message or "목차를 생성했습니다.")
                    st.rerun()
                    return

                if planner_mode == "FULL_DOCUMENT":
                    if get_daily_generate_count(user.id) >= 50:
                        _add_msg("assistant", "일일 생성 한도(50회)에 도달했습니다. 내일 다시 시도해주세요.")
                        save_audit_log(user.id, "RATE_LIMIT", "WARN", {"feature": "generate"})
                        st.rerun()
                        return
                    outline_md = st.session_state.get("chat_preview_md") or ""
                    if not outline_md.strip():
                        _add_msg("assistant", "본문 생성을 위해 먼저 목차가 필요합니다.")
                        st.rerun()
                        return
                    full_doc = host.plan(
                        mode="FULL_DOCUMENT",
                        user_intent=user_input,
                        context_text="",
                        outline_md=outline_md,
                    )
                    st.session_state.chat_preview_md = full_doc
                    st.session_state.chat_phase = "DOCUMENT_READY"
                    st.session_state.host_requires_confirmation = False
                    st.session_state.host_user_confirmed = False
                    st.session_state.host_completion_status = ""
                    _sync_host_guidance(announce=True, prefix="본문 생성이 완료되었습니다.")
                    _add_msg("assistant", message or "전체 기획서를 생성했습니다.")
                    st.rerun()
                    return

                revision_request = _build_revision_request_with_eval(user_input)
                revised = host.revise(
                    current_md=st.session_state.get("chat_preview_md") or "",
                    revision_request=revision_request,
                )
                st.session_state.chat_preview_md = revised
                st.session_state.chat_phase = "DOCUMENT_READY" if phase != "IDLE" else "OUTLINE_READY"
                st.session_state.host_requires_confirmation = False
                st.session_state.host_user_confirmed = False
                st.session_state.host_completion_status = ""
                _sync_host_guidance(announce=True, prefix="문서 수정이 완료되었습니다.")
                _add_msg("assistant", message or "요청 사항을 반영해 문서를 수정했습니다.")
                st.rerun()
                return
            except Exception as e:
                _add_msg("assistant", message or f"요청 처리 중 오류가 발생했습니다: {e}")
                st.rerun()
                return

        if selected_subagent == "none":
            st.session_state.host_requires_confirmation = False
            _add_msg("assistant", message or "요청을 처리했습니다.")
            st.rerun()
            return

    _add_msg(
        "assistant",
        "Host 자동 실행 결과를 받지 못했습니다. 현재는 LlmAgent의 tool 호출 결과만 처리하도록 설정되어 있어 다시 시도해주세요.",
    )
    st.rerun()


def _run_evaluation(user, proj):
    """Run AI evaluation on the current document content."""
    md = st.session_state.chat_preview_md
    if not md:
        st.warning("평가할 문서가 없습니다. 먼저 기획서를 생성해주세요.")
        return

    with st.spinner("문서를 평가하고 있습니다..."):
        try:
            completion = _run_host_completion_cycle(user, proj, md, trigger="manual")
            if not completion:
                return
            result = completion["evaluation"]
            completion_status = completion["completion_status"]

            if result.get("status") == "SUCCESS":
                summary = result.get("summary", "")
                score = result.get("total_score", "?")
                _add_msg("assistant",
                         f"**평가 완료** (종합 점수: {score}/100)\n\n{summary}\n\n"
                         "수정이 필요하면 채팅으로 요청하세요.")
                if completion_status == "READY_FOR_CONFIRM":
                    _add_msg("assistant", "완료 상태입니다. 아래 컨펌 패널에서 승인하면 저장할 수 있습니다.")
                else:
                    _add_msg("assistant", "아직 완료 기준(95점)에 미달합니다. 수정 후 다시 평가해주세요.")
                    missing_points = result.get("missing_points") or []
                    if isinstance(missing_points, list) and missing_points:
                        preview_points = "\n".join(f"- {str(item)}" for item in missing_points[:5])
                        _add_msg("assistant", f"수정 필요 포인트를 Host가 전달받았습니다:\n{preview_points}")

            st.rerun()
        except Exception as e:
            save_audit_log(user.id, "EVALUATE_FAILED", "ERROR", {"error": str(e)})
            st.error(f"평가 요청 실패: {e}")


def _render_eval_results():
    """Display evaluation results in an expander."""
    res = st.session_state.get("latest_eval_result")
    if not res:
        return

    status = res.get("status")
    with st.expander("📊 최근 평가 결과", expanded=True):
        if status == "SUCCESS":
            m1, m2, m3, m4, m5 = st.columns(5)
            m1.metric("종합", f"{res.get('total_score', '?')}")
            cscores = res.get("category_scores", {})
            if isinstance(cscores, dict):
                m2.metric("논리성", cscores.get("logic", "?"))
                m3.metric("실현성", cscores.get("feasibility", "?"))
                m4.metric("UX", cscores.get("ux_flow", "?"))
                m5.metric("비즈니스", cscores.get("business", "?"))

            st.markdown(f"**요약:** {res.get('summary', '')}")
            st.divider()
            c1, c2 = st.columns(2)
            with c1:
                st.markdown("**✅ 강점**")
                for s in res.get("strengths", []):
                    st.write(f"- {s}")
            with c2:
                st.markdown("**❌ 누락 항목**")
                for m in res.get("missing_points", []):
                    st.write(f"- {m}")
        elif status == "PARTIAL_ERROR":
            st.warning("부분 오류: 구조화된 파싱에 실패했습니다.")
            st.markdown(f"**요약:** {res.get('summary', '')}")
            raw_text = res.get("raw_text", "")
            if raw_text:
                with st.expander("원본 평가 응답 보기"):
                    st.text(raw_text)
        else:
            st.error("평가에 실패했습니다.")
            st.write(res.get("summary", ""))


def _save_current_draft(user, proj, success_text: str = "저장되었습니다!"):
    if not st.session_state.chat_preview_md:
        st.warning("저장할 내용이 없습니다.")
        return False

    upsert_draft(proj["id"], st.session_state.chat_preview_md)
    save_audit_log(user.id, "SAVE_DRAFT", "INFO", {"project_id": proj["id"]})
    st.session_state.host_requires_confirmation = False
    st.session_state.host_user_confirmed = False
    st.session_state.host_completion_status = ""
    _sync_host_guidance(announce=True, prefix="저장이 완료되었습니다.")
    st.success(success_text)
    time.sleep(0.7)
    st.rerun()
    return True


def render_workspace():
    st.title("작업 공간")

    user = st.session_state.user
    proj = st.session_state.current_project

    if not user or not proj:
        st.warning("프로젝트 탭에서 프로젝트를 선택해주세요.")
        return

    _init_state()
    _ensure_agents()
    _sync_host_guidance(announce=False)

    st.markdown(f"### 프로젝트: {proj['title']}")
    st.caption("Host 제어센터가 다음 액션을 제안하고, 사용자는 제안된 단계대로 진행합니다.")

    # ── Sidebar: Document History ──
    with st.sidebar:
        st.subheader("문서 버전 이력")
        cards = st.session_state.host_agent.get_agent_cards()
        skills = st.session_state.host_agent.get_agent_skills()
        with st.expander("Host Agent 카드", expanded=False):
            if "host" in cards:
                st.caption(f"Host: {cards['host']['name']} ({cards['host']['agent_id']})")
                for skill in skills.get("host", []):
                    st.caption(f"- {skill}")
            st.caption(f"Planner: {cards['planner']['name']} ({cards['planner']['agent_id']})")
            for skill in skills.get("planner", []):
                st.caption(f"- {skill}")
            st.caption(f"Evaluator: {cards['evaluator']['name']} ({cards['evaluator']['agent_id']})")
            for skill in skills.get("evaluator", []):
                st.caption(f"- {skill}")
        current_draft = get_document_draft(proj["id"])
        if current_draft:
            draft_color, draft_label = _score_color_and_label(current_draft["id"])
            st.markdown(
                f"초안: 편집 중 | 점수: <span style='color:{draft_color};font-weight:700'>{draft_label}</span>",
                unsafe_allow_html=True,
            )
            draft_cols = st.columns([2, 1])
            if draft_cols[0].button("현재 초안 불러오기", key="load_current_draft", use_container_width=True):
                _load_doc_to_editor(current_draft, phase="DOCUMENT_READY")
                st.rerun()
            draft_cols[1].download_button(
                "다운로드",
                data=current_draft.get("content_md", "") or "",
                file_name=f"{proj['title']}_draft.md",
                mime="text/markdown",
                key="download_current_draft",
                use_container_width=True,
            )
        else:
            st.caption("활성 초안이 없습니다.")

        finalized = get_finalized_documents(proj["id"])
        if finalized:
            with st.expander("확정 버전 보기", expanded=True):
                for doc in finalized:
                    score_color, score_label = _score_color_and_label(doc["id"])
                    st.markdown(
                        f"v{doc['version_num']} - {doc['created_at'][:10]} | 점수: "
                        f"<span style='color:{score_color};font-weight:700'>{score_label}</span>",
                        unsafe_allow_html=True,
                    )
                    fcols = st.columns([2, 1])
                    if fcols[0].button(
                        f"v{doc['version_num']} 불러오기",
                        key=f"load_final_{doc['id']}",
                        use_container_width=True,
                    ):
                        _load_doc_to_editor(doc, phase="DOCUMENT_READY")
                        st.rerun()
                    fcols[1].download_button(
                        "다운로드",
                        data=doc.get("content_md", "") or "",
                        file_name=f"{proj['title']}_v{doc['version_num']}.md",
                        mime="text/markdown",
                        key=f"download_final_{doc['id']}",
                        use_container_width=True,
                    )
        else:
            st.caption("아직 확정된 버전이 없습니다.")

    latest_eval = st.session_state.get("latest_eval_result")
    host_next_action = st.session_state.get("host_next_action", "REQUEST_INPUT")
    host_next_message = st.session_state.get("host_next_message", "요청을 입력하면 Host가 다음 단계를 안내합니다.")
    ui_step = st.session_state.host_agent.get_ui_step_state(
        phase=st.session_state.get("chat_phase", "IDLE"),
        next_action=host_next_action,
        completion_status=st.session_state.get("host_completion_status", ""),
        requires_confirmation=st.session_state.get("host_requires_confirmation", False),
    )
    current_score = "-"
    if latest_eval and isinstance(latest_eval.get("total_score"), (int, float)):
        current_score = f"{latest_eval.get('total_score')}/100"
    threshold_text = "95점"

    top1, top2, top3, top4 = st.columns(4)
    top1.metric("현재 단계", ui_step["current_step"])
    top2.metric("다음 단계", ui_step["next_step"])
    top3.metric("완료 기준", threshold_text)
    top4.metric("현재 점수", current_score)

    left, right = st.columns([2, 3], gap="large")

    # ── LEFT: Chat ──
    with left:
        st.markdown("#### 💬 AI 채팅")
        chat_container = st.container(height=520)
        with chat_container:
            for msg in st.session_state.chat_messages:
                avatar = "🧑‍💻" if msg["role"] == "user" else "🤖"
                with st.chat_message(msg["role"], avatar=avatar):
                    st.markdown(msg["content"])

        user_input = st.chat_input(
            "예) 사용자 여정 중심으로 기획서를 수정해줘",
            key="gen_chat_input",
        )
        if user_input:
            _handle_chat_input(user_input, user, proj)

    # ── RIGHT: Host Control Center + Document Panel ──
    with right:
        can_finalize = (
            bool(latest_eval)
            and latest_eval.get("status") == "SUCCESS"
            and isinstance(latest_eval.get("total_score"), (int, float))
            and latest_eval.get("total_score") >= 95
        )
        is_confirmed = st.session_state.get("host_user_confirmed", False)
        save_disabled = st.session_state.get("host_requires_confirmation") and not is_confirmed

        with st.container(border=True):
            st.markdown("### Host 제어센터")
            st.markdown(
                (
                    "<div class='gb-host-step-row'>"
                    f"<span class='gb-host-step-badge'>현재: {ui_step['current_step']}</span>"
                    f"<span class='gb-host-step-badge'>다음: {ui_step['next_step']}</span>"
                    "</div>"
                ),
                unsafe_allow_html=True,
            )
            st.caption(host_next_message)

            if host_next_action == "REQUEST_EVALUATION":
                if st.button("Host 제안 실행: 평가 진행", key="host_next_eval", type="primary", use_container_width=True):
                    _run_evaluation(user, proj)
            elif host_next_action == "REQUEST_REVISION":
                st.warning("추가 수정이 필요합니다. 왼쪽 채팅에 수정 요청을 입력해주세요.")
                latest_eval_for_revision = st.session_state.get("latest_eval_result") or {}
                missing_points = latest_eval_for_revision.get("missing_points") or []
                if isinstance(missing_points, list) and missing_points:
                    st.markdown("**반영할 평가 포인트 선택**")
                    selected_points = []
                    default_selected = set(st.session_state.get("host_selected_missing_points", []))
                    for idx, point in enumerate(missing_points):
                        point_text = str(point).strip()
                        if not point_text:
                            continue
                        is_checked = st.checkbox(
                            point_text,
                            value=(point_text in default_selected),
                            key=f"host_revision_point_{idx}",
                        )
                        if is_checked:
                            selected_points.append(point_text)
                    st.session_state.host_selected_missing_points = selected_points
                    st.session_state.host_include_eval_summary = st.checkbox(
                        "평가 요약도 수정 지침에 포함",
                        value=bool(st.session_state.get("host_include_eval_summary", True)),
                        key="host_include_eval_summary_checkbox",
                    )
                    st.caption(
                        f"선택된 포인트 {len(selected_points)}개가 사용자 요청과 함께 Host 수정 지침으로 전달됩니다."
                    )
                else:
                    st.info("현재 평가에서 선택 가능한 누락 포인트가 없습니다. 사용자 요청 중심으로 수정됩니다.")
            elif host_next_action == "REQUEST_CONFIRM":
                st.success("완료 상태입니다. 컨펌 후 저장 단계로 진행할 수 있습니다.")
            else:
                st.info("요청을 입력하면 Host가 다음 단계 액션을 제안합니다.")

        if st.session_state.get("host_requires_confirmation"):
            with st.container(border=True):
                st.markdown("**완료 상태 확인**: 평가 기준을 통과했습니다. 저장 전 사용자 컨펌이 필요합니다.")
                cc1, cc2, cc3 = st.columns(3)
                if cc1.button("✅ 컨펌", key="host_confirm_save", type="primary", use_container_width=True):
                    st.session_state.host_user_confirmed = True
                    _sync_host_guidance(announce=True, prefix="사용자가 컨펌했습니다.")
                    st.success("컨펌 완료. 이제 저장할 수 있습니다.")
                if cc2.button("✍️ 추가 수정", key="host_request_more_changes", type="secondary", use_container_width=True):
                    st.session_state.host_user_confirmed = False
                    st.session_state.host_requires_confirmation = False
                    st.session_state.host_completion_status = "NEEDS_REVISION"
                    _sync_host_guidance(announce=True, prefix="추가 수정을 선택했습니다.")
                    st.info("추가 수정을 진행해주세요.")
                if cc3.button("💾 컨펌 후 저장", key="host_confirm_then_save", type="secondary", use_container_width=True, disabled=save_disabled):
                    _save_current_draft(user, proj, success_text="컨펌된 초안을 저장했습니다.")

        panel_tabs = st.tabs(["문서 패널", "평가 결과"])
        with panel_tabs[0]:
            mode_cols = st.columns([1, 1, 1, 1, 1])
            if mode_cols[0].button("📝 편집", key="btn_edit", use_container_width=True):
                st.session_state.doc_view_mode = "edit"
                st.rerun()
            if mode_cols[1].button("👁 미리보기", key="btn_preview", use_container_width=True):
                st.session_state.doc_view_mode = "preview"
                st.rerun()
            if mode_cols[2].button("💾 임시 저장", key="btn_save", type="secondary", use_container_width=True):
                _save_current_draft(user, proj)
            if mode_cols[3].button("📊 평가 실행", key="btn_eval", type="secondary", use_container_width=True):
                _run_evaluation(user, proj)
            mode_cols[4].download_button(
                "⬇️ Markdown",
                data=st.session_state.chat_preview_md or "",
                file_name=f"{proj['title']}.md",
                mime="text/markdown",
                use_container_width=True,
            )

            if st.session_state.doc_view_mode == "edit":
                edited = st.text_area(
                    "마크다운 편집",
                    st.session_state.chat_preview_md,
                    height=500,
                    label_visibility="collapsed",
                    key="editor_textarea",
                )
                if edited != st.session_state.chat_preview_md:
                    st.session_state.chat_preview_md = edited
                    st.session_state.host_requires_confirmation = False
                    st.session_state.host_user_confirmed = False
                    st.session_state.host_completion_status = ""
                    _sync_host_guidance(announce=True, prefix="문서가 변경되었습니다.")
            else:
                if st.session_state.chat_preview_md:
                    with st.container(height=500):
                        st.markdown(st.session_state.chat_preview_md)
                else:
                    st.info("채팅으로 기획서를 생성하면 여기에 표시됩니다.")

        with panel_tabs[1]:
            _render_eval_results()
            c1, c2 = st.columns([2, 1])
            with c1:
                if not can_finalize:
                    st.caption("확정은 최신 평가 점수가 95점 이상일 때만 가능합니다.")
                elif save_disabled:
                    st.caption("저장 전 Host 컨펌이 필요합니다.")
            with c2:
                if st.button(
                    "✅ 확정 저장",
                    key="btn_finalize",
                    type="primary",
                    use_container_width=True,
                    disabled=(not can_finalize) or save_disabled,
                ):
                    if st.session_state.chat_preview_md:
                        upsert_draft(proj["id"], st.session_state.chat_preview_md)
                        finalize_document(proj["id"])
                        save_audit_log(user.id, "FINALIZE_VERSION", "INFO", {"project_id": proj["id"]})
                        st.session_state.host_requires_confirmation = False
                        st.session_state.host_user_confirmed = False
                        st.session_state.host_completion_status = ""
                        _sync_host_guidance(announce=True, prefix="확정 버전 저장이 완료되었습니다.")
                        st.success("버전이 확정되었습니다!")
                        time.sleep(0.9)
                        st.rerun()
                    else:
                        st.warning("확정할 내용이 없습니다.")
