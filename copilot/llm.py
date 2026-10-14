"""Minimal Claude Messages API client (standard library only)."""
import json
import re
import urllib.error
import urllib.request

from . import config

API_URL = "https://api.anthropic.com/v1/messages"


class LLMError(Exception):
    """A readable reason why a Claude call failed (never contains the API key)."""


STATUS = {"checked": False, "ok": None, "message": ""}


def _headers():
    h = {"x-api-key": config.ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01",
         "content-type": "application/json"}
    if config.ANTHROPIC_WORKSPACE_ID:
        h["anthropic-workspace-id"] = config.ANTHROPIC_WORKSPACE_ID
    return h


def available_models():
    """Model IDs this key can use, newest first (GET /v1/models)."""
    req = urllib.request.Request("https://api.anthropic.com/v1/models?limit=100", headers=_headers())
    with urllib.request.urlopen(req, timeout=30) as r:
        return [m["id"] for m in json.loads(r.read()).get("data", [])]


def check():
    """One tiny call at start-up so problems (bad key, no credits, wrong model) show immediately.
    If the configured model is not available to this key, switch to the newest available Sonnet."""
    u = Usage()
    try:
        chat("Reply with OK.", "OK?", u, max_tokens=5)
        STATUS.update(checked=True, ok=True, message="Claude API reachable")
        return STATUS
    except Exception as e:
        STATUS.update(checked=True, ok=False, message=str(e))
    if "HTTP 404" in STATUS["message"]:
        try:
            models = available_models()
        except Exception as e:
            STATUS["message"] += f" (could not list models: {e})"
            return STATUS
        pick = next((m for m in models if "sonnet" in m), models[0] if models else None)
        if pick:
            old, config.CLAUDE_MODEL = config.CLAUDE_MODEL, pick
            try:
                chat("Reply with OK.", "OK?", u, max_tokens=5)
                STATUS.update(ok=True, message=f"'{old}' is not available to this key; using '{pick}'. "
                                               f"Set CLAUDE_MODEL to it in config.json to make this permanent.")
            except Exception as e:
                STATUS.update(ok=False, message=f"{e}. Models available to this key: {', '.join(models[:8])}")
    return STATUS


class Usage:
    def __init__(self):
        self.input_tokens = 0
        self.output_tokens = 0
        self.calls = 0

    def add(self, u):
        self.input_tokens += u.get("input_tokens", 0)
        self.output_tokens += u.get("output_tokens", 0)
        self.calls += 1

    @property
    def cost_usd(self):
        return (self.input_tokens * config.PRICE_IN_PER_MTOK + self.output_tokens * config.PRICE_OUT_PER_MTOK) / 1_000_000

    def as_dict(self):
        return {"calls": self.calls, "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens, "cost_usd": round(self.cost_usd, 5)}


# Newer models reject sampling parameters such as temperature; we only send them if a model accepts them.
_SEND_TEMPERATURE = {"value": True}


def chat(system, user, usage, max_tokens=900, temperature=0):
    payload = {
        "model": config.CLAUDE_MODEL,
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }
    if _SEND_TEMPERATURE["value"]:
        payload["temperature"] = temperature
    try:
        return _send(payload, usage)
    except LLMError as e:
        if "temperature" in str(e) and "temperature" in payload:
            _SEND_TEMPERATURE["value"] = False  # remember for the rest of the session
            payload.pop("temperature")
            return _send(payload, usage)
        raise


def _send(payload, usage):
    body = json.dumps(payload).encode()
    req = urllib.request.Request(API_URL, data=body, method="POST", headers=_headers())
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read()).get("error", {}).get("message", "")
        except Exception:
            msg = ""
        raise LLMError(f"Claude API returned HTTP {e.code}: {msg or e.reason}") from None
    except urllib.error.URLError as e:
        raise LLMError(f"Could not reach the Claude API: {e.reason}") from None
    usage.add(data.get("usage", {}))
    return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")


def chat_json(system, user, usage, max_tokens=600):
    text = chat(system + "\nRespond with one JSON object only, no prose.", user, usage, max_tokens)
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError(f"No JSON in model output: {text[:200]}")
    return json.loads(m.group(0))
