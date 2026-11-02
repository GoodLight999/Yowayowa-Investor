# ruff: noqa: E501, RUF001
"""LLM-in-the-loop decision module for C2.

Calls zai Coding Plan (glm-5.3-flash) via subscription path (zero incremental cost).
Freezes the prompt template with immutable SHA256.
Maintains atomic reproducible records with full prompt/response evidence.
Categorizes attribution into:
- RULE: LLM agrees with rule or not called (weight_scale = 1.0)
- HYBRID: fine adjustment within ±10% (0.90 <= weight_scale <= 1.10, != 1.0)
- LLM: major override (> 10% change, e.g. risk-off reduction or emergency cash)
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .c2_features import C2MarketContext
from .c2_strategy import RuleOutput

logger = logging.getLogger("paper_c2.llm")

DEFAULT_MODEL = "glm-5.3-flash"
ZAI_API_URL = "https://api.z.ai/api/coding/paas/v4/chat/completions"

# Immutable frozen prompt template
PROMPT_TEMPLATE = """You are the quantitative risk officer in the loop for the C2 US Equity Volatility Target strategy (SPY, IWM, JNJ, PG).
Your mandate is to evaluate current market regime and systemic risk, and decide whether to maintain the quantitative rule or intervene.

=== MARKET CONTEXT ===
{market_context}

=== QUANTITATIVE RULE PROPOSALS ===
- 10% Vol Target Series: Rule Equity Weight = {w_rule_10:.4f} (Cash = {cash_10:.4f})
- 15% Vol Target Series: Rule Equity Weight = {w_rule_15:.4f} (Cash = {cash_15:.4f})

=== GOVERNANCE & INTERVENTION RULES ===
1. BASELINE IS TO MAINTAIN THE RULE (HOLD_RULE):
   - In normal or low-vol uptrends (BULL/LOW_VOL), the backward-looking volatility rule is optimal.
   - Do NOT intervene merely to express speculative market opinions.
2. OVERRIDE CAP:
   - LLM overrides are capped at <= 30.0% of total sessions across the 60-day phase.
3. INTERVENTION CONDITIONS:
   - Intervene ONLY if there is acute macroeconomic deterioration, liquidity crunch, rapid volatility spike,
     or severe trend breakdown that backward-looking 21-day σ has not yet incorporated.
   - Allowed intervention intents:
     * HOLD_RULE: Maintain quantitative rule weights (scale = 1.0, label = RULE).
     * FINE_TUNE_RISK: Minor adjustment within ±10% (scale between 0.90 and 1.10, label = HYBRID).
     * REDUCE_EXPOSURE_20PCT: Reduce equity sleeve by 20% (scale = 0.80, label = LLM).
     * REDUCE_EXPOSURE_50PCT: Reduce equity sleeve by 50% (scale = 0.50, label = LLM).
     * EMERGENCY_CASH: Force 100% cash / risk-off (scale = 0.0, label = LLM).

=== OUTPUT INSTRUCTION ===
Respond with ONLY a single valid JSON object strictly matching this schema:
{{
  "intent": "HOLD_RULE" | "FINE_TUNE_RISK" | "REDUCE_EXPOSURE_20PCT" | "REDUCE_EXPOSURE_50PCT" | "EMERGENCY_CASH",
  "weight_scale": float,
  "attribution_label": "RULE" | "LLM" | "HYBRID",
  "reasoning": "Clear, quantitative explanation justifying why the rule is maintained or overridden."
}}
"""

PROMPT_TEMPLATE_SHA256 = hashlib.sha256(PROMPT_TEMPLATE.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class LLMDecisionResult:
    called: bool
    model: str
    prompt_template_sha256: str
    prompt_input_summary: str
    raw_response: str
    parsed_intent: str
    weight_scale: float
    attribution_label: str
    reasoning: str

    def to_record_dict(self) -> dict[str, Any]:
        return {
            "called": self.called,
            "model": self.model,
            "prompt_template_sha256": self.prompt_template_sha256,
            "prompt_input_summary": self.prompt_input_summary,
            "raw_response": self.raw_response,
            "parsed_intent": self.parsed_intent,
            "weight_scale": self.weight_scale,
            "attribution_label": self.attribution_label,
            "reasoning": self.reasoning,
        }


def call_llm_decider(
    context: C2MarketContext,
    rule_10: RuleOutput,
    rule_15: RuleOutput,
    model: str = DEFAULT_MODEL,
    timeout_seconds: int = 40,
    force_rule_only: bool = False,
) -> LLMDecisionResult:
    """Execute LLM decision step with fail-closed safety.

    Uses permitted subscription routes (zai Coding Plan or agy).
    Distinguishes active HOLD_RULE (called=True) from failures (called=False).
    """
    summary_text = context.to_summary_text()

    if force_rule_only:
        return LLMDecisionResult(
            called=False,
            model=model,
            prompt_template_sha256=PROMPT_TEMPLATE_SHA256,
            prompt_input_summary=summary_text,
            raw_response="[SKIPPED: Rule-only execution requested]",
            parsed_intent="HOLD_RULE",
            weight_scale=1.0,
            attribution_label="RULE",
            reasoning="LLM not called: scheduled rule-only session.",
        )

    prompt = PROMPT_TEMPLATE.format(
        market_context=summary_text,
        w_rule_10=rule_10.w_rule,
        cash_10=rule_10.weights.get("USD", 0.0),
        w_rule_15=rule_15.w_rule,
        cash_15=rule_15.weights.get("USD", 0.0),
    )

    api_key = _get_zai_api_key()
    raw_output = ""
    used_model = model
    success = False

    # 1. Try zai Coding Plan if key exists
    if api_key:
        payload = json.dumps(
            {
                "model": model,
                "messages": [
                    {
                        "role": "system",
                        "content": "You are a quantitative portfolio risk officer. You must respond in valid JSON only.",
                    },
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.1,
                "max_tokens": 4096,
            }
        )
        try:
            proc = subprocess.run(
                [
                    "curl",
                    "-s",
                    "-m",
                    str(timeout_seconds),
                    ZAI_API_URL,
                    "-H",
                    f"Authorization: Bearer {api_key}",
                    "-H",
                    "Content-Type: application/json",
                    "-d",
                    payload,
                ],
                capture_output=True,
                text=True,
                timeout=timeout_seconds + 5,
            )
            raw = proc.stdout.strip()
            # Check for error response (e.g. code 1308 usage limit)
            if raw and '"error"' not in raw:
                raw_output = raw
                success = True
            else:
                logger.warning("zai API returned error or empty (%s); trying agy subscription route.", raw[:120])
        except Exception as exc:
            logger.warning("zai curl failed (%s); trying agy subscription route.", exc)

    # 2. Try agy subscription route if zai did not succeed
    if not success:
        try:
            proc_agy = subprocess.run(
                ["agy", "-p", prompt],
                capture_output=True,
                text=True,
                timeout=timeout_seconds + 10,
            )
            raw_agy = proc_agy.stdout.strip()
            if raw_agy and proc_agy.returncode == 0:
                raw_output = raw_agy
                used_model = "agy-gemini"
                success = True
            else:
                logger.warning("agy -p returned non-zero or empty (%s)", proc_agy.stderr[:120])
        except Exception as exc:
            logger.warning("agy invocation failed: %s", exc)

    if not success or not raw_output:
        return LLMDecisionResult(
            called=False,
            model=used_model,
            prompt_template_sha256=PROMPT_TEMPLATE_SHA256,
            prompt_input_summary=summary_text,
            raw_response=raw_output or "[ERROR: LLM endpoints unavailable or rate-limited]",
            parsed_intent="LLM_UNAVAILABLE_FALLBACK_RULE",
            weight_scale=1.0,
            attribution_label="RULE",
            reasoning="Fail-closed fallback: LLM APIs unavailable / rate-limited. Rule weights maintained.",
        )

    # 3. Parse JSON response
    try:
        # If response wrapped in choices (OpenAI / zai format)
        try:
            wrapper = json.loads(raw_output)
            if isinstance(wrapper, dict) and "choices" in wrapper:
                content = wrapper["choices"][0]["message"]["content"].strip()
            else:
                content = raw_output
        except Exception:
            content = raw_output

        cleaned = content
        if "```" in cleaned:
            parts = cleaned.split("```")
            cleaned = parts[1]
            if cleaned.startswith("json"):
                cleaned = cleaned[4:]
            cleaned = cleaned.strip()
        parsed = json.loads(cleaned)

        intent = str(parsed.get("intent", "HOLD_RULE")).upper()
        scale = float(parsed.get("weight_scale", 1.0))
        scale = max(0.0, min(1.20, scale))  # Bound between 0% and 120%
        reasoning = str(parsed.get("reasoning", "Rule accepted by LLM risk officer."))

        # Determine attribution label
        if intent == "HOLD_RULE" or abs(scale - 1.0) < 1e-4:
            attrib = "RULE"
            scale = 1.0
        elif 0.90 <= scale <= 1.10:
            attrib = "HYBRID"
        else:
            attrib = "LLM"

        return LLMDecisionResult(
            called=True,
            model=used_model,
            prompt_template_sha256=PROMPT_TEMPLATE_SHA256,
            prompt_input_summary=summary_text,
            raw_response=raw_output,
            parsed_intent=intent,
            weight_scale=scale,
            attribution_label=attrib,
            reasoning=reasoning,
        )

    except Exception as exc:
        logger.error("Failed to parse LLM response (%s): %s", exc, raw_output[:200])
        return LLMDecisionResult(
            called=False,
            model=used_model,
            prompt_template_sha256=PROMPT_TEMPLATE_SHA256,
            prompt_input_summary=summary_text,
            raw_response=raw_output,
            parsed_intent="LLM_PARSE_ERROR_FALLBACK_RULE",
            weight_scale=1.0,
            attribution_label="RULE",
            reasoning=f"Fail-closed fallback: Failed to parse LLM response ({exc}). Rule weights maintained.",
        )


def apply_decision_to_weights(
    rule_output: RuleOutput,
    decision: LLMDecisionResult,
) -> dict[str, float]:
    """Calculate final asset weights by applying LLM scale to rule weights."""
    scale = decision.weight_scale
    w_final_equity = min(1.0, max(0.0, rule_output.w_rule * scale))

    n_assets = len([k for k in rule_output.weights if k != "USD"])
    per_asset = w_final_equity / n_assets
    final_weights = {k: per_asset for k in rule_output.weights if k != "USD"}
    final_weights["USD"] = max(0.0, 1.0 - w_final_equity)
    return {k: round(v, 6) for k, v in final_weights.items()}


def _get_zai_api_key() -> str:
    if os.environ.get("ZAI_API_KEY"):
        return os.environ["ZAI_API_KEY"].strip()
    env_paths = [
        Path("/root/.hermes/.env"),
        Path("/root/.secrets/zai.key"),
    ]
    for p in env_paths:
        if p.exists():
            for line in p.read_text().splitlines():
                if line.startswith("ZAI_API_KEY="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""
