"""Measure the material packet's own token count against the §9 ceiling.

    python3 -m ops.shadow_llm.measure_materials

Sends the packet alone (no instructions, no schema) to the configured free
model with a tiny completion budget, so the provider's ``usage.prompt_tokens``
is the token count of the materials themselves.  Nothing is written to the
journal: this is a measurement, not a decision tick.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

from . import config, materials

CEILING = materials.TOKEN_BUDGET


def main() -> int:
    cfg = config.load()
    packet = materials.build(offline=False)
    payload = json.dumps(packet, ensure_ascii=False, separators=(",", ":"))
    body = json.dumps({
        "model": cfg["model"],
        "messages": [{"role": "user", "content": payload}],
        "max_tokens": 8,
        "temperature": 0.0,
    }).encode("utf-8")
    req = urllib.request.Request(cfg["endpoint"], data=body, headers={
        "Authorization": "Bearer " + str(cfg["api_key"]),
        "Content-Type": "application/json",
        "User-Agent": "shadow-llm/0.1",
    })
    try:
        with urllib.request.urlopen(req, timeout=cfg["timeout_seconds"]) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as exc:
        print(f"HTTP {exc.code}: {exc.read()[:200].decode('utf-8', 'replace')}", file=sys.stderr)
        return 2
    usage = data.get("usage") or {}
    tokens = usage.get("prompt_tokens")
    result = {
        "model": cfg["model"],
        "free_lane": cfg["model"].endswith(":free"),
        "materials_bytes": len(payload.encode("utf-8")),
        "materials_tokens_prompt": tokens,
        "local_upper_bound_estimate": materials.estimate_tokens(packet),
        "ceiling": CEILING,
        "within_ceiling": bool(tokens is not None and tokens <= CEILING),
        "cost_usd": usage.get("cost"),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["within_ceiling"] else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
