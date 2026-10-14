"""Outbound actions: Jira ticket, Slack notification, sending the reply.

Each one uses the real service when configured, otherwise writes to
data/outbox so the demo works offline and every action is still visible.
"""
import base64
import json
import urllib.error
import urllib.request
from datetime import datetime

from . import config

OUTBOX = config.DATA / "outbox"


def _local(kind, name, payload):
    folder = OUTBOX / kind
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_text(payload if isinstance(payload, str) else json.dumps(payload, indent=2, ensure_ascii=False),
                    encoding="utf-8")
    return str(path.relative_to(config.ROOT))


def _post(url, payload, headers):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",
                                 headers={"content-type": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=20) as r:
        body = r.read()
        return json.loads(body) if body and body[:1] in b"{[" else {}


def create_ticket(case, dispute, final_reply):
    summary = f"[{case['category']}] {dispute['subject']} ({case['dispute_id']})"[:250]
    lines = [
        f"Team: {case['team']}",
        f"Recommended action: {case['action']}   Credit: EUR {case['credit_eur']}",
        f"Confidence: {case['confidence']}   Escalate: {'yes – ' + '; '.join(case['escalation_reasons']) if case['escalate'] else 'no'}",
        "",
        "Checks:",
        *[f"{'PASS' if c['ok'] else 'FAIL'} – {c['check']}: {c['detail']}" for c in case["checks"]],
        "",
        "Reply sent:",
        final_reply,
    ]
    if config.jira_enabled():
        base_team = case["team"].replace(" + Account Manager", "")
        labels = ["dispute-copilot", case["category"].lower(), case["action"].lower()]
        if case["escalate"]:
            labels.append("escalated")
        fields = {
            "project": {"key": config.JIRA_PROJECT_KEY},
            "issuetype": {"name": config.JIRA_ISSUE_TYPE},
            "summary": summary,
            "labels": labels,
            "description": {"type": "doc", "version": 1, "content": [
                {"type": "paragraph", "content": [{"type": "text", "text": ln or " "}]} for ln in lines]},
            "components": [{"name": base_team}],
            "priority": {"name": "High" if case["escalate"] else "Medium"},
        }
        auth = base64.b64encode(f"{config.JIRA_EMAIL}:{config.JIRA_API_TOKEN}".encode()).decode()
        url = config.JIRA_BASE_URL.rstrip("/") + "/rest/api/3/issue"
        headers = {"Authorization": f"Basic {auth}", "Accept": "application/json"}
        try:
            res = _post(url, {"fields": fields}, headers)
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise RuntimeError("Jira rejected the login: check JIRA_EMAIL and JIRA_API_TOKEN in config.json") from e
            # Project may not have the components/priority fields: retry with the basics only.
            for k in ("components", "priority"):
                fields.pop(k, None)
            res = _post(url, {"fields": fields}, headers)
        key = res.get("key", "?")
        return key, f"{config.JIRA_BASE_URL.rstrip('/')}/browse/{key}"
    key = f"LOCAL-{case['dispute_id']}"
    path = _local("jira", f"{key}.txt", f"{summary}\n\n" + "\n".join(lines))
    return key, path


def notify(text):
    if config.slack_enabled():
        _post(config.SLACK_WEBHOOK_URL, {"text": text}, {})
        return "slack"
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    return _local("slack", f"{stamp}.txt", text)


def send_reply(dispute, final_reply):
    # Demo: replies are never emailed; they are written to the outbox for review.
    return _local("email", f"{dispute['id']}.txt",
                  f"To: {dispute['sender']}\nSubject: Re: {dispute['subject']}\n\n{final_reply}")
