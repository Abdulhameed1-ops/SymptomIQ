# SymptomIQ — MVP Prototype

A mobile-first AI medical symptom-checker with hybrid RAG grounding,
hard hallucination filtering, voice mode, and a WebGL orb.

---

## Setup

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Run the server
python app.py

# 3. Open in browser
http://localhost:5000
```

Use Chrome or Safari for voice mode (Web Speech API support).

---

## Project Structure

```
symptomiq/
├── app.py                  ← Flask backend
├── requirements.txt
├── templates/
│   └── index.html          ← App shell
└── static/
    ├── style.css           ← All styles
    └── app.js              ← Chat, voice, orb logic
```

---

## How Hallucinations Are Prevented

1. **System prompt** — AI is forbidden from diagnosing, naming diseases, or recommending drugs.
2. **Output filter** — Every AI response is scanned for ~30 forbidden phrases before being shown to the user. If triggered, a safe fallback message is shown instead.
3. **Rule engine** — Risk level (LOW / MEDIUM / HIGH) is computed in pure Python from collected data. The AI never decides severity.
4. **Hybrid RAG** — MedlinePlus, WHO ICD-11, and CDC data is fetched and injected into the AI prompt as a grounding reference. AI uses this as its primary source.
5. **Low temperature** — Cohere runs at temperature=0.2, minimising creative/random outputs.
6. **Role constraint** — AI is explicitly told it is a "symptom collection bot" that collects, not interprets.

---

## Safety Notice

This app is a prototype and is NOT a medical device.
It does not replace professional medical diagnosis or advice.
Always consult a qualified doctor for any health concerns.

---

## API Keys

Replace `COHERE_API_KEY` in `app.py` with your own key from https://cohere.com
