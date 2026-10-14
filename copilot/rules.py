"""Business rules for the recommerce dispute scenario.

Everything here is synthetic and illustrative: grade scale, price table,
defect-to-grade mapping and escalation policy. In a real deployment these
would come from the pricing engine and grading standard, not be hard-coded.
"""

GRADES = ["A", "B", "C", "D"]  # A = best

# Base buyback price (EUR) for a grade-A device.
BASE_PRICE = {
    "iPhone 11 64GB": 180,
    "iPhone 12 128GB": 260,
    "iPhone 13 128GB": 340,
    "iPhone 14 128GB": 420,
    "Galaxy S21 128GB": 170,
    "Galaxy S22 128GB": 220,
    "Pixel 7 128GB": 160,
}

GRADE_FACTOR = {"A": 1.00, "B": 0.85, "C": 0.65, "D": 0.35}

# Defect -> worst grade it allows.
DEFECT_GRADE = {
    "light scratches on frame": "B",
    "minor scratch on back": "B",
    "visible screen scratches": "C",
    "dent on corner": "C",
    "chip on back glass": "C",
    "cracked screen": "D",
    "cracked back glass": "D",
    "faulty Face ID": "D",
    "battery below 80%": "D",
}

# Which team owns each case type.
TEAM = {
    "GRADE_DISPUTE": "Quality Control (Tartu)",
    "LOCK": "Partner Operations",
    "IMEI_MISMATCH": "Receiving",
    "MISSING_DEVICE": "Warehouse & Logistics",
    "PRICE_CHANGE": "Pricing & Account Management",
    "OTHER": "Partner Operations",
}

CATEGORIES = list(TEAM.keys())
# UPHOLD             our grade/price stands, explain with evidence
# CORRECT_AND_CREDIT we were wrong, correct grade/price and credit the difference
# REQUEST_INFO       partner must act first (e.g. remove a lock)
# INVESTIGATE        internal check needed (missing device, IMEI mismatch)
# MANUAL_REVIEW      copilot could not match or verify the case
ACTIONS = ["UPHOLD", "CORRECT_AND_CREDIT", "REQUEST_INFO", "INVESTIGATE", "MANUAL_REVIEW"]

# Escalation policy.
ESCALATE_VALUE_EUR = 400          # device value or credit above this
ESCALATE_PARTNER_DISPUTES = 8     # disputes from the same partner in the window
ESCALATE_MIN_CONFIDENCE = 0.6
ESCALATION_WORDS = [
    "lawyer", "legal action", "terminate", "termination", "unacceptable",
    "anwalt", "rechtliche schritte", "kündigen",
]


def price_for(model: str, grade: str) -> int:
    return round(BASE_PRICE[model] * GRADE_FACTOR[grade])


def worst_grade(grades):
    return max(grades, key=GRADES.index) if grades else "A"


def expected_grade(defects, battery_health):
    gs = [DEFECT_GRADE[d] for d in defects if d in DEFECT_GRADE]
    if battery_health is not None and battery_health < 80:
        gs.append("D")
    return worst_grade(gs)
