"""Local web app (standard library only): inbox, case review, dashboard, evaluation, audit log."""
import html
import json
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock
from urllib.parse import parse_qs, urlparse

from . import config, store

CON = None
LOCK = Lock()
E = html.escape

CSS = """
:root{--bg:#f6f7f9;--card:#fff;--ink:#1d2433;--muted:#5d6678;--line:#e3e6ec;--accent:#0f766e;--accent-soft:#e6f4f2;
--ok:#15803d;--bad:#b91c1c;--warn:#b45309;--chip:#eef1f5}
*{box-sizing:border-box}body{margin:0;font:15px/1.5 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;background:var(--bg);color:var(--ink)}
header{background:#132033;color:#fff;padding:14px 24px;display:flex;gap:24px;align-items:center;flex-wrap:wrap}
header b{font-size:17px}header a{color:#cfd8e3;text-decoration:none;font-size:14px}header a:hover{color:#fff}
.mode{margin-left:auto;font-size:12px;background:#24344d;padding:4px 10px;border-radius:99px}
main{max-width:1180px;margin:0 auto;padding:24px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:18px 20px;margin-bottom:18px}
h1{font-size:22px;margin:0 0 4px}h2{font-size:16px;margin:0 0 12px}.muted{color:var(--muted)}
table{width:100%;border-collapse:collapse;font-size:14px}th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--muted);font-weight:600;font-size:12px;text-transform:uppercase;letter-spacing:.03em}
.chip{display:inline-block;padding:2px 9px;border-radius:99px;font-size:12px;background:var(--chip);white-space:nowrap}
.chip.ok{background:#e7f6ec;color:var(--ok)}.chip.bad{background:#fdecec;color:var(--bad)}.chip.warn{background:#fff4e5;color:var(--warn)}
.chip.acc{background:var(--accent-soft);color:var(--accent)}
.btn{display:inline-block;border:0;border-radius:8px;padding:9px 16px;font-size:14px;cursor:pointer;text-decoration:none;background:var(--accent);color:#fff}
.btn.sec{background:#fff;color:var(--ink);border:1px solid var(--line)}.btn.danger{background:#fff;color:var(--bad);border:1px solid #f1c7c7}
.btn.small{padding:5px 11px;font-size:13px}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:18px}@media(max-width:900px){.grid{grid-template-columns:1fr}}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:14px;margin-bottom:18px}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px}.kpi .v{font-size:24px;font-weight:650}.kpi .l{color:var(--muted);font-size:13px}
pre.mail{white-space:pre-wrap;font-family:inherit;font-size:14px;background:#fafbfc;border:1px solid var(--line);border-radius:8px;padding:12px;margin:0}
textarea{width:100%;min-height:230px;font-family:inherit;font-size:14px;line-height:1.5;border:1px solid var(--line);border-radius:8px;padding:12px}
.decision{border-left:4px solid var(--accent);padding-left:14px}
.bar{height:8px;background:var(--accent);border-radius:4px}
.steps li{margin-bottom:4px}.k{color:var(--muted);width:170px}
"""


def page(title, body):
    from . import llm
    if not config.llm_enabled():
        mode = "Offline mode (rules)"
    elif llm.STATUS["checked"] and not llm.STATUS["ok"]:
        mode = "Claude NOT working – " + llm.STATUS["message"][:90]
    else:
        mode = "Claude: " + config.CLAUDE_MODEL
    integ = []
    missing = config.jira_missing()
    integ.append("Jira" if not missing else ("Jira: local" if len(missing) == 4 else "Jira: local – missing " + ", ".join(missing)))
    integ.append("Slack" if config.slack_enabled() else "Slack: local")
    return f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{E(title)} · Dispute Copilot</title><style>{CSS}</style></head><body>
<header><b>Dispute Copilot</b><a href="/">Inbox</a><a href="/dashboard">Dashboard</a><a href="/eval">Evaluation</a><a href="/audit">Audit log</a>
<span class="mode">{E(mode)} · {E(' · '.join(integ))}</span></header><main>{body}</main></body></html>"""


def chip(text, kind=""):
    return f'<span class="chip {kind}">{E(str(text))}</span>'


ACTION_KIND = {"UPHOLD": "acc", "CORRECT_AND_CREDIT": "warn", "REQUEST_INFO": "", "INVESTIGATE": "", "MANUAL_REVIEW": "bad"}
STATUS_KIND = {"NEW": "", "DRAFTED": "acc", "CLOSED": "ok", "WITH_TEAM": "warn", "REJECTED": "bad"}


# ------------------------------------------------------------------ views
def inbox():
    rows = store.disputes(CON)
    n_new = sum(r["status"] == "NEW" for r in rows)
    trs = []
    for r in rows:
        act = (f'<a class="btn small" href="/case/{r["id"]}">Open</a>' if r["status"] != "NEW" else
               f'<form method="post" action="/run/{r["id"]}" style="margin:0"><button class="btn small">Run copilot</button></form>')
        trs.append(f"""<tr><td>{E(r['id'])}</td><td>{E(r['received_at'])}</td><td>{E(r['partner'])}</td><td>{E(r['subject'])}</td>
<td>{chip(r['status'], STATUS_KIND.get(r['status'], ''))}</td>
<td>{chip(r['category']) if r['category'] else ''} {chip(r['action'], ACTION_KIND.get(r['action'], '')) if r['action'] else ''}
{chip('escalate', 'bad') if r['escalate'] else ''}</td><td>{act}</td></tr>""")
    return page("Inbox", f"""
<div class="card"><h1>Partner dispute inbox</h1>
<p class="muted">Synthetic trade-in disputes from mobile operator and retail partners. The copilot reads each email, checks the device record,
recommends a decision and drafts a reply. A person approves before anything is sent.</p>
<form method="post" action="/run-all" style="display:inline"><button class="btn">Run copilot on all new ({n_new})</button></form>
<form method="post" action="/reset" style="display:inline;margin-left:8px" onsubmit="return confirm('Reset all demo data?')"><button class="btn danger">Reset demo</button></form></div>
<div class="card"><table><tr><th>ID</th><th>Received</th><th>Partner</th><th>Subject</th><th>Status</th><th>Copilot</th><th></th></tr>{''.join(trs)}</table></div>""")


def case_view(did):
    d = store.dispute(CON, did)
    c = store.get_case(CON, did)
    if not d:
        return page("Not found", "<div class='card'>Unknown dispute.</div>")
    if not c:
        return page(did, f"""<div class="card"><h1>{E(d['subject'])}</h1><pre class="mail">{E(d['body'])}</pre>
<form method="post" action="/run/{did}" style="margin-top:12px"><button class="btn">Run copilot</button></form></div>""")
    r = c["result"]
    checks = "".join(f"<tr><td>{chip('PASS', 'ok') if k['ok'] else chip('FAIL', 'bad')}</td><td>{E(k['check'])}</td><td class='muted'>{E(str(k['detail']))}</td></tr>"
                     for k in r["checks"])
    dev = r["facts"]["device"]
    devrows = "".join(f"<tr><td class='k'>{E(k)}</td><td>{E(', '.join(v) if isinstance(v, list) else str(v))}</td></tr>"
                      for k, v in (dev or {}).items()) or "<tr><td>No matching device</td></tr>"
    steps = "".join(f"<li><b>{E(s['step'])}</b> · {E(s['detail'])} <span class='muted'>({s['ms']} ms)</span></li>" for s in r["steps"])
    llm_warn = ("<p>" + chip("Claude failed", "bad") + " " + E(r["llm_errors"][0]) + "</p>") if r.get("llm_errors") else ""
    esc = llm_warn + ("<p>" + chip("Escalate", "bad") + " " + E("; ".join(r["escalation_reasons"])) + "</p>") if r["escalate"] else ""
    conf_kind = "ok" if r["confidence"] >= 0.8 else "warn" if r["confidence"] >= 0.6 else "bad"
    u = r["usage"]
    cost = f"{u['calls']} model calls · {u['input_tokens']}+{u['output_tokens']} tokens · ${u['cost_usd']:.4f}" if u["calls"] else "No model calls (offline mode) · $0"
    if c["decision"]:
        action_box = f"""<div class="card"><h2>Decision recorded</h2><p>{chip(c['decision'], 'ok' if c['decision']=='APPROVED' else 'warn')}
 at {E(c['decided_at'] or '')} · review time {c['handling_seconds'] or 0:.0f}s · ticket {E(c['jira_key'] or '-')}</p>
<pre class="mail">{E(c['final_reply'] or '')}</pre></div>"""
    else:
        action_box = f"""<div class="card"><h2>Draft reply <span class="muted" style="font-weight:400">(edit before approving)</span></h2>
<form method="post" action="/decide/{did}"><input type="hidden" name="opened" value="{time.time():.0f}">
<textarea name="reply">{E(r['draft'])}</textarea>
<p style="display:flex;gap:8px;flex-wrap:wrap"><button class="btn" name="decision" value="APPROVED">Approve &amp; send</button>
<button class="btn sec" name="decision" value="TO_HUMAN">Hand to {E(r['team'])}</button>
<button class="btn danger" name="decision" value="REJECTED">Reject draft</button>
<button class="btn sec" formaction="/run/{did}">Re-run copilot</button></p></form></div>"""
    return page(did, f"""
<p><a href="/">← Inbox</a></p>
<div class="grid">
 <div class="card"><h2>Partner email · {E(d['id'])}</h2><p class="muted">{E(d['sender'])} · {E(d['received_at'])}</p>
  <p><b>{E(d['subject'])}</b></p><pre class="mail">{E(d['body'])}</pre></div>
 <div class="card decision"><h2>Copilot recommendation</h2>
  <p>{chip(r['category'])} {chip(r['action'], ACTION_KIND.get(r['action'], ''))}
  {chip('Credit EUR ' + str(r['credit_eur']), 'warn') if r['credit_eur'] else ''} {chip('Confidence ' + str(r['confidence']), conf_kind)}</p>
  <p><span class="muted">Route to:</span> <b>{E(r['team'])}</b></p>{esc}
  <p class="muted" style="font-size:13px">{E(r['reading'].get('summary') or '')}</p>
  <p class="muted" style="font-size:13px">{E(cost)} · {r['elapsed_ms']} ms</p></div>
</div>
<div class="grid">
 <div class="card"><h2>Checks against the data</h2><table>{checks}</table></div>
 <div class="card"><h2>Device record</h2><table>{devrows}</table></div>
</div>
{action_box}
<div class="card"><h2>Trace</h2><ol class="steps">{steps}</ol></div>""")


def bars(counter, unit=""):
    if not counter:
        return "<p class='muted'>No data yet.</p>"
    top = max(counter.values())
    return "<table>" + "".join(
        f"<tr><td style='width:42%'>{E(str(k))}</td><td><div class='bar' style='width:{v / top * 100:.0f}%'></div></td><td style='width:60px'>{v}{unit}</td></tr>"
        for k, v in counter.most_common()) + "</table>"


def dashboard():
    m = store.metrics(CON)
    decided = m["approved"] + m["to_human"] + m["rejected"]
    avg_review = sum(m["review_seconds"]) / len(m["review_seconds"]) if m["review_seconds"] else 0
    avg_agent = sum(m["agent_ms"]) / len(m["agent_ms"]) if m["agent_ms"] else 0
    cpc = m["cost_usd"] / m["processed"] if m["processed"] else 0
    from collections import Counter
    ger = Counter({g: v for g, v in m["grader_error_rate"].items()})
    kp = lambda v, l: f"<div class='kpi'><div class='v'>{v}</div><div class='l'>{l}</div></div>"
    return page("Dashboard", f"""
<h1>Dashboard</h1><p class="muted">Value case inputs: volume, speed, quality of drafts, credits and root causes.</p>
<div class="kpis">{kp(m['processed'], 'cases processed')}{kp(f"{decided}", 'decided by a person')}
{kp(f"{(m['approved'] - m['edited']) / m['approved'] * 100:.0f}%" if m['approved'] else '–', 'approved without edits')}
{kp(f"{avg_review:.0f}s", 'avg review time per case')}{kp(f"{avg_agent:.0f} ms", 'avg copilot time')}
{kp(f"€{m['credits_eur']}", 'credits recommended')}{kp(m['escalated'], 'escalated')}{kp(f"${cpc:.4f}", 'model cost per case')}</div>
<div class="grid">
 <div class="card"><h2>Cases by type</h2>{bars(m['by_category'])}</div>
 <div class="card"><h2>Recommended actions</h2>{bars(m['by_action'])}</div>
 <div class="card"><h2>Disputes by partner</h2>{bars(m['by_partner'])}</div>
 <div class="card"><h2>Our errors (credits) by device model</h2>{bars(m['by_model'])}</div>
 <div class="card"><h2>Grade corrections by grader</h2>{bars(m['by_grader'])}
  <p class="muted" style="font-size:13px">Root cause signal for the Quality Control team lead — used to target coaching and re-checks, not to rank people.</p></div>
 <div class="card"><h2>Background over-grading rate by grader (all devices, %)</h2>{bars(ger, '%')}</div>
</div>""")


def eval_view(run=False):
    if not run:
        return page("Evaluation", f"""<div class="card"><h1>Evaluation</h1>
<p class="muted">Runs the copilot on all labelled disputes without saving anything and compares each result with the expected answer
(category, action, credit, team, escalation, reply language). Use it before every change to rules or prompts.</p>
<form method="post" action="/eval"><button class="btn">Run evaluation</button></form></div>""")
    e = store.evaluate(CON)
    acc = "".join(f"<div class='kpi'><div class='v'>{v}%</div><div class='l'>{E(k)}</div></div>" for k, v in e["accuracy"].items())
    rows = "".join(
        f"<tr><td>{E(r['id'])}</td><td>{'' if r['all_ok'] else chip('miss', 'bad')}</td>"
        + "".join(f"<td>{E(str(r['got'][k]))}" + ("" if r['ok'][k] else f"<br><span class='muted'>exp. {E(str(r['expected'][k]))}</span>") + "</td>"
                  for k in ["category", "action", "credit", "escalate", "language"])
        + f"<td>{r['confidence']}</td></tr>" for r in e["rows"])
    warn = (f"<p>{chip('Claude not used', 'bad')} {e['llm_failed']} of {e['n']} cases fell back to rules. Reason: <b>{E(e['llm_error'])}</b></p>"
            if e.get("llm_failed") else "")
    return page("Evaluation", f"""<div class="card"><h1>Evaluation · {e['n']} labelled cases · {E(e['mode'])} mode</h1>{warn}
<p>Fully correct: <b>{e['fully_correct']}%</b> · model cost for the run: ${e['cost_usd']}</p></div>
<div class="kpis">{acc}</div>
<div class="card"><table><tr><th>ID</th><th></th><th>Category</th><th>Action</th><th>Credit</th><th>Escalate</th><th>Language</th><th>Conf.</th></tr>{rows}</table></div>""")


def audit_view():
    rows = CON.execute("SELECT ts, dispute_id, event, detail FROM audit ORDER BY id DESC LIMIT 300").fetchall()
    trs = "".join(f"<tr><td>{E(ts)}</td><td><a href='/case/{E(d)}'>{E(d)}</a></td><td>{chip(ev)}</td><td class='muted' style='font-size:13px'>{E(det)}</td></tr>"
                  for ts, d, ev, det in rows)
    return page("Audit log", f"<div class='card'><h1>Audit log</h1><p class='muted'>Every copilot run and every human decision.</p><table>{trs}</table></div>")


# ------------------------------------------------------------------ server
class Handler(BaseHTTPRequestHandler):
    def _send(self, body, status=200):
        b = body.encode()
        self.send_response(status)
        self.send_header("content-type", "text/html; charset=utf-8")
        self.send_header("content-length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _redirect(self, to):
        self.send_response(303)
        self.send_header("location", to)
        self.end_headers()

    def log_message(self, *a):
        pass

    def do_GET(self):
        p = urlparse(self.path).path
        try:
            with LOCK:
                if p == "/":
                    return self._send(inbox())
                if p.startswith("/case/"):
                    return self._send(case_view(p.split("/")[2]))
                if p == "/dashboard":
                    return self._send(dashboard())
                if p == "/eval":
                    return self._send(eval_view())
                if p == "/audit":
                    return self._send(audit_view())
            self._send(page("Not found", "<div class='card'>Not found</div>"), 404)
        except Exception:
            self._send(page("Error", f"<pre>{E(traceback.format_exc())}</pre>"), 500)

    def do_POST(self):
        global CON
        p = urlparse(self.path).path
        n = int(self.headers.get("content-length") or 0)
        form = {k: v[0] for k, v in parse_qs(self.rfile.read(n).decode()).items()}
        try:
            with LOCK:
                if p.startswith("/run/"):
                    did = p.split("/")[2]
                    store.run_case(CON, did)
                    return self._redirect(f"/case/{did}")
                if p == "/run-all":
                    for r in store.disputes(CON):
                        if r["status"] == "NEW":
                            store.run_case(CON, r["id"])
                    return self._redirect("/")
                if p.startswith("/decide/"):
                    did = p.split("/")[2]
                    secs = max(0, time.time() - float(form.get("opened", time.time())))
                    store.decide(CON, did, form.get("decision", "REJECTED"), form.get("reply", ""), secs)
                    return self._redirect(f"/case/{did}")
                if p == "/eval":
                    return self._send(eval_view(run=True))
                if p == "/reset":
                    CON.close()
                    CON = store.reset()
                    return self._redirect("/")
            self._send(page("Not found", "<div class='card'>Not found</div>"), 404)
        except Exception:
            self._send(page("Error", f"<pre>{E(traceback.format_exc())}</pre>"), 500)


def serve(open_browser=True):
    global CON
    CON = store.connect()
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", config.PORT), Handler)
    except OSError:
        print(f"Port {config.PORT} is already in use. Close the other window running the app, "
              f"or set a different PORT in config.json.")
        return
    url = f"http://localhost:{config.PORT}"
    mode = "Claude " + config.CLAUDE_MODEL if config.llm_enabled() else "offline rules mode"
    print(f"Dispute Copilot is running at {url} ({mode}).")
    if config.llm_enabled():
        from . import llm
        st = llm.check()
        print(("Claude check: OK - " if st["ok"] else "Claude check FAILED: ") + st["message"])
    print("Keep this window open while you use the app. Press Ctrl+C to stop.")
    if open_browser:
        import threading
        import webbrowser
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("Stopped.")
