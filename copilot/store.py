"""Database access, case lifecycle, metrics and evaluation."""
import json
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime

from . import agent, config, data_gen, integrations


def connect():
    config.DATA.mkdir(exist_ok=True)
    fresh = not config.DB_PATH.exists()
    if fresh:
        data_gen.build(config.DB_PATH)
    con = sqlite3.connect(config.DB_PATH, check_same_thread=False)
    return con


def reset():
    if config.DB_PATH.exists():
        config.DB_PATH.unlink()
    out = integrations.OUTBOX
    if out.exists():
        for p in sorted(out.rglob("*"), reverse=True):
            p.unlink() if p.is_file() else p.rmdir()
    return connect()


def audit(con, dispute_id, event, detail=""):
    con.execute("INSERT INTO audit (ts, dispute_id, event, detail) VALUES (?,?,?,?)",
                (datetime.now().isoformat(timespec="seconds"), dispute_id, event,
                 detail if isinstance(detail, str) else json.dumps(detail, ensure_ascii=False)))
    con.commit()


def dispute(con, did):
    cur = con.execute("SELECT * FROM disputes WHERE id=?", (did,))
    row = cur.fetchone()
    return dict(zip([c[0] for c in cur.description], row)) if row else None


def disputes(con):
    cur = con.execute("""SELECT d.id, p.name, d.subject, d.received_at, d.status, c.result
                         FROM disputes d JOIN partners p ON p.id=d.partner_id
                         LEFT JOIN cases c ON c.dispute_id=d.id ORDER BY d.received_at DESC""")
    out = []
    for did, pname, subj, rec, status, result in cur.fetchall():
        r = json.loads(result) if result else None
        out.append({"id": did, "partner": pname, "subject": subj, "received_at": rec, "status": status,
                    "category": r and r["category"], "action": r and r["action"],
                    "escalate": r and r["escalate"], "confidence": r and r["confidence"]})
    return out


def run_case(con, did):
    result = agent.process(con, did)
    con.execute("INSERT OR REPLACE INTO cases (dispute_id, result, processed_at) VALUES (?,?,?)",
                (did, json.dumps(result, ensure_ascii=False), result["processed_at"]))
    con.execute("UPDATE disputes SET status='DRAFTED' WHERE id=?", (did,))
    con.commit()
    audit(con, did, "copilot_run", {k: result[k] for k in ("category", "action", "credit_eur", "confidence", "escalate", "mode", "usage")})
    if result["escalate"]:
        integrations.notify(f":rotating_light: Dispute {did} needs attention – {result['category']} / {result['action']} – "
                            f"{'; '.join(result['escalation_reasons'])}. Review: {config.APP_URL}/case/{did}")
    return result


def get_case(con, did):
    row = con.execute("SELECT result, final_reply, decision, decided_at, handling_seconds, jira_key FROM cases WHERE dispute_id=?",
                      (did,)).fetchone()
    if not row:
        return None
    return {"result": json.loads(row[0]), "final_reply": row[1], "decision": row[2],
            "decided_at": row[3], "handling_seconds": row[4], "jira_key": row[5]}


def safe_ticket(con, res, d, reply):
    """Create the Jira ticket; if Jira is unreachable or rejects it, keep a local copy and log why."""
    try:
        return integrations.create_ticket(res, d, reply)
    except Exception as e:
        audit(con, d["id"], "jira_error", f"{e.__class__.__name__}: {e}")
        from . import config
        saved = config.JIRA_BASE_URL
        try:
            config.JIRA_BASE_URL = None  # force local fallback for this one ticket
            return integrations.create_ticket(res, d, reply)
        finally:
            config.JIRA_BASE_URL = saved


def decide(con, did, decision, final_reply, reviewer_seconds):
    case = get_case(con, did)
    d = dispute(con, did)
    res = case["result"]
    final_reply = final_reply.replace("\r\n", "\n")
    edited = final_reply.strip() != res["draft"].replace("\r\n", "\n").strip()
    jira_key = None
    if decision == "APPROVED":
        integrations.send_reply(d, final_reply)
        jira_key, where = safe_ticket(con, res, d, final_reply)
        integrations.notify(f"Dispute {did} approved – {res['category']} → {res['action']}"
                            f"{' (credit EUR %s)' % res['credit_eur'] if res['credit_eur'] else ''}. Ticket {jira_key} for {res['team']}.")
        status = "CLOSED"
    elif decision == "TO_HUMAN":
        jira_key, where = safe_ticket(con, res, d, "(no reply sent – handed to a person)")
        integrations.notify(f"Dispute {did} handed to {res['team']} for manual handling. Ticket {jira_key}.")
        status = "WITH_TEAM"
    else:
        status = "REJECTED"
    con.execute("UPDATE cases SET final_reply=?, decision=?, decided_at=?, handling_seconds=?, jira_key=? WHERE dispute_id=?",
                (final_reply, decision, datetime.now().isoformat(timespec="seconds"), reviewer_seconds, jira_key, did))
    con.execute("UPDATE disputes SET status=? WHERE id=?", (status, did))
    con.commit()
    audit(con, did, f"human_{decision.lower()}", {"edited_draft": edited, "jira": jira_key, "review_seconds": reviewer_seconds})


def metrics(con):
    rows = con.execute("""SELECT c.result, c.decision, c.handling_seconds, d.partner_id, p.name
                          FROM cases c JOIN disputes d ON d.id=c.dispute_id JOIN partners p ON p.id=d.partner_id""").fetchall()
    m = {"processed": len(rows), "approved": 0, "to_human": 0, "rejected": 0, "edited": 0,
         "credits_eur": 0, "cost_usd": 0.0, "review_seconds": [], "agent_ms": [],
         "by_category": Counter(), "by_action": Counter(), "by_partner": Counter(),
         "by_grader": Counter(), "by_model": Counter(), "escalated": 0}
    for res, decision, secs, pid, pname in rows:
        r = json.loads(res)
        m["by_category"][r["category"]] += 1
        m["by_action"][r["action"]] += 1
        m["by_partner"][pname] += 1
        m["cost_usd"] += r["usage"]["cost_usd"]
        m["agent_ms"].append(r["elapsed_ms"])
        m["escalated"] += r["escalate"]
        dev = r["facts"]["device"]
        if r["action"] == "CORRECT_AND_CREDIT" and dev:
            m["credits_eur"] += r["credit_eur"]
            m["by_model"][dev["model"]] += 1
        if r["category"] == "GRADE_DISPUTE" and r["action"] == "CORRECT_AND_CREDIT" and dev:
            g = con.execute("SELECT grader_id FROM devices WHERE imei=?", (dev["imei"],)).fetchone()
            if g and g[0]:
                m["by_grader"][g[0]] += 1
        if decision:
            m[{"APPROVED": "approved", "TO_HUMAN": "to_human", "REJECTED": "rejected"}[decision]] += 1
            if secs:
                m["review_seconds"].append(secs)
    edits = con.execute("SELECT detail FROM audit WHERE event='human_approved'").fetchall()
    m["edited"] = sum(json.loads(e[0]).get("edited_draft", False) for e in edits)
    # Background regrade rate per grader across all graded devices (data quality signal)
    regrade = defaultdict(lambda: [0, 0])
    from . import rules
    for grader, defects, batt, fg in con.execute(
            "SELECT grader_id, defects, battery_health, final_grade FROM devices WHERE final_grade IS NOT NULL AND grader_id IS NOT NULL"):
        exp = rules.expected_grade(json.loads(defects), batt)
        regrade[grader][1] += 1
        if rules.GRADES.index(fg) > rules.GRADES.index(exp):
            regrade[grader][0] += 1
    m["grader_error_rate"] = {g: round(e / n * 100, 1) for g, (e, n) in sorted(regrade.items())}
    return m


def evaluate(con):
    """Run the copilot on every labelled dispute (without saving) and score it."""
    fields = ["category", "action", "credit", "team", "escalate", "language"]
    score = Counter()
    rows, confusion = [], Counter()
    cost = 0.0
    llm_failed, llm_ok, first_error = 0, 0, ""
    for did, label in con.execute("SELECT id, label FROM disputes ORDER BY id").fetchall():
        lab = json.loads(label)
        r = agent.process(con, did)
        cost += r["usage"]["cost_usd"]
        if r.get("llm_errors"):
            llm_failed += 1
            first_error = first_error or r["llm_errors"][0]
        if r["usage"]["calls"]:
            llm_ok += 1
        got = {"category": r["category"], "action": r["action"], "credit": r["credit_eur"],
               "team": r["team"].replace(" + Account Manager", ""), "escalate": r["escalate"],
               "language": r["reading"].get("language")}
        ok = {f: got[f] == lab[f] for f in fields}
        for f in fields:
            score[f] += ok[f]
        confusion[(lab["category"], r["category"])] += 1
        rows.append({"id": did, "expected": {f: lab[f] for f in fields}, "got": got, "ok": ok,
                     "all_ok": all(ok.values()), "confidence": r["confidence"]})
    n = len(rows)
    mode = "rules"
    if config.llm_enabled():
        mode = "llm" if llm_ok and not llm_failed else ("llm (partly failed)" if llm_ok else "llm FAILED - fell back to rules")
    return {"n": n, "mode": mode, "llm_failed": llm_failed, "llm_error": first_error,
            "accuracy": {f: round(score[f] / n * 100, 1) for f in fields},
            "fully_correct": round(sum(r["all_ok"] for r in rows) / n * 100, 1),
            "rows": rows, "confusion": confusion, "cost_usd": round(cost, 4)}
