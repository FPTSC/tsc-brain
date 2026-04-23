import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi import FastAPI, Request, Form, Depends, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles

from fastapi.security import HTTPBasic, HTTPBasicCredentials
import secrets
import anthropic

from config.settings import ANTHROPIC_API_KEY, CLAUDE_MODEL
from src.vectorstore.client import search, count

APP_PASSWORD = os.environ.get("APP_PASSWORD", "tsc2024")
APP_USER = os.environ.get("APP_USER", "tsc")
PORT = int(os.environ.get("PORT", 9001))

app = FastAPI(title="TSC Brain")
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")


@app.on_event("startup")
async def startup_event():
    import asyncio
    asyncio.create_task(_init_background())


async def _init_background():
    import asyncio
    # Pre-warm ChromaDB in a thread so it doesn't block the event loop
    await asyncio.to_thread(count)
    try:
        import subprocess, sys
        subprocess.Popen(
            [sys.executable, str(Path(__file__).parent.parent / "scripts" / "rebuild_index.py")]
        )
    except Exception:
        pass
security = HTTPBasic()
_claude = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

_HTML_PATH = Path(__file__).parent / "templates" / "index.html"

SYSTEM_PROMPT = """Sei l'assistente della knowledge base di THETA SALES CONSULTING (TSC).
Il tuo compito è rispondere alle domande di Federico e del suo team usando esclusivamente
il materiale estratto dalle sessioni di training, coaching e formazione di Federico.

Regole:
- Rispondi sempre in italiano
- Cita esplicitamente i concetti, procedure e principi trovati nel materiale
- Se il materiale non contiene informazioni sufficienti per rispondere, dillo chiaramente
- Quando riporti procedure, usa elenchi numerati
- Quando riporti principi o regole, usa elenchi puntati
- Quando riporti citazioni di Federico, mettile tra virgolette"""


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


@app.get("/", response_class=HTMLResponse)
async def index(_=Depends(_check_auth)):
    return FileResponse(_HTML_PATH)


@app.get("/api/status")
async def status():
    try:
        doc_count = await asyncio.to_thread(count)
    except Exception:
        doc_count = 0
    return {"doc_count": doc_count, "status": "ok"}


@app.post("/api/query")
async def query(request: Request, question: str = Form(...), _=Depends(_check_auth)):
    if not question.strip():
        return JSONResponse({"error": "Domanda vuota"}, status_code=400)

    results = search(question, n_results=5)

    if not results:
        return JSONResponse({
            "answer": "Il database è vuoto o non è stato ancora indicizzato. Esegui `python scripts/rebuild_index.py`.",
            "sources": [],
        })

    context = "\n\n---\n\n".join(
        f"[{r['metadata'].get('titolo', 'Senza titolo')}]\n{r['text']}"
        for r in results
    )

    message = _claude.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=2048,
        system=SYSTEM_PROMPT,
        messages=[{
            "role": "user",
            "content": f"MATERIALE DALLA KNOWLEDGE BASE:\n\n{context}\n\n---\n\nDOMANDA: {question}",
        }],
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
