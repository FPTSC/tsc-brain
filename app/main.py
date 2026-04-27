import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi import FastAPI, Request, Form, File, UploadFile, Depends, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.security import HTTPBasic, HTTPBasicCredentials
import secrets
import anthropic
from groq import Groq

from config.settings import ANTHROPIC_API_KEY, CLAUDE_MODEL, GROQ_API_KEY
from src.vectorstore.client import search, count

APP_PASSWORD = os.environ.get("APP_PASSWORD", "tsc2024")
APP_USER = os.environ.get("APP_USER", "tsc")
PORT = int(os.environ.get("PORT", 9001))
REBUILD_INTERVAL = int(os.environ.get("REBUILD_INTERVAL_SECONDS", "3600"))

app = FastAPI(title="TSC Brain")
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")

security = HTTPBasic()
_claude = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
_groq = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None
_HTML_PATH = Path(__file__).parent / "templates" / "index.html"
_bg_tasks: set = set()

ANALYSIS_PROMPT = """Sei l'analista della knowledge base di THETA SALES CONSULTING (TSC).
Ti viene fornita la trascrizione di una sales call e il materiale della knowledge base TSC più rilevante.

Il tuo compito è analizzare la call rispetto alle metodologie, procedure e principi TSC.

Struttura la risposta così:
1. **Sintesi della call** — di cosa parlava, obiettivo percepito (2-3 righe)
2. **Cosa è stato fatto bene** — comportamenti in linea con la metodologia TSC (con riferimenti specifici al materiale)
3. **Aree di miglioramento** — errori o deviazioni rispetto alle procedure TSC (concreti e azionabili)
4. **Raccomandazioni** — 2-3 azioni specifiche per la prossima call simile

Regole:
- Rispondi sempre in italiano
- Sii diretto e concreto, non generico
- Ogni osservazione deve essere ancorata a un momento specifico della call o a un principio TSC
- Non attribuire mai osservazioni a una persona specifica con frasi come "come dice X" o "secondo X"
- Puoi citare frasi dalla call tra virgolette come esempi concreti, senza attribuirle a nessuno"""

SYSTEM_PROMPT = """Sei l'assistente della knowledge base di THETA SALES CONSULTING (TSC).
Il tuo compito è rispondere alle domande del team usando esclusivamente
il materiale estratto dalle sessioni di training, coaching e formazione registrate da TSC.

Regole:
- Rispondi sempre in italiano
- Presenta le informazioni come metodologie, procedure e principi di TSC — non attribuirle mai a una persona specifica (non usare mai "come dice Federico", "secondo Federico", "Federico spiega che" o formule simili)
- Se il materiale non contiene informazioni sufficienti per rispondere, dillo chiaramente
- Quando riporti procedure, usa elenchi numerati
- Quando riporti principi o regole, usa elenchi puntati
- Puoi riportare frasi o formulazioni esatte tratte dalle call come esempi pratici (es. gestione obiezioni, script), mettendole tra virgolette — senza indicare chi le ha dette"""


def _check_auth(credentials: HTTPBasicCredentials = Depends(security)):
    ok_user = secrets.compare_digest(credentials.username.encode(), APP_USER.encode())
    ok_pass = secrets.compare_digest(credentials.password.encode(), APP_PASSWORD.encode())
    if not (ok_user and ok_pass):
        raise HTTPException(
            status_code=401,
            detail="Credenziali non valide",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


def _do_rebuild():
    import importlib.util, traceback
    print("[rebuild] starting...", flush=True)
    try:
        spec = importlib.util.spec_from_file_location(
            "rebuild_index",
            str(Path(__file__).parent.parent / "scripts" / "rebuild_index.py"),
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.run()
        print("[rebuild] done", flush=True)
    except Exception as exc:
        print(f"[rebuild] FAILED: {exc}", flush=True)
        traceback.print_exc()


async def _run_rebuild_async():
    await asyncio.to_thread(_do_rebuild)


async def _init_background():
    print("[rebuild] background task started", flush=True)
    try:
        await asyncio.to_thread(count)
        print("[rebuild] chromadb ok", flush=True)
    except Exception as e:
        print(f"[rebuild] chromadb pre-warm failed: {e}", flush=True)
    await _run_rebuild_async()


async def _periodic_rebuild():
    print(f"[rebuild] periodic task started — interval {REBUILD_INTERVAL}s", flush=True)
    while True:
        await asyncio.sleep(REBUILD_INTERVAL)
        print("[rebuild] periodic trigger", flush=True)
        await _run_rebuild_async()


def _spawn(coro):
    task = asyncio.create_task(coro)
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)


@app.on_event("startup")
async def startup_event():
    _spawn(_init_background())
    _spawn(_periodic_rebuild())


@app.get("/", response_class=HTMLResponse)
async def index(_=Depends(_check_auth)):
    return FileResponse(_HTML_PATH)


@app.post("/api/rebuild")
async def rebuild(_=Depends(_check_auth)):
    _spawn(_run_rebuild_async())
    return JSONResponse({"status": "rebuild started"})


@app.get("/api/status")
async def status():
    try:
        doc_count = await asyncio.to_thread(count)
    except Exception:
        doc_count = 0
    return {"doc_count": doc_count, "status": "ok"}


@app.post("/api/query")
async def query(request: Request, question: str = Form(...), history: str = Form("[]"), _=Depends(_check_auth)):
    if not question.strip():
        return JSONResponse({"error": "Domanda vuota"}, status_code=400)

    results = search(question, n_results=5)

    if not results:
        return JSONResponse({
            "answer": "Il database è vuoto o non è stato ancora indicizzato.",
            "sources": [],
        })

    context = "\n\n---\n\n".join(
        f"[{r['metadata'].get('titolo', 'Senza titolo')}]\n{r['text']}"
        for r in results
    )

    try:
        conv_history = json.loads(history)
    except Exception:
        conv_history = []

    messages = [
        {"role": m["role"], "content": m["content"]}
        for m in conv_history
        if m.get("role") in ("user", "assistant")
    ]
    messages.append({
        "role": "user",
        "content": f"MATERIALE DALLA KNOWLEDGE BASE:\n\n{context}\n\n---\n\nDOMANDA: {question}",
    })

    message = _claude.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=2048,
        system=SYSTEM_PROMPT,
        messages=messages,
    )

    sources = [
        {"titolo": r["metadata"].get("titolo", ""), "url": r["metadata"].get("url", "")}
        for r in results
        if r["metadata"].get("titolo")
    ]

    return JSONResponse({
        "answer": message.content[0].text,
        "sources": sources,
    })


@app.post("/api/analyze-audio")
async def analyze_audio(request: Request, file: UploadFile = File(...), _=Depends(_check_auth)):
    if not _groq:
        return JSONResponse({"error": "GROQ_API_KEY non configurata."}, status_code=500)

    content = await file.read()
    if len(content) > 25 * 1024 * 1024:
        return JSONResponse({"error": "File troppo grande. Limite massimo: 25MB."}, status_code=400)

    try:
        transcription = await asyncio.to_thread(
            lambda: _groq.audio.transcriptions.create(
                file=(file.filename, content),
                model="whisper-large-v3-turbo",
                language="it",
                response_format="text",
            )
        )
        transcript = transcription if isinstance(transcription, str) else transcription.text
    except Exception as e:
        return JSONResponse({"error": f"Errore trascrizione: {e}"}, status_code=500)

    results = search(transcript[:1000], n_results=6)
    context = "\n\n---\n\n".join(
        f"[{r['metadata'].get('titolo', 'Senza titolo')}]\n{r['text']}"
        for r in results
    ) if results else "Nessun materiale rilevante trovato nella knowledge base."

    message = _claude.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=2048,
        system=ANALYSIS_PROMPT,
        messages=[{
            "role": "user",
            "content": f"MATERIALE DALLA KNOWLEDGE BASE:\n\n{context}\n\n---\n\nTRASCRIZIONE DELLA CALL:\n\n{transcript}",
        }],
    )

    sources = [
        {"titolo": r["metadata"].get("titolo", ""), "url": r["metadata"].get("url", "")}
        for r in results
        if r["metadata"].get("titolo")
    ]

    return JSONResponse({
        "filename": file.filename,
        "transcript": transcript,
        "answer": message.content[0].text,
        "sources": sources,
    })
