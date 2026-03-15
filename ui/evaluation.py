import streamlit as st
from agents.evaluator import EvaluatorAgent
from utils.db import save_evaluation, save_audit_log, get_project_prompt, get_global_prompts, get_daily_evaluate_count

def render_evaluation_panel(draft_doc):
    user = st.session_state.user
    
    st.subheader("평가 실행")
    
    if "evaluator_agent" not in st.session_state:
        st.session_state.evaluator_agent = EvaluatorAgent()
        
    proj_idx = draft_doc['project_id']
    proj_prompt = get_project_prompt(proj_idx)
    global_prompts = get_global_prompts(user.id)
    
    if proj_prompt:
        active_rubric = proj_prompt['custom_rules']
        st.caption("프로젝트별 평가 프롬프트 사용 중")
    elif global_prompts:
        active_rubric = global_prompts[0]['custom_rules']
        st.caption("글로벌 사용자 평가 프롬프트 사용 중")
    else:
        active_rubric = "표준 평가 기준: 명확성, 실행 가능한 항목, 적절한 에러 처리, 지표 정의, 비즈니스 정당성을 확인합니다."
        st.caption("기본 시스템 평가 기준 사용 중")

    custom_rules = st.text_area("이번 평가에 적용할 추가 규칙이 있나요?", "")
    
    if st.button("평가 시작", type="primary"):
        if get_daily_evaluate_count(user.id) >= 10:
            st.error("일일 평가 한도(10회)에 도달했습니다. 내일 다시 시도해주세요.")
            save_audit_log(user.id, "RATE_LIMIT", "WARN", {"feature": "evaluate"})
        else:
            with st.spinner("문서를 평가하고 있습니다..."):
                try:
                    result = st.session_state.evaluator_agent.run(
                        document_md=draft_doc['content_md'],
                        custom_rules=custom_rules,
                        rubric=active_rubric
                    )
                    
                    save_evaluation(draft_doc['id'], result)
                    
                    action = f"EVALUATE_{result.get('status', 'FAILED')}"
                    sev = "INFO" if result.get('status') == "SUCCESS" else "WARN"
                    save_audit_log(user.id, action, sev, {"doc_id": draft_doc['id']})
                    
                    st.session_state.latest_eval_result = result
                    st.success("평가가 완료되었습니다.")
                except Exception as e:
                    save_audit_log(user.id, "EVALUATE_FAILED", "ERROR", {"error": str(e)})
                    st.error(f"평가 요청 실패: {e}")

    if "latest_eval_result" in st.session_state and st.session_state.latest_eval_result:
        res = st.session_state.latest_eval_result
        st.subheader("평가 결과")
        status = res.get("status")
        
        if status == "SUCCESS":
            st.metric("종합 점수", f"{res.get('total_score', '?')}/100")
            
            cscores = res.get('category_scores', {})
            col1, col2, col3, col4 = st.columns(4)
            col1.metric("논리성", cscores.get('logic', '?'))
            col2.metric("실현 가능성", cscores.get('feasibility', '?'))
            col3.metric("UX 흐름", cscores.get('ux_flow', '?'))
            col4.metric("비즈니스", cscores.get('business', '?'))
            
            st.markdown(f"**요약:** {res.get('summary', '')}")
            
            st.divider()
            
            rcol1, rcol2 = st.columns(2)
            with rcol1:
                st.markdown("### ✅ 강점")
                for s in res.get('strengths', []):
                    st.write(f"- {s}")
            with rcol2:
                st.markdown("### ❌ 누락 항목")
                for m in res.get('missing_points', []):
                    st.write(f"- {m}")
                    
        elif status == "PARTIAL_ERROR":
            st.warning("부분 오류: 구조화된 파싱에 실패했습니다.")
            st.markdown(f"**요약:** {res.get('summary', '')}")
            with st.expander("AI 원본 텍스트"):
                st.text(res.get('raw_text', ''))
                
        else:
            st.error("평가에 실패했습니다.")
            st.write(res.get("summary", ""))
