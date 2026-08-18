"""Grounding facts for Indian law, so the model reasons from a map rather than
free-associating.

This is scaffolding, not a legal database. Its job is to put the right statute
names, forums and helplines in front of the model for a given kind of problem —
the model still has to apply them, and every prompt that uses this says section
numbers must be verified before anyone relies on them.

Two things it deliberately encodes:

- India replaced its core criminal codes on 1 July 2024. The IPC, CrPC and
  Evidence Act became the BNS, BNSS and BSA. Offences from before that date are
  still tried under the old codes, so both names matter and the model is told so.
- Law is split between the Union and the states. Rent, police, land, stamp duty
  and shops-and-establishments rules are state subjects, so the answer changes
  with the state — which is why the state is asked for early.
"""

import re

STATES = [
    "Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar", "Chhattisgarh", "Goa",
    "Gujarat", "Haryana", "Himachal Pradesh", "Jharkhand", "Karnataka", "Kerala",
    "Madhya Pradesh", "Maharashtra", "Manipur", "Meghalaya", "Mizoram", "Nagaland",
    "Odisha", "Punjab", "Rajasthan", "Sikkim", "Tamil Nadu", "Telangana", "Tripura",
    "Uttar Pradesh", "Uttarakhand", "West Bengal",
    "Andaman and Nicobar Islands", "Chandigarh", "Dadra and Nagar Haveli and Daman and Diu",
    "Delhi", "Jammu and Kashmir", "Ladakh", "Lakshadweep", "Puducherry",
]

# Cities people name instead of the state they are in.
CITY_TO_STATE = {
    "mumbai": "Maharashtra", "pune": "Maharashtra", "nagpur": "Maharashtra",
    "thane": "Maharashtra", "nashik": "Maharashtra",
    "bengaluru": "Karnataka", "bangalore": "Karnataka", "mysuru": "Karnataka",
    "mysore": "Karnataka", "mangaluru": "Karnataka", "hubli": "Karnataka",
    "chennai": "Tamil Nadu", "coimbatore": "Tamil Nadu", "madurai": "Tamil Nadu",
    "hyderabad": "Telangana", "warangal": "Telangana",
    "kolkata": "West Bengal", "howrah": "West Bengal",
    "ahmedabad": "Gujarat", "surat": "Gujarat", "vadodara": "Gujarat", "rajkot": "Gujarat",
    "jaipur": "Rajasthan", "jodhpur": "Rajasthan", "udaipur": "Rajasthan",
    "lucknow": "Uttar Pradesh", "kanpur": "Uttar Pradesh", "noida": "Uttar Pradesh",
    "varanasi": "Uttar Pradesh", "agra": "Uttar Pradesh", "ghaziabad": "Uttar Pradesh",
    "patna": "Bihar", "bhopal": "Madhya Pradesh", "indore": "Madhya Pradesh",
    "gurugram": "Haryana", "gurgaon": "Haryana", "faridabad": "Haryana",
    "chandigarh": "Chandigarh", "kochi": "Kerala", "ernakulam": "Kerala",
    "thiruvananthapuram": "Kerala", "kozhikode": "Kerala",
    "bhubaneswar": "Odisha", "cuttack": "Odisha", "guwahati": "Assam",
    "raipur": "Chhattisgarh", "ranchi": "Jharkhand", "dehradun": "Uttarakhand",
    "shimla": "Himachal Pradesh", "amritsar": "Punjab", "ludhiana": "Punjab",
    "visakhapatnam": "Andhra Pradesh", "vijayawada": "Andhra Pradesh",
    "new delhi": "Delhi", "delhi": "Delhi", "srinagar": "Jammu and Kashmir",
}

# The 2024 recodification. Old names still matter for older offences.
CODE_CHANGES = (
    "India recodified its criminal law on 1 July 2024:\n"
    "- Indian Penal Code, 1860 → Bharatiya Nyaya Sanhita, 2023 (BNS)\n"
    "- Code of Criminal Procedure, 1973 → Bharatiya Nagarik Suraksha Sanhita, 2023 (BNSS)\n"
    "- Indian Evidence Act, 1872 → Bharatiya Sakshya Adhiniyam, 2023 (BSA)\n"
    "Offences committed BEFORE 1 July 2024 are still tried under the old codes. "
    "When the date matters, say which code applies and why."
)

# issue area → the statutes, forum and helpline that actually apply
ISSUE_AREAS = {
    "police_fir": {
        "label": "Police refusing to register a complaint / FIR",
        "keywords": ["fir", "police station", "police refuse", "complaint not registered",
                     "sho", "station house", "not filing", "refused to file"],
        "statutes": [
            "Bharatiya Nagarik Suraksha Sanhita, 2023 — registration of an FIR for a "
            "cognizable offence (BNSS s.173; formerly CrPC s.154)",
            "BNSS s.173(4) — complaint to the Superintendent of Police when the station "
            "refuses (formerly CrPC s.154(3))",
            "BNSS s.175(3) — application to the Magistrate to direct registration "
            "(formerly CrPC s.156(3))",
        ],
        "forum": "Police station → SP/DCP in writing → Magistrate; also the State Human "
                 "Rights Commission and the state police complaint portal",
        "helpline": "112 (emergency), state police online FIR portal",
        "notes": "A Zero FIR can be registered at ANY police station regardless of "
                 "jurisdiction, then transferred. Always get an acknowledgement with a "
                 "receipt number, and send copies by registered post or email so the "
                 "date of delivery is provable.",
    },
    "senior_citizens": {
        "label": "Maintenance, neglect or property dispute involving a senior citizen",
        "keywords": ["senior citizen", "elderly", "parents", "maintenance", "old age",
                     "abandoned", "neglect", "aged parent"],
        "statutes": [
            "Maintenance and Welfare of Parents and Senior Citizens Act, 2007 — s.4/s.5 "
            "application for maintenance to the Maintenance Tribunal",
            "Maintenance and Welfare of Parents and Senior Citizens Act, 2007 s.23 — a "
            "transfer of property subject to a promise of care can be declared void if "
            "the promise is broken",
            "BNSS s.144 (formerly CrPC s.125) — maintenance of parents",
        ],
        "forum": "Maintenance Tribunal at the Sub-Divisional Magistrate level; appeal to "
                 "the Appellate Tribunal (District Magistrate)",
        "helpline": "14567 (Elderline), 112",
        "notes": "The Tribunal is meant to be low-cost and lawyer-optional, and is "
                 "required to decide quickly. State rules under the Act vary, so the "
                 "procedure and forms depend on the state.",
    },
    "domestic_violence": {
        "label": "Domestic violence, harassment or dowry demands",
        "keywords": ["domestic violence", "husband beats", "in-laws", "dowry",
                     "harassment at home", "abusive husband", "cruelty", "498a"],
        "statutes": [
            "Protection of Women from Domestic Violence Act, 2005 — protection, "
            "residence, monetary and custody orders (s.12 application)",
            "Bharatiya Nyaya Sanhita, 2023 s.85/s.86 — cruelty by husband or his "
            "relatives (formerly IPC s.498A)",
            "Dowry Prohibition Act, 1961",
        ],
        "forum": "Protection Officer of the district, Magistrate under the DV Act, "
                 "police station, One Stop Centre",
        "helpline": "181 (women), 1091, 112, 1098 (children)",
        "notes": "A DV Act application can be filed without an FIR and covers a live-in "
                 "relationship too. Relief can include staying in the shared household.",
    },
    "consumer": {
        "label": "Defective goods, poor service, refund refused",
        "keywords": ["refund", "consumer", "defective", "warranty", "e-commerce",
                     "online order", "service deficiency", "builder delay", "not delivered"],
        "statutes": [
            "Consumer Protection Act, 2019 — complaint to the District/State/National "
            "Commission depending on value",
            "Consumer Protection (E-Commerce) Rules, 2020",
        ],
        "forum": "District Consumer Disputes Redressal Commission; file online at "
                 "e-daakhil.nic.in",
        "helpline": "1915 (National Consumer Helpline), consumerhelpline.gov.in",
        "notes": "Send a written notice to the seller first and keep proof of delivery; "
                 "there is a limitation period of two years from the cause of action.",
    },
    "cheque_bounce": {
        "label": "Cheque bounced / payment default",
        "keywords": ["cheque bounce", "cheque returned", "dishonour", "138",
                     "insufficient funds", "bounced"],
        "statutes": [
            "Negotiable Instruments Act, 1881 s.138 — dishonour of cheque",
            "Negotiable Instruments Act, 1881 s.138 proviso — demand notice within 30 "
            "days of the bank memo; complaint within 30 days after the 15-day notice "
            "period expires",
        ],
        "forum": "Magistrate's court where the payee's bank branch is located",
        "helpline": "—",
        "notes": "The deadlines are strict and the case dies if they are missed. Keep the "
                 "bank return memo — it starts the clock.",
    },
    "tenancy": {
        "label": "Landlord/tenant — deposit, eviction, rent",
        "keywords": ["landlord", "tenant", "deposit", "rent", "eviction", "vacate",
                     "security deposit", "lease", "pg ", "flat owner"],
        "statutes": [
            "The state Rent Control Act — this is a STATE subject and differs by state "
            "(e.g. Karnataka Rent Act 1999, Maharashtra Rent Control Act 1999, Delhi "
            "Rent Act 1958, Tamil Nadu Regulation of Rights and Responsibilities of "
            "Landlords and Tenants Act 2017)",
            "Model Tenancy Act, 2021 — only where the state has adopted it",
            "Transfer of Property Act, 1882 s.106 — notice to quit, where the Rent Act "
            "does not apply",
        ],
        "forum": "Rent Controller / Rent Authority of the state; Civil Court where no "
                 "Rent Act applies; Consumer Commission is generally NOT the forum",
        "helpline": "—",
        "notes": "Which Act applies depends on the state and often on the rent amount "
                 "and whether the premises are residential or commercial. Ask for the "
                 "state before naming a statute.",
    },
    "employment": {
        "label": "Salary unpaid, wrongful termination, workplace issues",
        "keywords": ["salary", "terminated", "fired", "resignation", "notice period",
                     "employer", "unpaid", "full and final", "relieving letter", "posh"],
        "statutes": [
            "Industrial Disputes Act, 1947 — for workmen; conciliation before the "
            "Labour Commissioner",
            "Payment of Wages Act, 1936 and the Code on Wages, 2019",
            "The state Shops and Establishments Act — a STATE law, differs by state",
            "Sexual Harassment of Women at Workplace (Prevention, Prohibition and "
            "Redressal) Act, 2013 — Internal Committee complaint",
        ],
        "forum": "Labour Commissioner of the state, Labour Court, or the Internal "
                 "Committee for harassment matters",
        "helpline": "—",
        "notes": "Whether someone counts as a 'workman' changes the forum entirely. "
                 "Managerial and supervisory roles usually fall outside the ID Act.",
    },
    "cyber": {
        "label": "Online fraud, cyber crime, defamation online",
        "keywords": ["online fraud", "upi", "cyber", "hacked", "phishing", "otp",
                     "scam", "social media", "morphed", "fake profile"],
        "statutes": [
            "Information Technology Act, 2000 — ss.43, 66, 66C (identity theft), 66D "
            "(cheating by personation), 67 (obscene material)",
            "Bharatiya Nyaya Sanhita, 2023 — cheating and forgery provisions",
        ],
        "forum": "cybercrime.gov.in, the local cyber cell, and your bank",
        "helpline": "1930 (cyber financial fraud — call within the golden hour), 112",
        "notes": "For money lost online, report on 1930 IMMEDIATELY — funds can often be "
                 "frozen only in the first hours. Also write to the bank the same day.",
    },
    "motor_accident": {
        "label": "Road accident, injury, insurance claim",
        "keywords": ["accident", "vehicle", "insurance claim", "injury", "hit and run",
                     "motor", "compensation"],
        "statutes": [
            "Motor Vehicles Act, 1988 (as amended 2019) — s.166 claim before the Motor "
            "Accident Claims Tribunal; s.164 no-fault compensation",
            "Motor Vehicles Act, 1988 s.161 — hit-and-run compensation scheme",
        ],
        "forum": "Motor Accident Claims Tribunal (MACT) of the district",
        "helpline": "112, 108 (ambulance)",
        "notes": "Get the FIR, the accident report and the medical records early — the "
                 "claim is built on them. There is no limitation period for a s.166 claim.",
    },
    "property": {
        "label": "Land, property, registration or inheritance dispute",
        "keywords": ["property", "land", "khata", "mutation", "registry", "partition",
                     "inheritance", "will", "ancestral", "encroachment", "builder"],
        "statutes": [
            "Transfer of Property Act, 1882; Registration Act, 1908; the state Stamp Act",
            "Hindu Succession Act, 1956 / Indian Succession Act, 1925 — depending on "
            "which personal law applies",
            "Real Estate (Regulation and Development) Act, 2016 — for builder disputes, "
            "before the state RERA authority",
        ],
        "forum": "Civil Court; the state RERA for builder delays; the Sub-Registrar and "
                 "revenue authorities for records",
        "helpline": "—",
        "notes": "Land records, stamp duty and mutation are state subjects — the "
                 "procedure and the office differ by state.",
    },
    "rti": {
        "label": "Getting information from a government body",
        "keywords": ["rti", "right to information", "government department",
                     "public information", "no response from office"],
        "statutes": [
            "Right to Information Act, 2005 — s.6 application, s.19 first appeal within "
            "30 days, second appeal to the Information Commission",
        ],
        "forum": "Public Information Officer of the department; then the First Appellate "
                 "Authority; then the State or Central Information Commission",
        "helpline": "rtionline.gov.in for central bodies; states have their own portals",
        "notes": "The fee is ₹10 for central bodies and the reply is due in 30 days "
                 "(48 hours where life or liberty is involved). State fees differ.",
    },
}

# What the drafter can produce.
DRAFT_TYPES = {
    "police-complaint": "a written complaint to the Station House Officer requesting "
                        "registration of an FIR",
    "sp-complaint": "a complaint to the Superintendent of Police under BNSS s.173(4), "
                    "used when the police station has refused to register an FIR",
    "legal-notice": "a legal notice to the opposite party demanding action before "
                    "proceedings are started",
    "statement": "your own written statement of facts, dated and signed, to keep on "
                 "record or hand over",
    "consumer-complaint": "a complaint to the District Consumer Disputes Redressal "
                          "Commission",
    "rti": "an application under the Right to Information Act, 2005",
    "tribunal-application": "an application to the relevant tribunal (for example the "
                            "Maintenance Tribunal for a senior citizen matter)",
}


def detect_state(text):
    """Find an Indian state named in the text, directly or via a city."""
    lowered = (text or "").lower()
    for state in STATES:
        if re.search(rf"\b{re.escape(state.lower())}\b", lowered):
            return state
    for city, state in CITY_TO_STATE.items():
        if re.search(rf"\b{re.escape(city)}\b", lowered):
            return state
    return None


def detect_areas(text, limit=3):
    """Rank issue areas by how many of their keywords the text hits."""
    lowered = (text or "").lower()
    scored = []
    for key, area in ISSUE_AREAS.items():
        hits = sum(1 for word in area["keywords"] if word in lowered)
        if hits:
            scored.append((hits, key))
    scored.sort(reverse=True)
    return [key for _, key in scored[:limit]]


def brief(areas, state=None):
    """Build the grounding block handed to the model for a situation."""
    if not areas:
        return ""

    parts = [
        "REFERENCE NOTES (starting points, not a complete statement of the law — "
        "verify every section number before relying on it):",
        "",
        CODE_CHANGES,
        "",
    ]
    for key in areas:
        area = ISSUE_AREAS.get(key)
        if not area:
            continue
        parts.append(f"## {area['label']}")
        parts.append("Statutes commonly engaged:")
        parts.extend(f"- {s}" for s in area["statutes"])
        parts.append(f"Forum: {area['forum']}")
        if area["helpline"] != "—":
            parts.append(f"Helpline: {area['helpline']}")
        parts.append(f"Practical: {area['notes']}")
        parts.append("")

    if state:
        parts.append(
            f"The person is in {state}. Rent, police procedure, land records, stamp "
            f"duty and shops-and-establishments rules are state subjects, so name the "
            f"{state} statute or authority where one exists, and say plainly when the "
            f"state position needs checking."
        )
    else:
        parts.append(
            "The state is not yet known. Several of these areas are governed by state "
            "law, so ask which state the person is in before naming a state statute."
        )
    return "\n".join(parts)


# Statutes this app is willing to see cited without a source attached. Anything
# else in an answer gets flagged for verification by the guardrails.
KNOWN_STATUTES = {
    "bharatiya nyaya sanhita", "bns", "bharatiya nagarik suraksha sanhita", "bnss",
    "bharatiya sakshya adhiniyam", "bsa", "indian penal code", "ipc",
    "code of criminal procedure", "crpc", "indian evidence act",
    "maintenance and welfare of parents and senior citizens act",
    "protection of women from domestic violence act", "dowry prohibition act",
    "consumer protection act", "negotiable instruments act",
    "transfer of property act", "registration act", "model tenancy act",
    "industrial disputes act", "payment of wages act", "code on wages",
    "shops and establishments", "sexual harassment of women at workplace",
    "information technology act", "motor vehicles act", "right to information act",
    "hindu succession act", "indian succession act", "rera",
    "real estate (regulation and development) act", "rent act", "rent control act",
    "constitution of india", "specific relief act", "limitation act",
    "indian contract act", "arbitration and conciliation act",
}
