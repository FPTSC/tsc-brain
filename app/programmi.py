import json
import os
import re
import threading
from datetime import datetime
from pathlib import Path

from fpdf import FPDF

_DATA_DIR = Path(os.environ.get("USERS_FILE", "/data/users.json")).parent
PO_DIR    = _DATA_DIR / "programmi-operativi"
PO_INDEX  = PO_DIR / "index.json"
_LOCK     = threading.Lock()


def _ensure():
    PO_DIR.mkdir(parents=True, exist_ok=True)


def load_index() -> dict:
    _ensure()
    if PO_INDEX.exists():
        try:
            return json.loads(PO_INDEX.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_index(data: dict):
    _ensure()
    with _LOCK:
        PO_INDEX.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _safe(name: str) -> str:
    s = re.sub(r"[^\w\s-]", "", name.lower())
    return re.sub(r"[\s-]+", "_", s).strip("_") or "cliente"


def next_numero(client_name: str) -> int:
    return len(load_index().get(client_name, [])) + 1


def classify(text: str, client_name: str, claude_client) -> dict:
    prompt = f"""Sei un assistente che analizza trascrizioni di chiamate aziendali in italiano.

Analizza questa chiamata tra TSC e il cliente "{client_name}".

Classifica come "programma_operativo" se nella chiamata emerge ALMENO UNA di queste situazioni:
- qualcuno dice cosa deve fare (es. "devo mandare", "lo facciamo noi", "dobbiamo sistemare", "bisogna fare")
- vengono assegnate responsabilità concrete (es. "ci penso io", "lo fai tu", "lo facciamo noi")
- si pianifica la creazione di qualcosa (piattaforma, documento, campagna, script, processo)
- si fissano scadenze o priorità (es. "entro quando", "la prossima settimana", "più urgente")
- si decide di procurarsi qualcosa o qualcuno (coach, risorsa, strumento)
- si parla di materiali da inviare o ricevere

Classifica come "formazione_consulenza" SOLO se la chiamata è interamente dedicata a coaching, revisione performance, metodologia di vendita, o analisi di call passate — senza nessuna azione concreta assegnata.

In caso di dubbio, scegli "programma_operativo".

Se è "programma_operativo", estrai:
- titolo breve (es. "Piattaforma Team + Reclutamento Coach")
- riepilogo in 2-3 frasi di cosa è stato deciso
- ogni attività menzionata, anche implicitamente: chi deve fare cosa

Rispondi SOLO con JSON valido:
{{
  "tipo": "formazione_consulenza",
  "titolo": null,
  "riepilogo": null,
  "attivita": null
}}
oppure:
{{
  "tipo": "programma_operativo",
  "titolo": "...",
  "riepilogo": "...",
  "attivita": [{{"descrizione": "...", "responsabile": "TSC", "scadenza": null}}]
}}

TRASCRIZIONE:
{text[:8000]}"""

    resp = claude_client.messages.create(
        model=os.environ.get("CLAUDE_MODEL", "claude-opus-4-7"),
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = resp.content[0].text.strip()
    raw = re.sub(r"^```[a-z]*\n?", "", raw)
    raw = re.sub(r"\n?```$", "", raw)
    return json.loads(raw)


def generate_pdf(numero: int, client_name: str, titolo: str, riepilogo: str,
                 attivita: list, created_by: str) -> bytes:
    pdf = FPDF()
    pdf.set_margins(20, 20, 20)
    pdf.add_page()

    # Header azienda
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_text_color(140, 140, 140)
    pdf.cell(0, 6, "THETA SALES CONSULTING", align="R")
    pdf.ln(12)

    # Titolo documento
    pdf.set_font("Helvetica", "B", 18)
    pdf.set_text_color(13, 27, 42)
    pdf.cell(0, 10, f"PROGRAMMA OPERATIVO  N° {numero:02d}", align="C")
    pdf.ln(7)

    # Sottotitolo
    pdf.set_font("Helvetica", "B", 12)
    pdf.set_text_color(0, 120, 160)
    pdf.cell(0, 8, titolo or "", align="C")
    pdf.ln(8)

    # Meta-info
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(100, 100, 100)
    today = datetime.now().strftime("%d/%m/%Y")
    pdf.cell(0, 6, f"Cliente: {client_name}   ·   Data: {today}   ·   Creato da: {created_by}", align="C")
    pdf.ln(12)

    # Linea separatrice
    pdf.set_draw_color(200, 215, 225)
    pdf.line(20, pdf.get_y(), 190, pdf.get_y())
    pdf.ln(10)

    # Riepilogo
    if riepilogo:
        pdf.set_font("Helvetica", "B", 11)
        pdf.set_text_color(13, 27, 42)
        pdf.cell(0, 7, "RIEPILOGO")
        pdf.ln(7)
        pdf.set_font("Helvetica", "", 10)
        pdf.set_text_color(60, 60, 60)
        pdf.multi_cell(0, 6, riepilogo)
        pdf.ln(10)

    # Attività
    if attivita:
        pdf.set_font("Helvetica", "B", 11)
        pdf.set_text_color(13, 27, 42)
        pdf.cell(0, 7, "ATTIVITÀ PROGRAMMATE")
        pdf.ln(7)
        pdf.set_draw_color(200, 215, 225)
        pdf.line(20, pdf.get_y(), 190, pdf.get_y())
        pdf.ln(8)

        for i, a in enumerate(attivita, 1):
            # Numero attività
            pdf.set_font("Helvetica", "B", 11)
            pdf.set_text_color(0, 140, 180)
            pdf.cell(10, 7, f"{i}.")
            # Descrizione
            pdf.set_font("Helvetica", "B", 10)
            pdf.set_text_color(20, 20, 20)
            x_before = pdf.get_x()
            pdf.multi_cell(0, 7, a.get("descrizione", ""))

            # Metadati
            details = []
            resp = a.get("responsabile")
            scad = a.get("scadenza")
            if resp:
                details.append(f"Responsabile: {resp}")
            if scad:
                details.append(f"Entro: {scad}")
            if details:
                pdf.set_font("Helvetica", "I", 9)
                pdf.set_text_color(130, 130, 130)
                pdf.cell(10)
                pdf.cell(0, 5, "   ·   ".join(details))
                pdf.ln(5)
            pdf.ln(5)

    # Footer
    pdf.set_y(-22)
    pdf.set_draw_color(200, 215, 225)
    pdf.line(20, pdf.get_y(), 190, pdf.get_y())
    pdf.ln(5)
    pdf.set_font("Helvetica", "I", 8)
    pdf.set_text_color(160, 160, 160)
    pdf.cell(0, 5, f"Generato da TSC Brain  ·  {datetime.now().strftime('%d/%m/%Y %H:%M')}", align="C")

    return bytes(pdf.output())


def create(transcript: str, client_name: str, created_by: str, claude_client=None) -> dict:
    """Main entry point. Returns {tipo, programma?} or raises on error."""
    if claude_client is None:
        import anthropic
        claude_client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
    result = classify(transcript, client_name, claude_client)
    tipo   = result.get("tipo", "formazione_consulenza")

    if tipo != "programma_operativo":
        return {"tipo": tipo}

    titolo    = result.get("titolo") or "Programma Operativo"
    riepilogo = result.get("riepilogo") or ""
    attivita  = result.get("attivita") or []
    numero    = next_numero(client_name)

    pdf_bytes = generate_pdf(numero, client_name, titolo, riepilogo, attivita, created_by)
    fname     = f"{_safe(client_name)}_{numero:03d}.pdf"
    _ensure()
    (PO_DIR / fname).write_bytes(pdf_bytes)

    entry = {
        "numero":     numero,
        "titolo":     titolo,
        "riepilogo":  riepilogo,
        "attivita":   attivita,
        "filename":   fname,
        "created_at": datetime.now().isoformat(),
        "created_by": created_by,
    }
    idx = load_index()
    idx.setdefault(client_name, []).append(entry)
    _save_index(idx)

    return {"tipo": "programma_operativo", "programma": entry}
