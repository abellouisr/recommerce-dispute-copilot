"""The dispute copilot pipeline.

read email -> find device -> verify against data -> decide -> draft reply -> route

Design choice: the language model reads and writes (understanding the email,
drafting the reply), while the decision itself is made by deterministic checks
against the device record. That keeps decisions explainable and testable, and
the model can never invent a grade or a credit amount.
"""
import json
import re
import time
from datetime import datetime

from . import config, rules
from .llm import Usage, chat, chat_json

IMEI_RE = re.compile(r"\b(\d{15})\b")
ORDER_RE = re.compile(r"\b(TRD-\d{7})\b", re.I)
GRADE_CLAIM_RE = re.compile(r"(?:grade|zustand)\s+([ABCD])\b", re.I)

TEXT_KEYWORDS = {
    "LOCK": ["icloud", "find my", "activation lock", "mdm", "on hold", "blocked", "gesperrt", "sperre"],
    "MISSING_DEVICE": ["not received", "never received", "not arrived", "missing", "nicht angekommen", "nicht erhalten"],
    "IMEI_MISMATCH": ["rejected", "wrong imei", "imei does not match", "abgelehnt"],
    "PRICE_CHANGE": ["price", "payout lower", "amount", "calculated", "preis", "betrag", "berechnet", "quote was"],
    "GRADE_DISPUTE": ["grade", "grading", "condition", "downgraded", "bewertung", "zustand"],
}


# ------------------------------------------------------------------ helpers
def luhn_ok(imei):
    total = 0
    for i, ch in enumerate(imei[:-1]):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return (10 - total % 10) % 10 == int(imei[-1])


def rules_read(subject, body):
    """Offline reader: regex + keyword scoring."""
    text = f"{subject}\n{body}".lower()
    scores = {c: sum(text.count(k) for k in ks) for c, ks in TEXT_KEYWORDS.items()}
    # Grade words appear in price emails too ("grade B"), so price wins ties only if explicitly asked.
    best = max(scores, key=lambda c: (scores[c], c == "LOCK", c == "MISSING_DEVICE"))
    category = best if scores[best] > 0 else "OTHER"
    de_markers = ["hallo", "bitte", "grüße", "auftrag", "zustand", "gerät"]
    lang = "de" if sum(m in text for m in de_markers) >= 2 else "en"
    claim = GRADE_CLAIM_RE.search(body)
    return {
        "category": category,
        "language": lang,
        "claimed_grade": claim.group(1).upper() if claim else None,
        "summary": subject,
        "mode": "rules",
    }


def llm_read(subject, body, usage):
    system = ("You triage partner emails for a phone trade-in (recommerce) business. "
              f"Classify the email into one category: {', '.join(rules.CATEGORIES)}. "
              "LOCK = device on hold because of iCloud/Find My or MDM; MISSING_DEVICE = partner says we did not receive it; "
              "IMEI_MISMATCH = partner says device was rejected or wrong IMEI; PRICE_CHANGE = question about the payout amount; "
              "GRADE_DISPUTE = disagrees with the condition grade.")
    user = (f"Subject: {subject}\n\n{body}\n\n"
            'Return JSON: {"category": str, "language": "en"|"de"|other ISO code, '
            '"claimed_grade": "A"|"B"|"C"|"D"|null, "summary": one sentence in English}')
    out = chat_json(system, user, usage)
    out["mode"] = "llm"
    if out.get("category") not in rules.CATEGORIES:
        out["category"] = "OTHER"
    return out


def find_device(con, imeis, orders):
    by_imei = [con.execute("SELECT * FROM devices WHERE imei=?", (i,)).fetchone() for i in imeis]
    by_order = [con.execute("SELECT * FROM devices WHERE order_id=?", (o.upper(),)).fetchone() for o in orders]
    found = [d for d in by_imei + by_order if d]
    return found


def row_to_dict(cur_row, cursor_desc):
    return {d[0]: v for d, v in zip(cursor_desc, cur_row)}


# ------------------------------------------------------------------ pipeline
def process(con, dispute_id):
    t0 = time.perf_counter()
    usage = Usage()
    steps = []
    llm_errors = []

    def step(name, detail):
        steps.append({"step": name, "detail": detail, "ms": round((time.perf_counter() - t0) * 1000)})

    con.row_factory = None
    cur = con.execute("SELECT * FROM disputes WHERE id=?", (dispute_id,))
    disp = row_to_dict(cur.fetchone(), cur.description)
    partner = con.execute("SELECT name, type, country FROM partners WHERE id=?", (disp["partner_id"],)).fetchone()

    # 1. Read the email
    imeis = list(dict.fromkeys(IMEI_RE.findall(disp["subject"] + " " + disp["body"])))
    orders = list(dict.fromkeys(o.upper() for o in ORDER_RE.findall(disp["subject"] + " " + disp["body"])))
    reading = None
    if config.llm_enabled():
        try:
            reading = llm_read(disp["subject"], disp["body"], usage)
        except Exception as e:  # fall back, never fail the case
            llm_errors.append(str(e))
            step("read", f"Claude read failed ({e}); using rules")
    if reading is None:
        reading = rules_read(disp["subject"], disp["body"])
    step("read", f"Text category {reading['category']} ({reading['mode']}), language {reading['language']}, "
                 f"IMEIs {imeis or '-'}, orders {orders or '-'}")

    checks = []

    def check(name, ok, detail):
        checks.append({"check": name, "ok": ok, "detail": detail})

    for i in imeis:
        check("IMEI checksum valid", luhn_ok(i), i)

    # 2. Find the device
    cur = con.execute("SELECT * FROM devices LIMIT 0")
    cols = [c[0] for c in cur.description]
    found = find_device(con, imeis, orders)
    devices = {r[0]: dict(zip(cols, r)) for r in found}
    confidence = 0.95
    dev = None
    if not devices:
        check("Device found in trade-in records", False, "No match for IMEI or order number")
        confidence = 0.3
    else:
        if len(devices) > 1:
            check("IMEI and order number point to the same device", False, ", ".join(devices))
            confidence -= 0.35
        dev = list(devices.values())[0]
        dev["defects"] = json.loads(dev["defects"])
        check("Device found in trade-in records", True, f"{dev['model']} · order {dev['order_id']}")
        same_partner = dev["partner_id"] == disp["partner_id"]
        check("Device belongs to the sending partner", same_partner,
              "Yes" if same_partner else f"Belongs to {dev['partner_id']}, sender is {disp['partner_id']}")
        if not same_partner:
            confidence = min(confidence, 0.4)
            dev = None  # never act on another partner's device
    step("lookup", f"{len(devices)} device(s) matched")

    # 3. Verify against data and decide
    category, action, credit, correct_grade = reading["category"], "MANUAL_REVIEW", 0, None
    reasons = []
    if dev:
        hard = None
        if not dev["received"]:
            hard = "MISSING_DEVICE"
            check("Device received at warehouse", False, "No goods-in scan")
        else:
            check("Device received at warehouse", True, "Goods-in scan found")
            if dev["received_imei"] and dev["received_imei"] != dev["imei"]:
                hard = "IMEI_MISMATCH"
                check("Scanned IMEI matches registered IMEI", False, f"Scanned {dev['received_imei']}")
            else:
                check("Scanned IMEI matches registered IMEI", True, "Match")
            if dev["fmi_lock"] or dev["mdm_lock"]:
                hard = "LOCK"
            check("No iCloud / Find My lock", not dev["fmi_lock"], "Locked" if dev["fmi_lock"] else "Clear")
            check("No MDM enrolment", not dev["mdm_lock"], "Enrolled" if dev["mdm_lock"] else "Clear")

        if hard:
            if reading["category"] not in (hard, "OTHER", "PRICE_CHANGE"):
                confidence -= 0.1  # text and data tell different stories
            category = hard
            action = "REQUEST_INFO" if hard == "LOCK" else "INVESTIGATE"
        else:
            if category not in ("GRADE_DISPUTE", "PRICE_CHANGE"):
                confidence -= 0.25
                category = "GRADE_DISPUTE" if dev["final_grade"] != dev["quoted_grade"] else "PRICE_CHANGE"
            correct_grade = rules.expected_grade(dev["defects"], dev["battery_health"])
            grade_ok = rules.GRADES.index(dev["final_grade"]) <= rules.GRADES.index(correct_grade)
            check("Assigned grade consistent with recorded defects", grade_ok,
                  f"Assigned {dev['final_grade']}, defects support {correct_grade}: "
                  f"{', '.join(dev['defects']) or 'no defects'}; battery {dev['battery_health']}%")
            correct_price = rules.price_for(dev["model"], correct_grade if not grade_ok else dev["final_grade"])
            price_ok = dev["final_price"] >= correct_price
            check("Payout matches current price table", price_ok,
                  f"Paid EUR {dev['final_price']}, table EUR {correct_price}")
            credit = max(0, correct_price - dev["final_price"])
            action = "CORRECT_AND_CREDIT" if credit > 0 else "UPHOLD"
            if grade_ok:
                correct_grade = dev["final_grade"]
    else:
        category = reading["category"] if reading["category"] != "OTHER" else "OTHER"
        action = "MANUAL_REVIEW"

    # 4. Escalation policy
    n_partner = con.execute("SELECT COUNT(*) FROM disputes WHERE partner_id=?", (disp["partner_id"],)).fetchone()[0]
    if n_partner >= rules.ESCALATE_PARTNER_DISPUTES:
        reasons.append(f"{n_partner} disputes from {partner[0]} in the last 30 days")
    value = max(dev["quoted_price"] if dev else 0, credit)
    if value > rules.ESCALATE_VALUE_EUR:
        reasons.append(f"High value (EUR {value})")
    words = [w for w in rules.ESCALATION_WORDS if w in disp["body"].lower()]
    if words:
        reasons.append(f"Escalation language: {', '.join(words)}")
    confidence = round(max(0.05, min(confidence, 0.99)), 2)
    if confidence < rules.ESCALATE_MIN_CONFIDENCE:
        reasons.append(f"Low confidence ({confidence})")
    team = rules.TEAM.get(category, rules.TEAM["OTHER"])
    step("decide", f"{category} → {action}, credit EUR {credit}, team {team}, escalate {bool(reasons)}")

    # 5. Draft reply
    facts = {
        "partner": partner[0], "contact": disp["sender"].split("<")[0].strip(),
        "category": category, "action": action, "credit_eur": credit,
        "device": None if not dev else {
            "model": dev["model"], "imei": dev["imei"], "order": dev["order_id"],
            "quoted_grade": dev["quoted_grade"], "assigned_grade": dev["final_grade"],
            "corrected_grade": correct_grade, "defects": dev["defects"],
            "battery_health": dev["battery_health"], "paid_eur": dev["final_price"],
            "fmi_lock": bool(dev["fmi_lock"]), "mdm_lock": bool(dev["mdm_lock"]),
        },
    }
    lang = reading.get("language") or disp["lang"]
    draft = None
    if config.llm_enabled():
        try:
            draft = llm_draft(disp, facts, lang, usage)
            step("draft", "Reply drafted by Claude")
        except Exception as e:
            llm_errors.append(str(e))
            step("draft", f"Claude draft failed ({e}); using template")
    if draft is None:
        draft = template_draft(facts, lang)
        step("draft", f"Reply drafted from template ({lang})")

    return {
        "dispute_id": dispute_id,
        "mode": ("llm" if usage.calls else "llm-failed") if config.llm_enabled() else "rules",
        "llm_errors": llm_errors,
        "reading": reading,
        "category": category, "action": action, "credit_eur": credit,
        "team": team + (" + Account Manager" if reasons else ""),
        "escalate": bool(reasons), "escalation_reasons": reasons,
        "confidence": confidence,
        "checks": checks, "facts": facts, "draft": draft,
        "steps": steps, "usage": usage.as_dict(),
        "elapsed_ms": round((time.perf_counter() - t0) * 1000),
        "processed_at": datetime.now().isoformat(timespec="seconds"),
    }


# ------------------------------------------------------------------ drafting
def llm_draft(disp, facts, lang, usage):
    system = ("You write short, professional replies to B2B trade-in partners on behalf of the Trade-in Support Team. "
              "Use ONLY the facts provided; never invent grades, prices, dates or promises. "
              "State the decision clearly, give the evidence, and the next step. Max 140 words. "
              "Write in the same language as the partner's email. Plain text, no markdown.")
    user = (f"Partner email:\nSubject: {disp['subject']}\n{disp['body']}\n\n"
            f"Facts and decision (authoritative):\n{json.dumps(facts, ensure_ascii=False, indent=1)}\n\n"
            f"Reply language: {lang}. Sign as 'Trade-in Support Team'.")
    return chat(system, user, usage, max_tokens=500).strip()


def template_draft(f, lang):
    d = f["device"] or {}
    name = f["contact"].split()[0] if f["contact"] else ""
    a, c = f["action"], f["category"]
    if lang == "de":
        hi, bye = f"Hallo {name},", "Viele Grüße\nTrade-in Support Team"
        ref = f"{d.get('model', '')} (Auftrag {d.get('order', '?')}, IMEI {d.get('imei', '?')})"
        msg = {
            ("LOCK", "REQUEST_INFO"): f"das {ref} ist {'mit iCloud / Wo ist? gesperrt' if d.get('fmi_lock') else 'noch in einer MDM-Verwaltung registriert'}. "
                                      "Bitte lassen Sie die Sperre durch den Kunden entfernen. Danach prüfen wir das Gerät erneut und geben die Auszahlung frei. Wir halten das Gerät 14 Tage.",
            ("IMEI_MISMATCH", "INVESTIGATE"): f"beim Wareneingang für {ref} wurde ein Gerät mit einer anderen IMEI gescannt. Unser Wareneingang prüft die Sendung; bitte senden Sie uns Ihre Packliste.",
            ("MISSING_DEVICE", "INVESTIGATE"): f"für {ref} haben wir keinen Wareneingangsscan. Unser Lager sucht die Sendung; bitte senden Sie uns die Sendungsnummer.",
            ("GRADE_DISPUTE", "UPHOLD"): f"wir haben {ref} erneut geprüft. Festgestellt: {', '.join(d.get('defects', [])) or 'keine Mängel'}, Akku {d.get('battery_health')}%. Damit bleibt Zustand {d.get('assigned_grade')} (EUR {d.get('paid_eur')}). Fotos und Testbericht senden wir gerne.",
            ("GRADE_DISPUTE", "CORRECT_AND_CREDIT"): f"Sie haben recht: {ref} entspricht Zustand {d.get('corrected_grade')}, nicht {d.get('assigned_grade')}. Wir korrigieren die Bewertung und schreiben EUR {f['credit_eur']} gut.",
            ("PRICE_CHANGE", "UPHOLD"): f"die Auszahlung für {ref} von EUR {d.get('paid_eur')} entspricht unserer Preistabelle für Zustand {d.get('assigned_grade')}. Das Angebot (Zustand {d.get('quoted_grade')}) galt für den angegebenen Zustand.",
            ("PRICE_CHANGE", "CORRECT_AND_CREDIT"): f"danke für den Hinweis: für {ref} wurde ein veralteter Preis verwendet. Wir schreiben EUR {f['credit_eur']} gut.",
        }
    else:
        hi, bye = f"Hello {name},", "Kind regards,\nTrade-in Support Team"
        ref = f"{d.get('model', '')} (order {d.get('order', '?')}, IMEI {d.get('imei', '?')})"
        msg = {
            ("LOCK", "REQUEST_INFO"): f"the {ref} is on hold because it is {'still locked with iCloud / Find My' if d.get('fmi_lock') else 'still enrolled in MDM (device management)'}. "
                                      "Please ask the customer to remove the lock. We will then re-check the device and release the payout. We hold the device for 14 days.",
            ("IMEI_MISMATCH", "INVESTIGATE"): f"at goods-in for {ref} we scanned a device with a different IMEI. Our receiving team is checking the shipment; please send us your packing list.",
            ("MISSING_DEVICE", "INVESTIGATE"): f"we have no goods-in scan for {ref}. Our warehouse team is tracing the parcel; please send us the tracking number.",
            ("GRADE_DISPUTE", "UPHOLD"): f"we re-checked {ref}. Recorded: {', '.join(d.get('defects', [])) or 'no defects'}; battery {d.get('battery_health')}%. That means grade {d.get('assigned_grade')} stands (EUR {d.get('paid_eur')}). We are happy to share the photos and test report.",
            ("GRADE_DISPUTE", "CORRECT_AND_CREDIT"): f"you are right: {ref} meets grade {d.get('corrected_grade')}, not {d.get('assigned_grade')}. We are correcting the grade and crediting EUR {f['credit_eur']}.",
            ("PRICE_CHANGE", "UPHOLD"): f"the payout of EUR {d.get('paid_eur')} for {ref} matches our price table for grade {d.get('assigned_grade')}. The quote assumed grade {d.get('quoted_grade')}.",
            ("PRICE_CHANGE", "CORRECT_AND_CREDIT"): f"thank you for flagging this: an outdated price was applied to {ref}. We are crediting EUR {f['credit_eur']}.",
        }
    body = msg.get((c, a))
    if body is None:
        if not d:
            body = ("wir konnten Ihre Anfrage keinem Trade-in zuordnen. Bitte senden Sie uns die IMEI oder Auftragsnummer; ein Kollege meldet sich innerhalb von zwei Werktagen."
                    if lang == "de" else
                    "we could not match your request to one of your trade-ins. Please send us the IMEI or order number, and a colleague will reply within two working days.")
        else:
            body = ("wir prüfen Ihre Anfrage manuell und melden uns innerhalb von zwei Werktagen." if lang == "de"
                    else "a colleague is reviewing your request and will reply within two working days.")
    if lang != "de":
        body = body[0].upper() + body[1:]
    return f"{hi}\n\n{body}\n\n{bye}"
