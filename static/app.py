"""
SymptomIQ — Flask Backend
=========================
• Hybrid RAG: MedlinePlus + WHO ICD-11 + CDC ground the AI;
  AI knowledge fills gaps where context is thin
• Hard output filter blocks diagnoses / drug advice before text reaches user
• Rule engine (Python) owns ALL risk decisions — AI never decides severity
• Voice correction endpoint cleans raw speech-to-text transcripts
• Session state tracks symptoms, answers, questions asked, stage
"""

from flask import Flask, request, jsonify, render_template
import requests as req
import re, threading, time

app = Flask(__name__)

import os
COHERE_API_KEY = os.environ.get("COHERE_API_KEY")
# ══════════════════════════════════════════════════════════════════
#  PROMPTS
# ══════════════════════════════════════════════════════════════════

SYSTEM_PROMPT = """You are SymptomIQ, a careful and empathetic medical triage assistant.

You have deep medical knowledge. Use it — but ONLY to ask better follow-up questions
and help the user describe their symptoms more clearly.

=== ABSOLUTE RULES — NEVER BREAK THESE ===
1. NEVER name a specific disease as the user's diagnosis
2. NEVER say "you have X", "this is X", "sounds like X", "probably X", "could be X"
3. NEVER recommend a specific drug, dosage, supplement, or treatment
4. NEVER express certainty about any medical outcome
5. NEVER use alarming language that could cause panic
6. Ask ONLY ONE follow-up question per response
7. Keep responses short — 1–2 sentences maximum unless summarising
8. If VERIFIED MEDICAL CONTEXT is provided, treat it as your primary reference
9. You may use your own knowledge to supplement context — but conservatively
10. Risk level is always computed externally — NEVER state or guess a risk level yourself

=== YOUR ROLE ===
You are a symptom collection bot. You collect. You do not interpret.
A separate rule engine reads your collected data and computes risk.
You then summarise what was shared — nothing more."""

CORRECTION_PROMPT = """You are a speech-to-text correction assistant.
Fix grammar, arrange words properly, correct medical terms if obvious.
Return ONLY the corrected sentence — no explanation, no prefix, no quotes.
If already correct, return as-is."""

SUMMARY_INSTRUCTIONS = """Write 2–3 warm, factual sentences describing ONLY what the user reported.
Do NOT add interpretation, diagnosis, guesses, or medical opinion.
Do NOT reference risk level in your sentences.
Then write this exact recommendation on a new line, word for word: {recommendation}"""

# ══════════════════════════════════════════════════════════════════
#  HALLUCINATION FILTER
# ══════════════════════════════════════════════════════════════════

FORBIDDEN = [
    # Disease naming
    "you have ", "you've got", "you likely have", "you probably have",
    "this is malaria", "this is typhoid", "this is dengue", "this is covid",
    "this is flu", "this is pneumonia", "sounds like ", "looks like ",
    "could be malaria", "could be typhoid", "could be dengue",
    "probably malaria", "probably typhoid", "consistent with malaria",
    "consistent with typhoid", "indicative of", "suggests malaria",
    "suggests typhoid", "diagnosis", "diagnosed",
    # Drug / treatment advice
    "take paracetamol", "take ibuprofen", "take aspirin", "take amoxicillin",
    "take artemether", "take coartem", "take chloroquine",
    " mg ", " mg,", " mg.", "dosage", "dose of", "prescribed",
    "medication", "drug of choice", "antibiotic", "antimalarial",
    "treatment is", "treat with", "remedy",
    # False certainty
    "definitely ", "certainly ", "i am certain", "i am sure",
    "without doubt", "no doubt", "confirmed", "100%", "guaranteed",
    "test result", "blood test shows", "lab result",
]

SAFE_FALLBACK = (
    "Thank you for sharing that. To help me understand your situation better — "
    "could you describe when these symptoms first started?"
)

def is_safe(text: str) -> bool:
    t = text.lower()
    return not any(phrase in t for phrase in FORBIDDEN)


# ══════════════════════════════════════════════════════════════════
#  MEDICAL CONTEXT FETCHER  (Hybrid RAG)
# ══════════════════════════════════════════════════════════════════

SOURCE_META = {
    "MedlinePlus": {
        "label": "MedlinePlus (NIH)",
        "logo":  "https://medlineplus.gov/images/medlineplus-logo.png",
        "url":   "https://medlineplus.gov",
    },
    "WHO": {
        "label": "World Health Organization",
        "logo":  "https://www.who.int/ResourcePackages/WHO/assets/dist/images/logos/en/h-logo-blue.svg",
        "url":   "https://www.who.int",
    },
    "CDC": {
        "label": "CDC",
        "logo":  "https://www.cdc.gov/TemplatePackage/4.0/assets/imgs/favicon/apple-touch-icon.png",
        "url":   "https://www.cdc.gov",
    },
}

def _fetch_medlineplus(query: str) -> dict | None:
    try:
        r = req.get(
            "https://connect.medlineplus.gov/service",
            params={
                "mainSearchCriteria.v.dn": query,
                "knowledgeResponseType": "application/json",
                "informationRecipient": "IVL_PAT",
            },
            timeout=7,
        )
        entries = r.json().get("feed", {}).get("entry", [])[:3]
        snippets = []
        for e in entries:
            title   = e.get("title", {}).get("_value", "")
            summary = e.get("summary", {}).get("_value", "")
            # Strip HTML tags
            summary = re.sub(r"<[^>]+>", " ", summary).strip()[:350]
            if title and summary:
                snippets.append(f"{title}: {summary}")
        if snippets:
            return {"source": "MedlinePlus", "snippets": snippets}
    except Exception:
        pass
    return None


def _fetch_who(query: str) -> dict | None:
    try:
        token_resp = req.post(
            "https://icdaccessmanagement.who.int/connect/token",
            data={
                "client_id":     "b0a96820-9f02-4a83-8c1e-f1e95a2afa9b_ea753db5-bcd9-4d3d-8e7d-a6a5ebe3a3f0",
                "client_secret": "kHqhAlXyVgbfB2QcLjdO0nIh/0sMXS0Z9OWqtMQFAnk=",
                "scope":         "icdapi_access",
                "grant_type":    "client_credentials",
            },
            timeout=6,
        )
        token = token_resp.json().get("access_token", "")
        if not token:
            raise ValueError("no token")

        r = req.get(
            "https://id.who.int/icd/entity/search",
            params={"q": query, "useFlexisearch": "true", "flatResults": "true"},
            headers={
                "Authorization": f"Bearer {token}",
                "Accept":        "application/json",
                "API-Version":   "v2",
                "Accept-Language": "en",
            },
            timeout=7,
        )
        entities = r.json().get("destinationEntities", [])[:3]
        snippets = []
        for e in entities:
            title = e.get("title", "")
            defn  = e.get("definition", "")[:300]
            if title and defn:
                snippets.append(f"{title}: {defn}")
        if snippets:
            return {"source": "WHO", "snippets": snippets}
    except Exception:
        pass
    return None


def _fetch_cdc(query: str) -> dict | None:
    try:
        r = req.get(
            "https://tools.cdc.gov/api/v2/resources/media.json",
            params={
                "topic":       query,
                "mediatype":   "webpage",
                "language":    "english",
                "max":         3,
                "sort":        "date",
            },
            timeout=7,
        )
        items = r.json().get("results", [])[:3]
        snippets = []
        for item in items:
            name = item.get("name", "")
            desc = item.get("description", "")[:300]
            if name and desc:
                snippets.append(f"{name}: {desc}")
        if snippets:
            return {"source": "CDC", "snippets": snippets}
    except Exception:
        pass
    return None


def fetch_medical_context(symptoms: list) -> tuple[str, list]:
    """
    Fetches from all 3 sources in parallel.
    Returns (context_text, sources_used_list).
    sources_used_list items match SOURCE_META keys.
    """
    if not symptoms:
        return "", []

    query   = " ".join(symptoms[:3])  # limit query length
    results = [None, None, None]

    def run(fn, idx):
        results[idx] = fn(query)

    threads = [
        threading.Thread(target=run, args=(_fetch_medlineplus, 0)),
        threading.Thread(target=run, args=(_fetch_who,         1)),
        threading.Thread(target=run, args=(_fetch_cdc,         2)),
    ]
    for t in threads: t.start()
    for t in threads: t.join(timeout=8)

    snippets_all  = []
    sources_used  = []

    for r in results:
        if r and r.get("snippets"):
            snippets_all.extend(r["snippets"][:2])   # max 2 per source
            sources_used.append(r["source"])

    context_text = "\n\n".join(snippets_all[:6])      # hard cap at 6 total
    return context_text, sources_used


# ══════════════════════════════════════════════════════════════════
#  SYMPTOM / QUESTION / RULE ENGINE
# ══════════════════════════════════════════════════════════════════

SYMPTOM_KEYWORDS = {
    "fever":    ["fever","hot","high temperature","burning up","high temp","temperature"],
    "headache": ["headache","head pain","head ache","head hurts","migraine","pounding head"],
    "vomiting": ["vomit","vomiting","throwing up","nausea","nauseous","been sick","sick to my stomach"],
    "weakness": ["weak","weakness","tired","fatigue","fatigued","exhausted","no energy","lethargic"],
    "malaria":  ["malaria","chills","shivering","shaking","rigor"],
    "typhoid":  ["typhoid","diarrhea","diarrhoea","loose stool","stomach pain","abdominal pain","belly pain"],
    "cough":    ["cough","coughing","dry cough","wet cough"],
    "breathe":  ["breathing","breathless","shortness of breath","can't breathe","chest tight"],
    "rash":     ["rash","skin","spots","itching","hives"],
}

QUESTION_BANK = {
    "fever": [
        {"id":"temp",     "q":"Have you measured your temperature? If so, what is it?"},
        {"id":"duration", "q":"How many days have you had the fever?"},
        {"id":"chills",   "q":"Are you experiencing chills or shaking along with the fever?"},
    ],
    "headache": [
        {"id":"head_sev", "q":"Would you describe the headache as mild, moderate, or severe?"},
        {"id":"head_dur", "q":"How long have you had the headache?"},
    ],
    "vomiting": [
        {"id":"vomit_n",  "q":"Roughly how many times have you vomited today?"},
        {"id":"vomit_fl", "q":"Are you able to keep water or fluids down?"},
    ],
    "weakness": [
        {"id":"weak_lv",  "q":"Are you able to stand up and move around normally?"},
    ],
    "malaria": [
        {"id":"mal_trv",  "q":"Have you recently been to an area where malaria is common?"},
        {"id":"mal_swt",  "q":"Are you sweating heavily, especially at night?"},
    ],
    "typhoid": [
        {"id":"typh_wt",  "q":"Have you had untreated water or street food recently?"},
        {"id":"typh_bw",  "q":"Have you noticed any changes in your bowel movements?"},
    ],
    "cough": [
        {"id":"cough_t",  "q":"Is your cough dry, or are you coughing up mucus?"},
        {"id":"cough_d",  "q":"How long have you been coughing?"},
    ],
    "breathe": [
        {"id":"breath_r", "q":"Is the breathing difficulty constant or does it come and go?"},
    ],
    "rash": [
        {"id":"rash_loc", "q":"Where on your body is the rash, and does it itch?"},
    ],
}

def detect_symptoms(text: str) -> list:
    tl = text.lower()
    found = []
    for symptom, kws in SYMPTOM_KEYWORDS.items():
        if any(kw in tl for kw in kws):
            found.append(symptom)
    return list(set(found))


def extract_values(text: str, sd: dict) -> dict:
    tl = text.lower()
    # Temperature
    m = re.search(r'(\d+\.?\d*)\s*°?\s*[cC]', text)
    if m:
        sd["answers"]["temperature"] = float(m.group(1))
    else:
        m2 = re.search(r'\b(3[5-9]\.?\d*|40\.?\d*)\b', text)
        if m2:
            sd["answers"]["temperature"] = float(m2.group(1))
    # Duration
    m = re.search(r'(\d+)\s*day', tl)
    if m:
        sd["answers"]["duration_days"] = int(m.group(1))
    elif "week" in tl:
        sd["answers"]["duration_days"] = 7
    elif "yesterday" in tl or "since yesterday" in tl:
        sd["answers"]["duration_days"] = 1
    elif re.search(r'\btwo\b|\b2\b', tl) and "day" in tl:
        sd["answers"]["duration_days"] = 2
    # Vomit count
    m = re.search(r'(\d+)\s*time', tl)
    if m:
        sd["answers"]["vomiting_count"] = int(m.group(1))
    elif "once" in tl:
        sd["answers"]["vomiting_count"] = 1
    elif "twice" in tl:
        sd["answers"]["vomiting_count"] = 2
    elif re.search(r'many|several|multiple|a lot', tl):
        sd["answers"]["vomiting_count"] = 5
    # Breathing flag
    if "breathe" in sd.get("symptoms", []):
        sd["answers"]["breathing_issue"] = True
    return sd


def evaluate_risk(sd: dict) -> str:
    ans  = sd.get("answers", {})
    syms = sd.get("symptoms", [])
    temp = ans.get("temperature", 0)
    dur  = ans.get("duration_days", 0)
    vom  = ans.get("vomiting_count", 0)
    risk = "LOW"

    # Immediate HIGH flags
    if ans.get("breathing_issue"):
        return "HIGH"
    if "breathe" in syms:
        return "HIGH"

    # Fever evaluation
    if "fever" in syms:
        if temp >= 39.5 or dur >= 3:
            risk = "HIGH"
        elif temp >= 38.0 or dur >= 2:
            risk = "MEDIUM"
        elif temp > 0 or dur > 0:
            risk = "LOW"

    # Vomiting escalation
    if vom >= 3:
        risk = "HIGH" if risk == "MEDIUM" else "MEDIUM"
    elif vom > 0 and "fever" in syms and risk == "LOW":
        risk = "MEDIUM"

    # Weakness escalation
    if "weakness" in syms and risk == "MEDIUM":
        risk = "HIGH"

    # Tropical disease bump
    if ("malaria" in syms or "typhoid" in syms) and risk == "LOW":
        risk = "MEDIUM"

    return risk


def get_recommendation(risk: str) -> str:
    return {
        "HIGH":   "Please visit a clinic or hospital as soon as possible — do not delay.",
        "MEDIUM": "Consider visiting a clinic within the next 24 hours. Rest and stay hydrated.",
        "LOW":    "Rest, stay hydrated, and monitor your symptoms. See a doctor if they worsen.",
    }.get(risk, "Please consult a qualified doctor for a proper evaluation.")


def get_next_question(sd: dict) -> dict | None:
    asked = sd.get("asked_question_ids", [])
    for sym in sd.get("symptoms", []):
        for q in QUESTION_BANK.get(sym, []):
            if q["id"] not in asked:
                return q
    return None


def should_evaluate(sd: dict) -> bool:
    asked = sd.get("asked_question_ids", [])
    ans   = sd.get("answers", {})
    syms  = sd.get("symptoms", [])
    if len(asked) >= 4:                              return True
    if "temperature" in ans and "duration_days" in ans: return True
    if len(asked) >= 2 and len(syms) >= 3:           return True
    if ans.get("breathing_issue"):                   return True
    return False


# ══════════════════════════════════════════════════════════════════
#  COHERE CALLER
# ══════════════════════════════════════════════════════════════════

def call_cohere(history: list, message: str, preamble: str = None) -> str:
    url     = "https://api.cohere.ai/v1/chat"
    headers = {
        "Authorization": f"Bearer {COHERE_API_KEY}",
        "Content-Type":  "application/json",
    }
    chat_history = [
        {"role": "USER" if m["role"] == "user" else "CHATBOT", "message": m["content"]}
        for m in history
    ]
    payload = {
        "model":        "command-a-03-2025",
        "message":      message,
        "chat_history": chat_history,
        "preamble":     preamble or SYSTEM_PROMPT,
        "temperature":  0.2,
        "max_tokens":   400,
    }
    try:
        resp  = req.post(url, headers=headers, json=payload, timeout=30)
        reply = resp.json().get("text", "").strip()
        if not reply:
            return SAFE_FALLBACK
        # ── HALLUCINATION FILTER ──────────────────────────
        if not is_safe(reply):
            return SAFE_FALLBACK
        return reply
    except Exception:
        return "I'm having a connection issue. Please try again in a moment."


# ══════════════════════════════════════════════════════════════════
#  ROUTES
# ══════════════════════════════════════════════════════════════════

@app.route("/")
def home():
    return render_template("index.html")


@app.route("/correct", methods=["POST"])
def correct():
    """Clean up raw voice transcript."""
    raw = (request.json or {}).get("text", "").strip()
    if not raw:
        return jsonify({"corrected": raw})
    corrected = call_cohere([], raw, preamble=CORRECTION_PROMPT)
    corrected = corrected.strip().strip('"').strip("'")
    return jsonify({"corrected": corrected})


@app.route("/chat", methods=["POST"])
def chat():
    body    = request.json or {}
    msg     = body.get("message", "").strip()
    history = body.get("history", [])
    sd      = body.get("session", {
        "symptoms": [], "asked_question_ids": [],
        "answers":  {}, "stage": "start", "question_count": 0,
    })

    if not msg:
        return jsonify({"reply": "Please describe your symptoms.", "session": sd})

    # ── Completed session guard ───────────────────────────
    if sd.get("stage") == "done":
        return jsonify({
            "reply":   "Your assessment is complete. Tap the refresh icon to start a new session.",
            "session": sd,
            "type":    "info",
            "sources": [],
        })

    # ── Accumulate symptoms & values ─────────────────────
    for s in detect_symptoms(msg):
        if s not in sd["symptoms"]:
            sd["symptoms"].append(s)
    sd = extract_values(msg, sd)

    # ── Fetch medical context (parallel) ─────────────────
    context_text, sources_used = fetch_medical_context(sd["symptoms"])

    def build_context_block(task_instruction: str) -> str:
        if context_text:
            return (
                f"=== VERIFIED MEDICAL REFERENCE — use this as primary source ===\n"
                f"{context_text}\n\n"
                f"You may use your own medical knowledge to supplement the above, "
                f"but never go beyond collecting symptoms and asking questions.\n\n"
                f"--- {task_instruction} ---"
            )
        return task_instruction

    # ── Should we evaluate? ───────────────────────────────
    if sd["stage"] == "questioning" and should_evaluate(sd):
        risk   = evaluate_risk(sd)
        rec    = get_recommendation(risk)
        s_str  = ", ".join(sd["symptoms"]) or "the symptoms described"
        a_str  = str(sd["answers"]) if sd["answers"] else "no specific measurements"

        summary_task = (
            SUMMARY_INSTRUCTIONS.format(recommendation=rec) + "\n\n"
            f"Symptoms reported: {s_str}. Data collected: {a_str}."
        )
        ai_reply = call_cohere(history, build_context_block(summary_task))
        sd["stage"] = "done"
        return jsonify({
            "reply":          ai_reply,
            "session":        sd,
            "type":           "assessment",
            "risk":           risk,
            "recommendation": rec,
            "sources":        sources_used,
        })

    # ── Next question ─────────────────────────────────────
    next_q = get_next_question(sd)
    if next_q:
        sd["asked_question_ids"].append(next_q["id"])
        sd["stage"]          = "questioning"
        sd["question_count"] += 1
        task = f"User said: '{msg}'. Ask warmly in ONE sentence: {next_q['q']}"
        ai_reply = call_cohere(history, build_context_block(task))
        return jsonify({
            "reply":   ai_reply,
            "session": sd,
            "type":    "question",
            "sources": sources_used,
        })

    # ── Some symptoms but no more questions → evaluate ────
    if sd["symptoms"] or sd["answers"]:
        risk  = evaluate_risk(sd)
        rec   = get_recommendation(risk)
        s_str = ", ".join(sd["symptoms"]) or "the symptoms described"
        summary_task = (
            SUMMARY_INSTRUCTIONS.format(recommendation=rec) + "\n\n"
            f"Symptoms reported: {s_str}."
        )
        ai_reply = call_cohere(history, build_context_block(summary_task))
        sd["stage"] = "done"
        return jsonify({
            "reply":          ai_reply,
            "session":        sd,
            "type":           "assessment",
            "risk":           risk,
            "recommendation": rec,
            "sources":        sources_used,
        })

    # ── No symptoms yet — ask user to elaborate ───────────
    task = (
        f"User sent: '{msg}'. "
        "Welcome them warmly to SymptomIQ and ask them in ONE sentence to describe "
        "their symptoms (e.g. fever, headache, vomiting, weakness)."
    )
    ai_reply = call_cohere(history, build_context_block(task))
    return jsonify({
        "reply":   ai_reply,
        "session": sd,
        "type":    "question",
        "sources": sources_used,
    })


if __name__ == "__main__":
    app.run(debug=True)
