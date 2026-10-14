"""Minimal Claude Messages API client (standard library only)."""
import json
import re
import urllib.request

from . import config

API_URL = "https://api.anthropic.com/v1/messages"


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


def chat(system, user, usage, max_tokens=900, temperature=0):
    body = json.dumps({
        "model": config.CLAUDE_MODEL,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }).encode()
    req = urllib.request.Request(API_URL, data=body, method="POST", headers={
        "x-api-key": config.ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    })
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.loads(r.read())
    usage.add(data.get("usage", {}))
    return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")


def chat_json(system, user, usage, max_tokens=600):
    text = chat(system + "\nRespond with one JSON object only, no prose.", user, usage, max_tokens)
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError(f"No JSON in model output: {text[:200]}")
    return json.loads(m.group(0))
