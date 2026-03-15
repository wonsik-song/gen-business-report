import ast
import json
import os
import re
import urllib.error
import urllib.request
from typing import Any, Dict, Optional, Tuple

from google import genai

from .models import CategoryScores, EvaluationResult
from .prompts import (
    build_evaluator_prompt,
    build_full_doc_prompt,
    build_outline_prompt,
    build_revise_prompt,
)


class A2AClient:
    """A2A transport client. If no endpoint is configured, use local ADK runtime."""

    def __init__(self, base_url: Optional[str] = None, timeout_s: int = 90):
        self.base_url = (base_url or os.environ.get("A2A_BASE_URL", "")).rstrip("/")
        self.timeout_s = timeout_s
        self.protocol = os.environ.get("A2A_PROTOCOL", "rest").lower()  # rest | jsonrpc
        self.invoke_path = os.environ.get("A2A_INVOKE_PATH", "/invoke")
        self.card_path_template = os.environ.get("A2A_CARD_PATH_TEMPLATE", "/agents/{agent_id}/card")
        self.api_key = os.environ.get("A2A_API_KEY")
        self.local_runtime = None

    def call(self, agent_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        if self.base_url:
            return self._call_remote(agent_id, payload)
        if self.local_runtime is None:
            self.local_runtime = LocalAdkRuntime()
        return self.local_runtime.invoke(agent_id, payload)

    def fetch_agent_card(self, agent_id: str) -> Dict[str, Any]:
        """
        Fetch agent card from A2A endpoint.
        Supports direct card response and wrapped forms (data/result/card).
        """
        if not self.base_url:
            raise RuntimeError("A2A_BASE_URL이 설정되지 않아 원격 카드 fetch를 사용할 수 없습니다.")

        card_path = self.card_path_template.format(agent_id=agent_id)
        headers = {"Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        req = urllib.request.Request(
            url=f"{self.base_url}{card_path}",
            headers=headers,
            method="GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            msg = e.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"Agent card fetch HTTP error ({e.code}): {msg}") from e
        except Exception as e:
            raise RuntimeError(f"Agent card fetch failed: {e}") from e

        if not isinstance(body, dict):
            raise RuntimeError("Agent card response must be a JSON object.")
        return _unwrap_agent_card_response(body)

    def _call_remote(self, agent_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        request_body = (
            self._jsonrpc_request(agent_id, payload)
            if self.protocol == "jsonrpc"
            else {"agent_id": agent_id, "payload": payload}
        )
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        req = urllib.request.Request(
            url=f"{self.base_url}{self.invoke_path}",
            data=json.dumps(request_body).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            msg = e.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"A2A HTTP error ({e.code}): {msg}") from e
        except Exception as e:
            raise RuntimeError(f"A2A call failed: {e}") from e

        if not isinstance(body, dict):
            raise RuntimeError("A2A response must be a JSON object.")
        return _unwrap_a2a_response(body)

    def _jsonrpc_request(self, agent_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": payload.get("request_id", "req-1"),
            "method": "invoke",
            "params": {"agent_id": agent_id, "payload": payload},
        }


class LocalAdkRuntime:
    """Local ADK-style runtime for planner/evaluator agents."""

    def __init__(self):
        api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY 또는 GOOGLE_API_KEY가 필요합니다.")
        self.client = genai.Client(api_key=api_key)
        self.planner_model = os.environ.get("PLANNER_MODEL", "gemini-3.1-pro-preview")
        self.evaluator_model = os.environ.get("EVALUATOR_MODEL", "gemini-3.1-pro-preview")

    def invoke(self, agent_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        if agent_id == "planner-agent":
            return self._planner(payload)
        if agent_id == "evaluator-agent":
            return self._evaluator(payload)
        raise ValueError(f"지원하지 않는 agent_id: {agent_id}")

    def _planner(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        model_name = payload.get("model") or self.planner_model
        mode = payload.get("mode")
        if mode == "OUTLINE":
            prompt = build_outline_prompt(
                user_intent=payload.get("user_intent", ""),
                context_text=payload.get("context_text", ""),
            )
        elif mode == "FULL_DOCUMENT":
            prompt = build_full_doc_prompt(
                user_intent=payload.get("user_intent", ""),
                context_text=payload.get("context_text", ""),
                outline_md=payload.get("outline_md", ""),
            )
        elif mode == "REVISE":
            prompt = build_revise_prompt(
                current_md=payload.get("current_md", ""),
                revision_request=payload.get("revision_request", ""),
            )
        else:
            raise ValueError(f"지원하지 않는 planner mode: {mode}")

        text, prompt_tokens, comp_tokens = _generate_text(self.client, model_name, prompt)
        return {
            "status": "SUCCESS",
            "markdown": text,
            "prompt_tokens": prompt_tokens,
            "comp_tokens": comp_tokens,
        }

    def _evaluator(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        model_name = payload.get("model") or self.evaluator_model
        prompt = build_evaluator_prompt(
            document_md=payload.get("document_md", ""),
            custom_rules=payload.get("custom_rules", ""),
            rubric=payload.get("rubric", ""),
        )
        raw_text, prompt_tokens, comp_tokens = _generate_text(self.client, model_name, prompt)
        parsed = _safe_parse_json(raw_text)
        if not parsed:
            return {
                "status": "PARTIAL_ERROR",
                "total_score": None,
                "category_scores": None,
                "summary": "평가 결과를 구조화하는 데 실패했습니다. 아래 원문을 확인해주세요.",
                "strengths": [],
                "missing_points": [],
                "raw_text": raw_text,
                "prompt_tokens": prompt_tokens,
                "comp_tokens": comp_tokens,
            }

        parsed = _normalize_evaluation_payload(parsed)
        try:
            result = EvaluationResult.model_validate(parsed)
        except Exception:
            return {
                "status": "PARTIAL_ERROR",
                "total_score": None,
                "category_scores": None,
                "summary": "평가 결과를 스키마에 맞게 변환하지 못했습니다.",
                "strengths": [],
                "missing_points": [],
                "raw_text": raw_text,
                "prompt_tokens": prompt_tokens,
                "comp_tokens": comp_tokens,
            }

        # Normalize status and total score.
        if not result.status:
            result.status = "SUCCESS"
        if result.status == "SUCCESS" and result.total_score is None and result.category_scores:
            scores: CategoryScores = result.category_scores
            result.total_score = int(
                (scores.logic + scores.feasibility + scores.ux_flow + scores.business) / 4.0
            )

        out = result.model_dump()
        out["prompt_tokens"] = prompt_tokens
        out["comp_tokens"] = comp_tokens
        return out


def _extract_text_from_response_parts(response: object) -> str:
    """
    Extract model output from candidates.content.parts without using response.text.
    This avoids SDK warnings when non-text parts (e.g. function_call) are present.
    """
    candidates = getattr(response, "candidates", None) or []
    text_parts = []
    function_args_parts = []

    for candidate in candidates:
        content = getattr(candidate, "content", None)
        parts = getattr(content, "parts", None) or []
        for part in parts:
            part_text = getattr(part, "text", None)
            if isinstance(part_text, str) and part_text.strip():
                text_parts.append(part_text.strip())

            function_call = getattr(part, "function_call", None)
            if function_call is not None:
                args = getattr(function_call, "args", None)
                if isinstance(args, dict):
                    function_args_parts.append(json.dumps(args, ensure_ascii=False))
                elif isinstance(args, str) and args.strip():
                    function_args_parts.append(args.strip())
                elif args is not None:
                    try:
                        function_args_parts.append(json.dumps(args, ensure_ascii=False))
                    except Exception:
                        pass

    if function_args_parts:
        return "\n".join(function_args_parts).strip()
    if text_parts:
        return "\n".join(text_parts).strip()
    return ""


def _generate_text(client: genai.Client, model_name: str, prompt: str) -> Tuple[str, int, int]:
    response = client.models.generate_content(model=model_name, contents=prompt)
    text = _extract_text_from_response_parts(response)
    usage = getattr(response, "usage_metadata", None)
    prompt_tokens = int(getattr(usage, "prompt_token_count", 0) or 0)
    comp_tokens = int(getattr(usage, "candidates_token_count", 0) or 0)
    return text, prompt_tokens, comp_tokens


def _safe_parse_json(text: str) -> Optional[Dict[str, Any]]:
    cleaned = (text or "").strip()
    if not cleaned:
        return None

    # Prefer fenced JSON block when model returns markdown wrapper.
    fenced_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, flags=re.DOTALL)
    if fenced_match:
        candidate = fenced_match.group(1).strip()
        try:
            return json.loads(candidate)
        except Exception:
            pass

    # Try direct JSON parse first.
    try:
        return json.loads(cleaned)
    except Exception:
        pass

    # Try extracting the first object-like region.
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start >= 0 and end > start:
        candidate = cleaned[start : end + 1]
        # Normalize common LLM artifacts.
        candidate = candidate.replace("“", '"').replace("”", '"').replace("’", "'")
        candidate = re.sub(r",\s*([}\]])", r"\1", candidate)
        try:
            return json.loads(candidate)
        except Exception:
            # Fallback: python-literal style dict with single quotes.
            try:
                literal = ast.literal_eval(candidate)
                if isinstance(literal, dict):
                    return literal
            except Exception:
                return None
    return None


def _unwrap_a2a_response(body: Dict[str, Any]) -> Dict[str, Any]:
    """
    Support multiple A2A server response shapes:
    1) { ...payload... }
    2) { "data": { ...payload... } }
    3) { "result": { ...payload... } } (JSON-RPC / REST wrappers)
    4) { "result": { "output": { ...payload... } } }
    """
    if "error" in body and body["error"]:
        raise RuntimeError(f"A2A server error: {body['error']}")

    # JSON-RPC: {"jsonrpc":"2.0","result":{...}}
    if isinstance(body.get("result"), dict):
        result = body["result"]
        if isinstance(result.get("output"), dict):
            return result["output"]
        if isinstance(result.get("data"), dict):
            return result["data"]
        if isinstance(result.get("payload"), dict):
            return result["payload"]
        return result

    # Common REST wrappers
    if isinstance(body.get("data"), dict):
        return body["data"]
    if isinstance(body.get("payload"), dict):
        return body["payload"]
    return body


def extract_markdown(result: Dict[str, Any]) -> str:
    if isinstance(result.get("markdown"), str):
        return result["markdown"]
    if isinstance(result.get("content_md"), str):
        return result["content_md"]
    if isinstance(result.get("content"), str):
        return result["content"]
    if isinstance(result.get("text"), str):
        return result["text"]
    output = result.get("output")
    if isinstance(output, dict):
        return (
            output.get("markdown")
            or output.get("content_md")
            or output.get("content")
            or output.get("text")
            or ""
        )
    return ""


def _normalize_evaluation_payload(parsed: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normalize slight schema deviations from evaluator outputs before Pydantic validation.
    """
    if not isinstance(parsed, dict):
        return {}

    out = dict(parsed)
    if isinstance(out.get("evaluation"), dict):
        out = dict(out["evaluation"])
    if "status" not in out or not out.get("status"):
        out["status"] = "SUCCESS"

    # Normalize category score aliases.
    cat_alias = {
        "logic": "logic",
        "logicality": "logic",
        "논리성": "logic",
        "feasibility": "feasibility",
        "실현성": "feasibility",
        "실행성": "feasibility",
        "ux_flow": "ux_flow",
        "ux": "ux_flow",
        "user_flow": "ux_flow",
        "business": "business",
        "비즈니스": "business",
        "사업성": "business",
    }

    category_source = out.get("category_scores")
    if not isinstance(category_source, dict) and isinstance(out.get("categories"), dict):
        category_source = out.get("categories")

    normalized_category_scores = {}
    if isinstance(category_source, dict):
        for key, value in category_source.items():
            alias_key = str(key).strip().lower().replace("-", "_")
            canonical = cat_alias.get(alias_key) or cat_alias.get(str(key).strip())
            if canonical:
                normalized_category_scores[canonical] = value
    if normalized_category_scores:
        out["category_scores"] = normalized_category_scores

    # If category scores are top-level, fold them into category_scores.
    if not isinstance(out.get("category_scores"), dict):
        has_top_scores = all(k in out for k in ["logic", "feasibility", "ux_flow", "business"])
        if has_top_scores:
            out["category_scores"] = {
                "logic": out.get("logic"),
                "feasibility": out.get("feasibility"),
                "ux_flow": out.get("ux_flow"),
                "business": out.get("business"),
            }

    if "strengths" in out and not isinstance(out.get("strengths"), list):
        out["strengths"] = [str(out["strengths"])]
    if "missing_points" in out and not isinstance(out.get("missing_points"), list):
        out["missing_points"] = [str(out["missing_points"])]

    if "summary" not in out or out.get("summary") is None:
        out["summary"] = ""

    return out


def _unwrap_agent_card_response(body: Dict[str, Any]) -> Dict[str, Any]:
    if isinstance(body.get("card"), dict):
        return body["card"]
    if isinstance(body.get("data"), dict):
        data = body["data"]
        if isinstance(data.get("card"), dict):
            return data["card"]
        return data
    if isinstance(body.get("result"), dict):
        result = body["result"]
        if isinstance(result.get("card"), dict):
            return result["card"]
        return result
    return body
