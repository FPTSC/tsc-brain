"""
Analizza tutte le sessioni Fathom e genera titolo/categoria/riassunto per ciascuna.
Uso: python scripts/analyze_sessions.py
"""
import json
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import anthropic
import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

FATHOM_API_KEY  = os.environ["FATHOM_API_KEY"]
ANTHROPIC_KEY   = os.environ["ANTHROPIC_API_KEY"]
FATHOM_BASE_URL = "https://api.fathom.ai/external/v1"
HEADERS         = {"X-Api-Key": FATHOM_API_KEY}


def list_all_recordings():
    recordings, cursor = [], None
    while True:
        params = {"limit": 50}
        if cursor:
            params["cursor"] = cursor
        r = requests.get(f"{FATHOM_BASE_URL}/meetings", headers=HEADERS, params=params)
        r.raise_for_status()
        body = r.json()
        recordings.extend(body.get("items", []))
        cursor = body.get("next_cursor")
        if not cursor:
            break
    return recordings


def get_transcript(recording_id):
    r = requests.get(
        f"{FATHOM_BASE_URL}/recordings/{recording_id}/transcript",
        headers=HEADERS,
    )
    if r.status_code != 200:
        return ""
    lines = []
    for seg in r.json().get("transcript", []):
        speaker = seg.get("speaker", {}).get("display_name", "Unknown")
        text    = seg.get("text", "").strip()
        if text:
            lines.append(f"{speaker}: {text}")
    return "\n".join(lines)


def analyze(title, transcript, client):
    prompt = f"""Analizza questa sessione TSC e rispondi SOLO con JSON valido, nessun altro testo.

Titolo Fathom: "{title}"
Trascrizione (primi 5000 caratteri):
{transcript[:5000]}

Genera:
- "titolo": titolo descrittivo in italiano (max 65 caratteri), dice esattamente cosa è stato trattato
- "categoria": una di ["formazione_vendita", "formazione_chat", "reclutamento", "programma_operativo", "revisione_call", "strategia_cliente", "onboarding_cliente", "interno_tsc", "altro"]
- "riassunto": 2 frasi max che spiegano il contenuto principale della sessione
- "cliente": nome del cliente se identificabile, altrimenti null

JSON valido:"""

    resp = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=400,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = resp.content[0].text.strip()
    raw = re.sub(r"^```[a-z]*\n?", "", raw)
    raw = re.sub(r"\n?```$", "", raw)
    return json.loads(raw)


CAT_ORDER = [
    "formazione_vendita",
    "formazione_chat",
    "reclutamento",
    "revisione_call",
    "strategia_cliente",
    "onboarding_cliente",
    "programma_operativo",
    "interno_tsc",
    "altro",
    "nessuna_trascrizione",
    "errore",
]

CAT_LABELS = {
    "formazione_vendita":    "FORMAZIONE VENDITA",
    "formazione_chat":       "FORMAZIONE CHAT",
    "reclutamento":          "RECLUTAMENTO",
    "revisione_call":        "REVISIONE CALL",
    "strategia_cliente":     "STRATEGIA CLIENTE",
    "onboarding_cliente":    "ONBOARDING CLIENTE",
    "programma_operativo":   "PROGRAMMA OPERATIVO",
    "interno_tsc":           "INTERNO TSC",
    "altro":                 "ALTRO",
    "nessuna_trascrizione":  "SENZA TRASCRIZIONE",
    "errore":                "ERRORI",
}


def main():
    claude = anthropic.Anthropic(api_key=ANTHROPIC_KEY)

    print("Recupero lista sessioni da Fathom...")
    recordings = list_all_recordings()
    print(f"Trovate {len(recordings)} sessioni.\n")

    results = []
    for i, rec in enumerate(recordings):
        rec_id       = str(rec["recording_id"])
        fathom_title = rec.get("title") or f"recording_{rec_id}"
        date_str     = (rec.get("started_at") or "")[:10]

        print(f"[{i+1}/{len(recordings)}] {fathom_title[:70]}", end="  ", flush=True)

        try:
            transcript = get_transcript(rec_id)
            if not transcript.strip():
                print("(nessuna trascrizione)")
                results.append({
                    "id": rec_id, "data": date_str,
                    "fathom_title": fathom_title, "titolo": fathom_title,
                    "categoria": "nessuna_trascrizione",
                    "riassunto": "Trascrizione non disponibile.", "cliente": None,
                })
                continue

            analysis = analyze(fathom_title, transcript, claude)
            analysis.update({"id": rec_id, "data": date_str, "fathom_title": fathom_title})
            results.append(analysis)
            print(f"→ {analysis.get('categoria', '?')}  [{analysis.get('cliente') or '—'}]")
        except Exception as e:
            print(f"ERRORE: {e}")
            results.append({
                "id": rec_id, "data": date_str,
                "fathom_title": fathom_title, "titolo": fathom_title,
                "categoria": "errore", "riassunto": str(e), "cliente": None,
            })

        time.sleep(0.25)

    # ── Riepilogo per categoria ──────────────────────────────
    print("\n\n" + "=" * 80)
    print("RIEPILOGO SESSIONI TSC")
    print("=" * 80)

    by_cat = {}
    for r in results:
        by_cat.setdefault(r.get("categoria", "altro"), []).append(r)

    for cat in CAT_ORDER:
        items = by_cat.get(cat)
        if not items:
            continue
        print(f"\n── {CAT_LABELS.get(cat, cat.upper())} ({len(items)}) " + "─" * 40)
        for r in sorted(items, key=lambda x: x.get("data", ""), reverse=True):
            cliente_str = f"  [{r['cliente']}]" if r.get("cliente") else ""
            print(f"  {r.get('data', '?'):<12} {r.get('titolo', r['fathom_title'])}{cliente_str}")
            if r.get("riassunto"):
                for line in r["riassunto"].split(". "):
                    line = line.strip()
                    if line:
                        print(f"               → {line.rstrip('.')}.")

    out = Path(__file__).parent.parent / "sessions_analysis.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n\nDettaglio completo salvato in: {out}")


if __name__ == "__main__":
    main()
