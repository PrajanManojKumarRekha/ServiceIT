import json
import logging
import os
import re
import subprocess
from typing import Any

from dotenv import load_dotenv
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from langchain_google_genai import ChatGoogleGenerativeAI
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Trace
from services.incident_processor import (
    build_trace_summary,
    format_similar_incidents,
    get_service_metrics,
    search_similar_incidents,
)

load_dotenv()

logger = logging.getLogger(__name__)

def _resolve_chat_model(model_name: str) -> str:
    if model_name == "gemini-1.5-flash":
        return "models/gemini-2.0-flash"
    if model_name.startswith("models/"):
        return model_name
    if model_name.startswith("gemini-"):
        return f"models/{model_name}"
    return model_name


_MODEL_NAME = _resolve_chat_model(os.getenv("GEMINI_MODEL", "gemini-1.5-flash"))
_GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
_MAX_TOOL_CALLS = 2
_SYSTEM_PROMPT = """You are ServiceIT, an autonomous incident diagnostic agent.
Given a failing trace, use your tools to investigate and return a JSON object:
{
  "root_cause": "one sentence",
  "severity": "LOW|MEDIUM|HIGH|CRITICAL",
  "contributing_factors": ["factor1", "factor2"],
  "recommendations": ["action1", "action2", "action3"],
  "confidence": 0.0
}
Be concise. Max 2 tool calls. Return only the JSON object."""

if not _GEMINI_API_KEY:
    raise RuntimeError("GEMINI_API_KEY is required for diagnostic agent")

_llm = ChatGoogleGenerativeAI(
    model=_MODEL_NAME,
    google_api_key=_GEMINI_API_KEY,
    temperature=0.1,
    max_output_tokens=2048,
)


def _default_response() -> dict[str, Any]:
    return {
        "root_cause": "Unable to determine root cause from available trace context.",
        "severity": "MEDIUM",
        "contributing_factors": ["Insufficient diagnostic context"],
        "recommendations": [
            "Inspect the failing service logs around the trace timestamp",
            "Retry the request path with debug logging enabled",
            "Review recent config or dependency changes for the service",
        ],
        "confidence": 0.2,
    }


def _safe_parse_json(payload: str) -> dict[str, Any]:
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError:
        logger.warning("Agent returned non-JSON output")
        return _default_response()

    if not isinstance(parsed, dict):
        return _default_response()

    result = _default_response()
    result["root_cause"] = str(parsed.get("root_cause", result["root_cause"]))

    severity = str(parsed.get("severity", result["severity"])).upper()
    if severity in {"LOW", "MEDIUM", "HIGH", "CRITICAL"}:
        result["severity"] = severity

    contributing_factors = parsed.get("contributing_factors", result["contributing_factors"])
    if isinstance(contributing_factors, list) and contributing_factors:
        result["contributing_factors"] = [str(item) for item in contributing_factors[:5]]

    recommendations = parsed.get("recommendations", result["recommendations"])
    if isinstance(recommendations, list) and recommendations:
        result["recommendations"] = [str(item) for item in recommendations[:5]]

    confidence = parsed.get("confidence", result["confidence"])
    if isinstance(confidence, (int, float)):
        result["confidence"] = max(0.0, min(1.0, float(confidence)))

    return result


async def run_diagnostic_agent(session: AsyncSession, trace: Trace) -> dict[str, Any]:
    try:
        tools = _build_tools(session)
        tool_map = {tool_.name: tool_ for tool_ in tools}
        llm_with_tools = _llm.bind_tools(tools)

        messages = [
            SystemMessage(content=_SYSTEM_PROMPT),
            HumanMessage(
                content=(
                    "Investigate this failing trace.\n\n"
                    f"{build_trace_summary(trace)}\n\n"
                    "Use tools only if needed. Prefer the error message and at most the top 3 similar incidents."
                )
            ),
        ]

        tool_calls_used = 0
        while True:
            response = await llm_with_tools.ainvoke(messages)
            messages.append(response)

            if not isinstance(response, AIMessage):
                return _default_response()

            tool_calls = response.tool_calls or []
            if not tool_calls:
                return _safe_parse_json(response.content if isinstance(response.content, str) else "")

            remaining_tool_budget = _MAX_TOOL_CALLS - tool_calls_used
            if remaining_tool_budget <= 0:
                break

            for tool_call in tool_calls[:remaining_tool_budget]:
                selected_tool = tool_map.get(tool_call["name"])
                if selected_tool is None:
                    continue

                tool_result = await selected_tool.ainvoke(tool_call["args"])
                messages.append(ToolMessage(content=str(tool_result), tool_call_id=tool_call["id"]))
                tool_calls_used += 1

            if tool_calls_used >= _MAX_TOOL_CALLS:
                final_response = await _llm.ainvoke(
                    messages
                    + [
                        HumanMessage(
                            content="You have reached the tool limit. Return the final JSON object now."
                        )
                    ]
                )
                if isinstance(final_response, AIMessage):
                    return _safe_parse_json(
                        final_response.content if isinstance(final_response.content, str) else ""
                    )
                break
    except Exception:
        logger.exception("Diagnostic agent fallback triggered")
        return _default_response()

    return _default_response()


_SERVICE_NAME_RE = re.compile(r'^[a-zA-Z0-9\-]{1,64}$')


def _validate_service_name(service_name: str) -> None:
    if not _SERVICE_NAME_RE.match(service_name):
        raise ValueError(
            f"Invalid service_name '{service_name}': must match ^[a-zA-Z0-9\\-]{{1,64}}$"
        )


@tool
def get_recent_commits(service_name: str) -> str:
    """Get the last 5 git commits that touched files related to this service.
    Input: the service name (alphanumeric and hyphens only, max 64 chars).
    Returns: short commit list (hash, author, date, subject) or unavailability message.
    """
    try:
        _validate_service_name(service_name)
    except ValueError as exc:
        return f"Invalid service name: {exc}"

    try:
        result = subprocess.run(
            ["git", "log", "--oneline", "--format=%h %an %ad %s", "--date=short", "-5", "--", f"*{service_name}*"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        output = result.stdout.strip()
        return output if output else f"No recent commits found touching files matching '{service_name}'."
    except FileNotFoundError:
        return "Git history unavailable: git not found in PATH."
    except subprocess.TimeoutExpired:
        return "Git history unavailable: command timed out."
    except Exception as exc:
        logger.warning("get_recent_commits failed for '%s': %s", service_name, exc)
        return f"Git history unavailable: {exc}"


@tool
def fetch_raw_logs(service_name: str, tail_lines: int = 50) -> str:
    """Fetch recent stdout/stderr log lines from the running container for this service.
    Input: service_name (alphanumeric and hyphens only), tail_lines (10-200, default 50).
    Returns: last N log lines or an informative message if the container is not found.
    """
    try:
        _validate_service_name(service_name)
    except ValueError as exc:
        return f"Invalid service name: {exc}"

    tail_lines = max(10, min(200, tail_lines))

    try:
        import docker  # imported here so the module loads without Docker SDK at import time
        client = docker.from_env()
        containers = client.containers.list(filters={"name": service_name})
        if not containers:
            return f"No running container found for: {service_name}"
        raw = containers[0].logs(tail=tail_lines, stdout=True, stderr=True)
        return raw.decode("utf-8", errors="replace").strip()
    except Exception as exc:
        logger.warning("fetch_raw_logs failed for '%s': %s", service_name, exc)
        return f"Log retrieval unavailable for '{service_name}': {exc}"


def _build_tools(session: AsyncSession) -> list[Any]:
    @tool
    async def search_similar_incidents_tool(error_summary: str) -> str:
        """Search the database for past incidents similar to this error.
        Input: a brief description of the error (max 200 chars).
        Returns: up to 3 similar past incidents with their root causes.
        """
        trimmed_summary = error_summary.strip()[:200]
        matches = await search_similar_incidents(session, trimmed_summary, limit=3)
        return format_similar_incidents(matches)

    @tool
    async def get_service_metrics_tool(service_name: str) -> str:
        """Get recent error rate and trace counts for a service.
        Input: the service name exactly as it appears in traces.
        Returns: error count in last hour, total traces, error rate.
        """
        metrics = await get_service_metrics(session, service_name)
        return json.dumps(metrics)

    return [search_similar_incidents_tool, get_service_metrics_tool, get_recent_commits, fetch_raw_logs]
