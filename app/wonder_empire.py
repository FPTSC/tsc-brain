"""
Wonder Empire — Fathom call analysis module.
Classifies WE team calls by type, analyzes them with type-specific prompts,
and saves results to Supabase.
"""

import json
import re
import requests

FATHOM_BASE = "https://api.fathom.ai/external/v1"

WE_COACH_NAMES = {"Claudia", "Irene", "Josy", "Jess", "Giulia", "Wonderlou", "Federica", "Lou"}
WE_COACH_USERNAME = {
    "Claudia": "claudia",
    "Irene": "irene",
    "Josy": "josy",
    "Jess": "jess",
    "Giulia": "giulia",
    "Wonderlou": "wonderlou",
    "Lou": "wonderlou",
    "Federica": "federica",
}
CALL_TYPES = ("onboarding", "vendita", "rinnovo", "check")

# ── Prompts ──────────────────────────────────────────────────────────────────

_CLASSIFY_PROMPT = """Classifica questa call del team coaching Wonder Empire.

Titolo Fathom: "{title}"
Partecipanti rilevati: {participants}
Trascrizione (inizio):
{transcript}

Categorie:
- "onboarding": prima call con nuova cliente (presentazione, raccolta dati anamnesi, setup app Trainerize/Macros)
- "check": call periodica di review progressi e aggiornamento scheda
- "vendita": call di vendita con prospect (proposta abbonamento, chiusura)
- "rinnovo": call di rinnovo abbonamento con cliente esistente

Coach del team WE: Claudia, Irene, Josy, Jess, Giulia, Wonderlou, Federica

Rispondi SOLO con JSON valido, zero testo extra:
{{"call_type": "onboarding|vendita|rinnovo|check", "coach": "nome esatto coach o null", "client_name": "nome cliente o null", "confidence": "high|medium|low"}}"""

_ONBOARDING_PROMPT = """Sei il coach di Wonder Empire che analizza una call di ONBOARDING con una nuova cliente.

STRUTTURA CORRETTA CALL DI ONBOARDING:
1. INTRODUZIONE + ALLINEAMENTO (3') — far percepire squadra e continuità.
   Frase corretta: "Ciao, piacere, io sono ___, sarò la coach che ti seguirà. Mi sono già confrontata con Lucrezia su di te e conosco i tuoi obiettivi, oggi approfondiamo tutto per costruire il tuo protocollo su misura."
   Obiettivo: la cliente deve sentirsi vista, ascoltata, capita.
2. COMFORT + RELAZIONE (2') — abbassare difese, creare fiducia. Domanda soft: "Che rapporto hai oggi con allenamento e alimentazione?"
3. ANAMNESI COMPLETA (10-15') — raccogliere:
   - Allenamento: giorni disponibili, dove si allena, attrezzatura, infortuni passati
   - Alimentazione: come mangia ora, fame nervosa, orari difficili
   - Stile di vita: lavoro/turni, sonno, stress, viaggi
   - Salute: patologie, farmaci, ormoni/ciclo, interventi
4. EMERSIONE PAIN POINT (5') — "Cosa ti fa stare più a disagio oggi?", "Quando ti guardi allo specchio, cosa non ti piace?", "Cosa ti ha fatto dire: basta, inizio?"
5. RINFORZO MOTIVAZIONALE (3') — collegare passato → presente → futuro: "Sei partita perché ___ — oggi sei qui perché ___ — e noi lavoreremo per portarti a ___"
6. SPIEGAZIONE METODO (5') — protocollo personalizzato, check ogni 4 settimane, video, costanza. "Non lavoriamo a caso: ogni 4 settimane valutiamo, correggiamo, miglioriamo."
7. CHIUSURA + PROSSIMI STEP (3') — cosa succede ora, quando riceve il protocollo, cosa deve fare. "Questo è il tuo percorso. Noi ci siamo, ma lo costruiamo insieme."

TRASCRIZIONE:
{transcript}

Analizza e struttura ESATTAMENTE così:

**SINTESI** — cosa è successo (2 righe max)

**STRUTTURA CALL**
- 1. Introduzione + Allineamento: ✅/⚠️/❌ — [nota specifica]
- 2. Comfort + Relazione: ✅/⚠️/❌ — [nota]
- 3. Anamnesi Completa: ✅/⚠️/❌ — [nota — specifica cosa manca se incompleta]
- 4. Pain Point: ✅/⚠️/❌ — [nota]
- 5. Motivazione: ✅/⚠️/❌ — [nota]
- 6. Metodo di Lavoro: ✅/⚠️/❌ — [nota]
- 7. Chiusura + Step: ✅/⚠️/❌ — [nota]

**FATTO BENE**
- [comportamento specifico con citazione dalla call tra virgolette] — perché funziona

**DA CORREGGERE**
- [errore specifico con citazione tra virgolette] — come si corregge: "[frase esatta da usare la prossima volta]"

**PROSSIMI STEP**
1. [azione concreta]
2. [azione concreta]
3. [azione concreta]

Regole: imperativo sempre. Vietato: "potresti", "sarebbe utile", "considera di". Non citare la cliente per nome. Non citare fonti esterne."""

_VENDITA_PROMPT = """Sei il coach di Wonder Empire che analizza una call di VENDITA.

MATERIALE DI RIFERIMENTO:
{context}

TRASCRIZIONE:
{transcript}

Valuta: apertura e rapport, identificazione bisogni/pain point, presentazione offerta, gestione obiezioni, chiusura e prossimi step.

**SINTESI** — cosa è successo, tipo di prospect, esito (2 righe max)

**FATTO BENE**
- [comportamento specifico con citazione tra virgolette] — perché funziona

**DA CORREGGERE**
- [errore specifico con citazione tra virgolette] — come si corregge: "[frase esatta]"

**PROSSIMI STEP**
1. [azione concreta]
2. [azione concreta]
3. [azione concreta]

Regole: imperativo sempre. Non citare la prospect per nome. Non citare autori o fonti esterne."""

_RINNOVO_PROMPT = """Sei il coach di Wonder Empire che analizza una call di RINNOVO.

MATERIALE DI RIFERIMENTO:
{context}

TRASCRIZIONE:
{transcript}

Principio: il rinnovo non si vende nell'ultima settimana, si costruisce durante tutto il percorso.
Valuta: come è stato seminato il rinnovo nel percorso, tempistica della proposta, gestione obiezioni finanziarie, proposta acconto, chiusura.

**SINTESI** — cosa è successo, esito del rinnovo (2 righe max)

**FATTO BENE**
- [comportamento specifico con citazione tra virgolette] — perché funziona

**DA CORREGGERE**
- [errore specifico con citazione tra virgolette] — come si corregge: "[frase esatta]"

**PROSSIMI STEP**
1. [azione concreta]
2. [azione concreta]
3. [azione concreta]

Regole: imperativo sempre. Non citare la cliente per nome. Non citare autori o fonti esterne."""

_CHECK_PROMPT = """Sei il coach di Wonder Empire che analizza una call di CHECK.

[Linee guida specifiche per il check in arrivo — per ora analisi generale.]

TRASCRIZIONE:
{transcript}

**SINTESI** — cosa è successo (2 righe max)

**FATTO BENE**
- [comportamento specifico] — perché funziona

**DA CORREGGERE**
- [errore specifico con citazione tra virgolette] — come si corregge: "[frase esatta]"

**PROSSIMI STEP**
1. [azione concreta]
2. [azione concreta]"""


# ── Fathom helpers ───────────────────────────────────────────────────────────

def fetch_recordings(api_key: str, since_date: str | None = None) -> list[dict]:
    """List all WE recordings from Fathom, newest first."""
    recs, cursor = [], None
    while True:
        params = {"limit": 50}
        if cursor:
            params["cursor"] = cursor
        r = requests.get(
            f"{FATHOM_BASE}/meetings",
            headers={"X-Api-Key": api_key},
            params=params,
            timeout=30,
        )
        r.raise_for_status()
        body = r.json()
        items = body.get("items", [])
        if since_date:
            items = [i for i in items if (i.get("started_at") or "") >= since_date]
        recs.extend(items)
        cursor = body.get("next_cursor")
        if not cursor or (since_date and len(items) < 50):
            break
    return recs


def fetch_transcript(api_key: str, recording_id: str) -> tuple[str, list[str]]:
    """
    Returns (formatted_transcript, speaker_names).
    Speaker names are used for coach detection.
    """
    r = requests.get(
        f"{FATHOM_BASE}/recordings/{recording_id}/transcript",
        headers={"X-Api-Key": api_key},
        timeout=30,
    )
    if r.status_code != 200:
        return "", []

    lines, speakers = [], set()
    for seg in r.json().get("transcript", []):
        name = seg.get("speaker", {}).get("display_name", "Unknown")
        text = seg.get("text", "").strip()
        speakers.add(name)
        if text:
            lines.append(f"{name}: {text}")

    return "\n".join(lines), list(speakers)


def _detect_coach(speakers: list[str]) -> str | None:
    """Match speaker list against known WE coach names."""
    for s in speakers:
        # exact match
        if s in WE_COACH_USERNAME:
            return WE_COACH_USERNAME[s]
        # partial match (e.g. "Claudia R." → "Claudia")
        for coach in WE_COACH_USERNAME:
            if coach.lower() in s.lower():
                return WE_COACH_USERNAME[coach]
    return None


# ── AI classification ────────────────────────────────────────────────────────

def classify_call(
    title: str,
    speakers: list[str],
    transcript: str,
    claude_client,
) -> dict:
    """
    Returns dict: {call_type, coach (username|None), client_name, confidence}
    Uses claude-haiku for speed.
    """
    prompt = _CLASSIFY_PROMPT.format(
        title=title,
        participants=", ".join(speakers) if speakers else "non rilevati",
        transcript=transcript[:3000],
    )
    resp = claude_client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=200,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = resp.content[0].text.strip()
    raw = re.sub(r"^```[a-z]*\n?", "", raw)
    raw = re.sub(r"\n?```$", "", raw)

    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        result = {"call_type": "check", "coach": None, "client_name": None, "confidence": "low"}

    # Override coach from speaker detection if AI missed it
    if not result.get("coach"):
        detected = _detect_coach(speakers)
        if detected:
            result["coach"] = detected
    else:
        # Normalize coach name to username
        coach_raw = result["coach"]
        result["coach"] = WE_COACH_USERNAME.get(coach_raw, coach_raw.lower() if coach_raw else None)

    return result


# ── AI analysis ──────────────────────────────────────────────────────────────

def analyze_call(
    call_type: str,
    transcript: str,
    search_fn,
    claude_client,
) -> str:
    """
    Returns the analysis text.
    For vendita/rinnovo: fetches relevant KB context first.
    For onboarding/check: uses fixed prompts.
    """
    if call_type == "onboarding":
        prompt = _ONBOARDING_PROMPT.format(transcript=transcript)
        system = "Sei il coach operativo di Wonder Empire. Parla diretto, usa l'imperativo."

    elif call_type == "check":
        prompt = _CHECK_PROMPT.format(transcript=transcript)
        system = "Sei il coach operativo di Wonder Empire. Parla diretto, usa l'imperativo."

    elif call_type in ("vendita", "rinnovo"):
        query = "rinnovo gestione obiezioni chiusura script" if call_type == "rinnovo" \
                else "vendita struttura call obiezioni apertura chiusura prospect"
        results = search_fn(query, n_results=5)
        context = "\n\n---\n\n".join(
            f"[{r['metadata'].get('titolo', '')}]\n{r['text']}"
            for r in results
        ) if results else "Nessun materiale trovato nella knowledge base."

        template = _RINNOVO_PROMPT if call_type == "rinnovo" else _VENDITA_PROMPT
        prompt = template.format(context=context, transcript=transcript)
        system = "Sei il coach operativo di Wonder Empire. Parla diretto, usa l'imperativo. Non citare fonti o autori esterni."

    else:
        prompt = _CHECK_PROMPT.format(transcript=transcript)
        system = "Sei il coach operativo di Wonder Empire. Parla diretto, usa l'imperativo."

    resp = claude_client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2000,
        system=system,
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.content[0].text


# ── Supabase helpers ─────────────────────────────────────────────────────────

def _sb_headers(service_key: str) -> dict:
    return {
        "apikey": service_key,
        "Authorization": f"Bearer {service_key}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal",
    }


def get_existing_ids(supabase_url: str, service_key: str) -> set[str]:
    """Return set of fathom_call_ids already in Supabase."""
    r = requests.get(
        f"{supabase_url}/rest/v1/call_analyses",
        headers={**_sb_headers(service_key), "Prefer": ""},
        params={"select": "fathom_call_id"},
        timeout=15,
    )
    if r.status_code != 200:
        return set()
    return {row["fathom_call_id"] for row in r.json()}


def save_to_supabase(
    supabase_url: str,
    service_key: str,
    fathom_call_id: str,
    date: str,
    coach_username: str,
    client_name: str,
    call_type: str,
    analysis: str,
    raw_transcript: str,
) -> bool:
    """Insert one call analysis. Returns True on success."""
    payload = {
        "fathom_call_id": fathom_call_id,
        "date": date,
        "coach_username": coach_username or "unknown",
        "client_name": client_name or "Sconosciuta",
        "call_type": call_type,
        "analysis": {"text": analysis},
        "raw_transcript": raw_transcript,
    }
    r = requests.post(
        f"{supabase_url}/rest/v1/call_analyses",
        headers=_sb_headers(service_key),
        json=payload,
        timeout=15,
    )
    return r.status_code < 300
