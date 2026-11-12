"""Incident catalogue, severity rules and safety score."""
import math

SEVERITIES = ["LOW", "MEDIUM", "HIGH", "CRITICAL"]

CATALOG = {
    "no_helmet": ("Helmet missing", "Worker detected without a safety helmet.",
                  "Stop the task and provide a helmet before work continues.", "CRITICAL"),
    "no_harness": ("Harness missing", "Worker at height without a safety harness.",
                   "Bring the worker down and fit a harness immediately.", "CRITICAL"),
    "no_goggle": ("Eye protection missing", "Worker detected without safety goggles.",
                  "Provide goggles before continuing eye-hazard work.", "HIGH"),
    "no_goggles": ("Eye protection missing", "Worker detected without safety goggles.",
                   "Provide goggles before continuing eye-hazard work.", "HIGH"),
    "no_vest": ("Safety vest missing", "Worker detected without a high-visibility vest.",
                "Ask the worker to wear a hi-vis vest.", "MEDIUM"),
    "no_gloves": ("Gloves missing", "Worker detected without protective gloves.",
                  "Provide suitable gloves for the task.", "MEDIUM"),
    "no_boots": ("Safety boots missing", "Worker detected without safety boots.",
                 "Ask the worker to wear safety footwear.", "MEDIUM"),
    "no_mask": ("Mask missing", "Worker detected without a protective mask.",
                "Provide a mask or respirator for the area.", "MEDIUM"),
}


def pretty(violation):
    return violation.replace("_", " ").title()


def severity_for(violation):
    return CATALOG.get(violation, (None, None, None, "MEDIUM"))[3]


def bump(severity, steps=1):
    i = SEVERITIES.index(severity) if severity in SEVERITIES else 1
    return SEVERITIES[min(len(SEVERITIES) - 1, i + steps)]


def enrich_incident_data(violation, confidence=0.0):
    title, desc, action, sev = CATALOG.get(
        violation,
        (pretty(violation), f"PPE violation detected: {pretty(violation)}.",
         "Check the worker and correct the violation.", "MEDIUM"),
    )
    return {
        "title": title,
        "description": desc,
        "recommended_action": action,
        "severity": sev,
        "confidence": round(float(confidence), 3),
    }


def calculate_safety_score(total_checks, total_violations):
    """Simple ratio score kept for backwards compatibility."""
    if not total_checks:
        return 100.0
    return round(max(0.0, 100.0 * (1 - total_violations / total_checks)), 1)


WEIGHTS = {"LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 5}


def weighted_safety_score(counts_by_severity, resolved_ratio=0.0):
    """100 = nothing happened. Each incident adds a weighted penalty; resolved ones count half."""
    penalty = sum(WEIGHTS.get(s, 2) * n for s, n in counts_by_severity.items())
    penalty *= (1 - 0.5 * resolved_ratio)
    return round(100.0 * math.exp(-penalty / 60.0), 1)


def score_label(score):
    if score >= 90:
        return "Excellent"
    if score >= 75:
        return "Good"
    if score >= 50:
        return "Needs attention"
    return "Critical"
