from __future__ import annotations

import json
import re
from typing import Any

import httpx

from yowayowa.config import Settings
from yowayowa.domain import Operation, OperationKind, OperationPlan

_SYMBOL_RE = re.compile(r"(?<![A-Z0-9.\-])[A-Z][A-Z0-9.\-]{0,9}(?![A-Z0-9.\-])")
_JAPANESE_RE = re.compile(r"[\u3040-\u30ff\u3400-\u9fff]")


def _is_japanese(text: str) -> bool:
    return bool(_JAPANESE_RE.search(text))


def deterministic_plan(text: str) -> OperationPlan | None:
    normalized = text.strip()
    lowered = normalized.casefold()
    japanese = _is_japanese(normalized)
    symbols = list(dict.fromkeys(_SYMBOL_RE.findall(normalized.upper())))
    if ("ウォッチ" in normalized or "watchlist" in lowered) and symbols:
        remove = "外" in normalized or "remove" in lowered or "delete" in lowered
        kind = OperationKind.WATCHLIST_REMOVE if remove else OperationKind.WATCHLIST_ADD
        if japanese:
            verb = "から削除" if remove else "に追加"
            summary = f"{', '.join(symbols)} を現在のウォッチリスト{verb}"
        else:
            action = "Remove" if remove else "Add"
            direction = "from" if remove else "to"
            summary = f"{action} {', '.join(symbols)} {direction} the active watchlist"
        return OperationPlan(
            summary=summary,
            operations=[Operation(kind=kind, arguments={"symbols": symbols})],
            confidence=0.99,
            source="deterministic",
        )
    if ("比較" in normalized or "compare" in lowered) and len(symbols) >= 2:
        summary = f"{', '.join(symbols)} を比較" if japanese else f"Compare {', '.join(symbols)}"
        return OperationPlan(
            summary=summary,
            operations=[
                Operation(kind=OperationKind.COMPARE_SYMBOLS, arguments={"symbols": symbols})
            ],
            confidence=0.95,
            source="deterministic",
        )
    if "rsi" in lowered or "移動平均" in normalized or "sma" in lowered or "ema" in lowered:
        indicators: list[str] = []
        if "rsi" in lowered:
            match = re.search(r"rsi\D*(\d+)?", lowered)
            indicators.append(f"rsi{match.group(1) if match and match.group(1) else '14'}")
        if "200" in normalized and "移動平均" in normalized:
            indicators.append("sma200")
        elif "sma" in lowered:
            match = re.search(r"sma\D*(\d+)", lowered)
            indicators.append(f"sma{match.group(1) if match else '20'}")
        elif "ema" in lowered:
            match = re.search(r"ema\D*(\d+)", lowered)
            indicators.append(f"ema{match.group(1) if match else '20'}")
        summary = (
            f"チャート指標を設定: {', '.join(indicators)}"
            if japanese
            else f"Set chart indicators: {', '.join(indicators)}"
        )
        return OperationPlan(
            summary=summary,
            operations=[
                Operation(
                    kind=OperationKind.CHART_SET_INDICATORS,
                    arguments={"indicators": indicators},
                )
            ],
            confidence=0.9,
            source="deterministic",
        )
    return None


class OpenAICompatiblePlanner:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def available(self) -> bool:
        return bool(
            self.settings.openai_compatible_base_url
            and self.settings.openai_compatible_api_key
            and self.settings.openai_compatible_model
        )

    def plan(self, text: str) -> OperationPlan:
        base_url = self.settings.openai_compatible_base_url
        api_key = self.settings.openai_compatible_api_key
        model = self.settings.openai_compatible_model
        if not base_url or not api_key or not model:
            raise RuntimeError("OpenAI-compatible planner is not configured")
        schema = OperationPlan.model_json_schema()
        language_instruction = (
            "Write the summary in Japanese. "
            if _is_japanese(text)
            else "Write the summary in English. "
        )
        prompt = (
            "Translate the user's investment-workspace request into transparent GUI operations. "
            "Do not recommend securities and do not invent data. "
            + language_instruction
            + "Output exactly one JSON object matching this schema:\n"
            + json.dumps(schema, ensure_ascii=False)
            + "\nUser request:\n"
            + text
        )
        base = base_url.rstrip("/")
        response = httpx.post(
            f"{base}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0,
                "response_format": {"type": "json_object"},
            },
            timeout=self.settings.request_timeout_seconds,
        )
        response.raise_for_status()
        payload: dict[str, Any] = response.json()
        content = payload["choices"][0]["message"]["content"]
        parsed = OperationPlan.model_validate_json(content)
        return parsed.model_copy(update={"source": "llm", "requires_confirmation": True})


def plan_operation(text: str, settings: Settings) -> OperationPlan:
    deterministic = deterministic_plan(text)
    if deterministic is not None:
        return deterministic
    planner = OpenAICompatiblePlanner(settings)
    if planner.available():
        return planner.plan(text)
    summary = (
        "安全に実行できる定型操作に一致せず、BYOK LLMも設定されていません"
        if _is_japanese(text)
        else "No safe deterministic operation matched and no BYOK LLM is configured"
    )
    return OperationPlan(
        summary=summary,
        operations=[],
        confidence=0,
        requires_confirmation=True,
        source="deterministic",
    )
