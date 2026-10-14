"""Generate a synthetic recommerce dataset: partners, graders, devices and
partner dispute emails with ground-truth labels.

All names, companies, IMEIs and prices are invented. Deterministic (seeded),
so every run produces the same demo data.
"""
import json
import random
import sqlite3
from datetime import date, timedelta

from . import rules

PARTNERS = [
    ("P01", "Kestrel Telecom", "Mobile operator", "FI", "en"),
    ("P02", "Aurora Mobile", "Mobile operator", "SE", "en"),
    ("P03", "Rhine Digital", "Mobile operator", "DE", "de"),
    ("P04", "Lumen Retail", "Retailer", "DE", "de"),
    ("P05", "Polaris Phones", "Retailer", "PL", "en"),
    ("P06", "Fjord Connect", "Mobile operator", "NO", "en"),
]
CONTACTS = {
    "P01": "Anna Virtanen", "P02": "Erik Lund", "P03": "Jonas Weber",
    "P04": "Miriam Koch", "P05": "Tomasz Nowak", "P06": "Ingrid Berg",
}
GRADERS = ["G01", "G02", "G03", "G04", "G05", "G06", "G07", "G08"]
ERROR_PRONE = ["G04", "G07"]  # used to make root-cause analysis interesting
TODAY = date(2026, 10, 14)


def luhn_imei(rng):
    body = [rng.randint(0, 9) for _ in range(14)]
    body[0] = 3
    body[1] = 5
    total = 0
    for i, d in enumerate(body):
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    check = (10 - total % 10) % 10
    return "".join(map(str, body)) + str(check)


def defects_for(rng, grade):
    """Pick defects whose worst allowed grade is exactly `grade`."""
    pools = {g: [d for d, dg in rules.DEFECT_GRADE.items() if dg == g] for g in "BCD"}
    if grade == "A":
        return []
    out = [rng.choice(pools[grade])]
    # sometimes add a milder defect too
    milder = [g for g in "BC" if rules.GRADES.index(g) < rules.GRADES.index(grade)]
    if milder and rng.random() < 0.4:
        out.append(rng.choice(pools[rng.choice(milder)]))
    return out


def base_device(rng, n, partner_id, model=None):
    model = model or rng.choice(list(rules.BASE_PRICE))
    return {
        "imei": luhn_imei(rng),
        "order_id": f"TRD-26{n:05d}",
        "partner_id": partner_id,
        "model": model,
        "quoted_grade": "A",
        "quoted_price": rules.price_for(model, "A"),
        "received": 1,
        "received_imei": None,
        "fmi_lock": 0,
        "mdm_lock": 0,
        "defects": [],
        "battery_health": rng.randint(82, 98),
        "final_grade": "A",
        "final_price": rules.price_for(model, "A"),
        "grader_id": rng.choice(GRADERS),
        "graded_at": str(TODAY - timedelta(days=rng.randint(2, 25))),
    }


# ---------------------------------------------------------------- emails
def email_for(rng, scenario, dev, partner, lang):
    contact = CONTACTS[partner[0]]
    ident_style = rng.choice(["imei", "order", "both"])
    if ident_style == "imei":
        ref_en, ref_de = f"IMEI {dev['imei']}", f"IMEI {dev['imei']}"
    elif ident_style == "order":
        ref_en, ref_de = f"order {dev['order_id']}", f"Auftrag {dev['order_id']}"
    else:
        ref_en = f"order {dev['order_id']} (IMEI {dev['imei']})"
        ref_de = f"Auftrag {dev['order_id']} (IMEI {dev['imei']})"
    m, q, f = dev["model"], dev["quoted_grade"], dev["final_grade"]
    qp, fp = dev["quoted_price"], dev["final_price"]

    en = {
        "grade": [
            (f"Grade dispute – {m}",
             f"Hello,\n\nThe {m} on {ref_en} was sent in as grade {q}, but your report says grade {f}. "
             f"Our store checked it before shipping and it was in very good condition. Please re-check the grading and correct the payout.\n\nBest regards,\n{contact}"),
            (f"Wrong grading on {ref_en}",
             f"Hi team,\n\nWe do not agree with the grade {f} for the {m} ({ref_en}). The customer handed it in as grade {q}. "
             f"Can you send us the photos and test results, or adjust the grade?\n\nThanks,\n{contact}"),
            (f"Please review condition assessment",
             f"Dear Trade-in Support team,\n\nFor {ref_en} you downgraded the device from {q} to {f}. This seems too strict. "
             f"Please review the condition assessment.\n\nKind regards,\n{contact}"),
        ],
        "lock": [
            (f"Payout missing for {ref_en}",
             f"Hello,\n\nWe have not received any payout for {ref_en} ({m}). Your portal shows it as on hold. "
             f"Why is this device blocked? Please release the payment.\n\nRegards,\n{contact}"),
            (f"Device on hold – {m}",
             f"Hi,\n\nThe {m} with {ref_en} has been on hold for over a week. Please explain what is missing so we can close this trade-in.\n\nThanks,\n{contact}"),
        ],
        "imei": [
            (f"Device rejected – {ref_en}",
             f"Hello,\n\nYour system says the {m} for {ref_en} was rejected, but we shipped exactly that device. "
             f"Please check again and pay the agreed amount of EUR {qp}.\n\nBest,\n{contact}"),
            (f"Rejected trade-in {ref_en}",
             f"Hi team,\n\nWhy was {ref_en} rejected? Our records show the correct phone was packed. Please investigate.\n\n{contact}"),
        ],
        "missing": [
            (f"Device not received? {ref_en}",
             f"Hello,\n\nYour portal says the {m} for {ref_en} was never received, but the tracking shows it was delivered to your Tartu site. "
             f"Please find it and process the payout.\n\nRegards,\n{contact}"),
            (f"Missing device – {ref_en}",
             f"Hi,\n\nWe sent the {m} ({ref_en}) three weeks ago. It still shows as not arrived. Can you check your warehouse?\n\nThanks,\n{contact}"),
        ],
        "price": [
            (f"Payout lower than expected – {ref_en}",
             f"Hello,\n\nFor {ref_en} ({m}, grade {f}) we received EUR {fp}. We expected more for this grade. "
             f"Please explain how the price was calculated.\n\nBest regards,\n{contact}"),
            (f"Price question {ref_en}",
             f"Hi team,\n\nThe payout for the {m} on {ref_en} is EUR {fp}. The quote was EUR {qp}. Why is the amount different?\n\nThanks,\n{contact}"),
        ],
    }
    de = {
        "grade": [
            (f"Einspruch Bewertung – {m}",
             f"Hallo,\n\ndas {m} aus {ref_de} wurde als Zustand {q} eingeschickt, Ihr Bericht zeigt aber Zustand {f}. "
             f"Bitte prüfen Sie die Bewertung erneut und korrigieren Sie die Auszahlung.\n\nMit freundlichen Grüßen\n{contact}"),
        ],
        "lock": [
            (f"Auszahlung fehlt – {ref_de}",
             f"Hallo,\n\nfür {ref_de} ({m}) haben wir keine Auszahlung erhalten. Im Portal steht das Gerät auf 'gesperrt'. "
             f"Bitte erklären Sie, was fehlt.\n\nViele Grüße\n{contact}"),
        ],
        "imei": [
            (f"Gerät abgelehnt – {ref_de}",
             f"Hallo,\n\nwarum wurde {ref_de} abgelehnt? Wir haben genau dieses Gerät verschickt. Bitte prüfen.\n\n{contact}"),
        ],
        "missing": [
            (f"Gerät nicht erhalten? {ref_de}",
             f"Hallo,\n\nlaut Portal ist das {m} aus {ref_de} nicht angekommen, die Sendungsverfolgung zeigt aber die Zustellung. "
             f"Bitte prüfen Sie Ihr Lager.\n\nViele Grüße\n{contact}"),
        ],
        "price": [
            (f"Frage zum Preis – {ref_de}",
             f"Hallo,\n\nfür {ref_de} ({m}, Zustand {f}) haben wir EUR {fp} erhalten, angeboten waren EUR {qp}. "
             f"Wie wurde der Betrag berechnet?\n\nMit freundlichen Grüßen\n{contact}"),
        ],
    }
    kind = {"grade_ok": "grade", "grade_err": "grade", "fmi": "lock", "mdm": "lock",
            "imei": "imei", "missing": "missing", "price_ok": "price", "price_err": "price"}[scenario]
    pool = de if lang == "de" else en
    subject, body = rng.choice(pool[kind])
    return subject, body


SCENARIOS = (
    ["grade_ok"] * 7 + ["grade_err"] * 6 + ["fmi"] * 4 + ["mdm"] * 3 +
    ["imei"] * 4 + ["missing"] * 4 + ["price_ok"] * 4 + ["price_err"] * 4
)


def build(db_path):
    rng = random.Random(42)
    con = sqlite3.connect(db_path)
    cur = con.cursor()
    cur.executescript(SCHEMA)

    cur.executemany("INSERT INTO partners VALUES (?,?,?,?,?)", PARTNERS)
    n = 1

    # Background devices: normal processed trade-ins, graded with some error.
    for _ in range(260):
        p = rng.choice(PARTNERS)
        d = base_device(rng, n, p[0]); n += 1
        true_g = rng.choices("ABCD", weights=[35, 35, 20, 10])[0]
        d["defects"] = defects_for(rng, true_g)
        d["quoted_grade"] = rng.choice([g for g in "AB" if rules.GRADES.index(g) <= rules.GRADES.index(true_g)] or ["A"])
        d["quoted_price"] = rules.price_for(d["model"], d["quoted_grade"])
        g = true_g
        err_rate = 0.18 if d["grader_id"] in ERROR_PRONE else 0.04
        if rng.random() < err_rate and g != "D":
            g = rules.GRADES[rules.GRADES.index(g) + 1]
        d["final_grade"] = g
        d["final_price"] = rules.price_for(d["model"], g)
        insert_device(cur, d)

    # Disputed devices.
    order = SCENARIOS[:]
    rng.shuffle(order)
    # Kestrel Telecom sends 8 disputes (triggers the repeat-partner escalation rule).
    partner_plan = ["P01"] * 8 + ["P02"] * 7 + ["P03"] * 6 + ["P04"] * 6 + ["P05"] * 5 + ["P06"] * 4
    rng.shuffle(partner_plan)
    pmap = {p[0]: p for p in PARTNERS}

    for i, (scenario, pid) in enumerate(zip(order, partner_plan), start=1):
        partner = pmap[pid]
        d = base_device(rng, n, pid); n += 1
        label = {"scenario": scenario}

        if scenario in ("grade_ok", "grade_err", "price_ok", "price_err"):
            true_g = rng.choice("BCD")
            d["defects"] = defects_for(rng, true_g)
            d["quoted_grade"] = rng.choice([g for g in "AB" if rules.GRADES.index(g) < rules.GRADES.index(true_g)])
            d["quoted_price"] = rules.price_for(d["model"], d["quoted_grade"])
            d["final_grade"] = true_g
            d["final_price"] = rules.price_for(d["model"], true_g)
            if scenario == "grade_err":
                d["grader_id"] = rng.choice(ERROR_PRONE)
                true_g = rng.choice("AB")
                d["defects"] = defects_for(rng, true_g)
                d["quoted_grade"] = true_g if rng.random() < 0.7 else "A"
                d["quoted_price"] = rules.price_for(d["model"], d["quoted_grade"])
                wrong = rules.GRADES[min(3, rules.GRADES.index(true_g) + rng.choice([1, 2]))]
                d["final_grade"] = wrong
                d["final_price"] = rules.price_for(d["model"], wrong)
            if scenario == "price_err":
                d["final_price"] = round(d["final_price"] * 0.85)  # stale price table
        elif scenario in ("fmi", "mdm"):
            d["fmi_lock"] = int(scenario == "fmi")
            d["mdm_lock"] = int(scenario == "mdm")
            d["final_price"] = 0
        elif scenario == "imei":
            d["received_imei"] = luhn_imei(rng)
            d["final_grade"] = None
            d["final_price"] = 0
            d["grader_id"] = None
        elif scenario == "missing":
            d["received"] = 0
            d["final_grade"] = None
            d["final_price"] = 0
            d["grader_id"] = None
            d["graded_at"] = None

        insert_device(cur, d)

        # Ground truth
        cat, action = {
            "grade_ok": ("GRADE_DISPUTE", "UPHOLD"),
            "grade_err": ("GRADE_DISPUTE", "CORRECT_AND_CREDIT"),
            "fmi": ("LOCK", "REQUEST_INFO"),
            "mdm": ("LOCK", "REQUEST_INFO"),
            "imei": ("IMEI_MISMATCH", "INVESTIGATE"),
            "missing": ("MISSING_DEVICE", "INVESTIGATE"),
            "price_ok": ("PRICE_CHANGE", "UPHOLD"),
            "price_err": ("PRICE_CHANGE", "CORRECT_AND_CREDIT"),
        }[scenario]
        credit = 0
        if scenario == "grade_err":
            credit = rules.price_for(d["model"], rules.expected_grade(d["defects"], d["battery_health"])) - d["final_price"]
        elif scenario == "price_err":
            credit = rules.price_for(d["model"], d["final_grade"]) - d["final_price"]

        lang = "de" if (partner[4] == "de" and rng.random() < 0.6) else "en"
        subject, body = email_for(rng, scenario, d, partner, lang)
        if rng.random() < 0.12:
            body = body.replace("\n\n", "\n\nThis is the third time this month and it is unacceptable. ", 1) if lang == "en" \
                else body.replace("\n\n", "\n\nDas ist unakzeptabel, wir prüfen rechtliche Schritte. ", 1)

        label.update({"category": cat, "action": action, "credit": credit,
                      "team": rules.TEAM[cat], "imei": d["imei"], "language": lang})
        received_at = f"{TODAY - timedelta(days=rng.randint(0, 6))} {rng.randint(8, 17):02d}:{rng.randint(0, 59):02d}"
        cur.execute(
            "INSERT INTO disputes (id, partner_id, sender, subject, body, lang, received_at, status, label) VALUES (?,?,?,?,?,?,?,?,?)",
            (f"D{i:03d}", pid, f"{CONTACTS[pid]} <{CONTACTS[pid].split()[0].lower()}@{partner[1].split()[0].lower()}.example>",
             subject, body, lang, received_at, "NEW", json.dumps(label)))

    # Edge cases: what a rules engine handles poorly and a human must see.
    i = len(order)
    edges = []
    # 1) No identifier at all
    p = pmap["P05"]
    edges.append(("P05", "Problem with last week's Samsung",
                  f"Hi,\n\nOne of the Samsung phones we sent last week came back with a much lower grade than we expected. "
                  f"Can you check it? I don't have the order number at hand.\n\nThanks,\n{CONTACTS['P05']}",
                  "en", {"category": "GRADE_DISPUTE", "action": "MANUAL_REVIEW", "credit": 0,
                         "team": rules.TEAM["GRADE_DISPUTE"], "imei": None, "language": "en"}))
    # 2) Partner quotes a device that belongs to another partner
    other = cur.execute("SELECT imei, model, final_grade FROM devices WHERE partner_id='P03' AND final_grade IS NOT NULL LIMIT 1").fetchone()
    edges.append(("P06", f"Grade check IMEI {other[0]}",
                  f"Hello,\n\nPlease re-check the grade on IMEI {other[0]} ({other[1]}). We think grade {other[2]} is too strict.\n\nRegards,\n{CONTACTS['P06']}",
                  "en", {"category": "GRADE_DISPUTE", "action": "MANUAL_REVIEW", "credit": 0,
                         "team": rules.TEAM["GRADE_DISPUTE"], "imei": other[0], "language": "en"}))
    # 3) Finnish email, grade dispute where our grade is correct
    d = base_device(rng, n, "P01"); n += 1
    d["defects"] = ["cracked back glass"]; d["quoted_grade"] = "B"
    d["quoted_price"] = rules.price_for(d["model"], "B")
    d["final_grade"] = "D"; d["final_price"] = rules.price_for(d["model"], "D")
    insert_device(cur, d)
    edges.append(("P01", f"Luokitus väärin – {d['order_id']}",
                  f"Hei,\n\nlaite {d['model']} (tilaus {d['order_id']}) lähetettiin kuntoluokassa B, mutta te luokittelitte sen D:ksi. "
                  f"Asiakkaan mukaan puhelin oli hyvässä kunnossa. Tarkistakaa luokitus.\n\nTerveisin,\n{CONTACTS['P01']}",
                  "fi", {"category": "GRADE_DISPUTE", "action": "UPHOLD", "credit": 0,
                         "team": rules.TEAM["GRADE_DISPUTE"], "imei": d["imei"], "language": "fi"}))
    for pid, subject, body, lang, lab in edges:
        i += 1
        lab["scenario"] = "edge"
        partner = pmap[pid]
        received_at = f"{TODAY - timedelta(days=rng.randint(0, 3))} {rng.randint(8, 17):02d}:{rng.randint(0, 59):02d}"
        cur.execute(
            "INSERT INTO disputes (id, partner_id, sender, subject, body, lang, received_at, status, label) VALUES (?,?,?,?,?,?,?,?,?)",
            (f"D{i:03d}", pid, f"{CONTACTS[pid]} <{CONTACTS[pid].split()[0].lower()}@{partner[1].split()[0].lower()}.example>",
             subject, body, lang, received_at, "NEW", json.dumps(lab)))

    # escalation ground truth (policy-based) once all disputes exist
    counts = dict(cur.execute("SELECT partner_id, COUNT(*) FROM disputes GROUP BY partner_id").fetchall())
    for did, pid, body, label in cur.execute("SELECT id, partner_id, body, label FROM disputes").fetchall():
        lab = json.loads(label)
        dev = cur.execute("SELECT quoted_price, partner_id FROM devices WHERE imei=?", (lab["imei"],)).fetchone()
        value = dev[0] if dev and dev[1] == pid else 0
        esc = (counts[pid] >= rules.ESCALATE_PARTNER_DISPUTES
               or max(value, lab["credit"]) > rules.ESCALATE_VALUE_EUR
               or lab["action"] == "MANUAL_REVIEW"
               or any(w in body.lower() for w in rules.ESCALATION_WORDS))
        lab["escalate"] = bool(esc)
        con.execute("UPDATE disputes SET label=? WHERE id=?", (json.dumps(lab), did))

    con.commit()
    con.close()


def insert_device(cur, d):
    cur.execute(
        "INSERT INTO devices VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (d["imei"], d["order_id"], d["partner_id"], d["model"], d["quoted_grade"], d["quoted_price"],
         d["received"], d["received_imei"], d["fmi_lock"], d["mdm_lock"], json.dumps(d["defects"]),
         d["battery_health"], d["final_grade"], d["final_price"], d["grader_id"], d["graded_at"]))


SCHEMA = """
DROP TABLE IF EXISTS partners; DROP TABLE IF EXISTS devices; DROP TABLE IF EXISTS disputes;
DROP TABLE IF EXISTS cases; DROP TABLE IF EXISTS audit;
CREATE TABLE partners (id TEXT PRIMARY KEY, name TEXT, type TEXT, country TEXT, lang TEXT);
CREATE TABLE devices (
  imei TEXT PRIMARY KEY, order_id TEXT UNIQUE, partner_id TEXT, model TEXT,
  quoted_grade TEXT, quoted_price INTEGER, received INTEGER, received_imei TEXT,
  fmi_lock INTEGER, mdm_lock INTEGER, defects TEXT, battery_health INTEGER,
  final_grade TEXT, final_price INTEGER, grader_id TEXT, graded_at TEXT);
CREATE TABLE disputes (
  id TEXT PRIMARY KEY, partner_id TEXT, sender TEXT, subject TEXT, body TEXT, lang TEXT,
  received_at TEXT, status TEXT, label TEXT);
CREATE TABLE cases (
  dispute_id TEXT PRIMARY KEY, result TEXT, final_reply TEXT, decision TEXT,
  processed_at TEXT, decided_at TEXT, handling_seconds REAL, jira_key TEXT);
CREATE TABLE audit (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, dispute_id TEXT, event TEXT, detail TEXT);
"""
