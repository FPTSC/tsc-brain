import asyncio
import json
import os
import re
import sys
import threading
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi import FastAPI, Request, Form, File, UploadFile, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.security import HTTPBasic, HTTPBasicCredentials
import secrets
import bcrypt
import anthropic
from groq import Groq

from config.settings import ANTHROPIC_API_KEY, CLAUDE_MODEL, GROQ_API_KEY, FATHOM_API_KEY, \
    WE_FATHOM_API_KEY, WE_API_KEY, WE_SUPABASE_URL, WE_SUPABASE_KEY
from src.vectorstore.client import search, count
from src.processor.extractor import extract_call_data, extract_from_coaching
from src.notion.client import save_call
from app import programmi

APP_PASSWORD     = os.environ.get("APP_PASSWORD", "tsc2024")
APP_USER         = os.environ.get("APP_USER", "tsc")
HATTING_API_KEY  = os.environ.get("HATTING_API_KEY", "")

USERS_FILE_PATH = Path(os.environ.get("USERS_FILE", str(Path(__file__).parent.parent / "users.json")))
_USERS_LOCK = threading.Lock()


def _normalize_users(raw: dict) -> dict:
    """Normalize flat {user: hash} format to {user: {hash, admin}} format."""
    result = {}
    for k, v in raw.items():
        result[k] = v if isinstance(v, dict) else {"hash": v, "admin": False}
    return result


def _load_users() -> dict:
    """Load users from USERS_FILE or USERS_JSON env var. Returns empty dict if none configured."""
    if USERS_FILE_PATH.exists():
        try:
            return _normalize_users(json.loads(USERS_FILE_PATH.read_text()))
        except Exception:
            pass
    users_json = os.environ.get("USERS_JSON", "")
    if users_json:
        try:
            return _normalize_users(json.loads(users_json))
        except Exception:
            pass
    return {}


def _save_users(users: dict) -> None:
    with _USERS_LOCK:
        USERS_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
        USERS_FILE_PATH.write_text(json.dumps(users, indent=2, ensure_ascii=False))


_USERS: dict = _load_users()


def _verify_credentials(credentials: HTTPBasicCredentials) -> tuple[bool, dict | None]:
    """Returns (is_valid, user_entry). Falls back to APP_USER/APP_PASSWORD when no users configured."""
    if _USERS:
        entry = _USERS.get(credentials.username)
        if entry and bcrypt.checkpw(credentials.password.encode(), entry["hash"].encode()):
            return True, entry
        return False, None
    valid = secrets.compare_digest(
        credentials.username.encode(), APP_USER.encode()
    ) and secrets.compare_digest(
        credentials.password.encode(), APP_PASSWORD.encode()
    )
    return valid, {"hash": "", "admin": True} if valid else None
PORT = int(os.environ.get("PORT", 9001))
REBUILD_INTERVAL = int(os.environ.get("REBUILD_INTERVAL_SECONDS", "3600"))
FATHOM_INTERVAL = int(os.environ.get("FATHOM_INTERVAL_SECONDS", "300"))

app = FastAPI(title="TSC Brain")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")

security = HTTPBasic()
_claude = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
_groq = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None
_HTML_PATH = Path(__file__).parent / "templates" / "index.html"
_bg_tasks: set = set()
_jobs: dict = {}
_JOB_TTL = 600

# ── Hatting docs da sincronizzare ───────────────────────────
_HATTING_BASE = "https://tsc-platform.vercel.app/"
_HATTING_DOCS = [
    ("assets/docs/scaling/stage01-monetizza.html",                    "Stage 1: Monetizza",                    "scaling"),
    ("assets/docs/scaling/stage02-pubblicizza.html",                  "Stage 2: Pubblicizza",                  "scaling"),
    ("assets/docs/scaling/stage03-stabilizza.html",                   "Stage 3: Stabilizza",                   "scaling"),
    ("assets/docs/scaling/stage04-prioritizza.html",                  "Stage 4: Prioritizza",                  "scaling"),
    ("assets/docs/scaling/stage05-productizza.html",                  "Stage 5: Productizza",                  "scaling"),
    ("assets/docs/scaling/stage06-ottimizza.html",                    "Stage 6: Ottimizza",                    "scaling"),
    ("assets/docs/scaling/stage07-categorizza.html",                  "Stage 7: Categorizza",                  "scaling"),
    ("assets/docs/scaling/stage08-specializza.html",                  "Stage 8: Specializza",                  "scaling"),
    ("assets/docs/scaling/stage09-capitalizza.html",                  "Stage 9: Capitalizza",                  "scaling"),
    ("assets/docs/consulente-vendita/struttura-call.html",            "Struttura della Call",                  "vendita"),
    ("assets/docs/struttura-call-vendita.html",                       "Scheda Rapida — 5 Fasi",                "vendita"),
    ("assets/docs/consulente-vendita/negoziazione.html",              "Negoziazione",                          "vendita"),
    ("assets/docs/consulente-vendita/obiezioni.html",                 "Anticipazione e Gestione Obiezioni",    "vendita"),
    ("assets/docs/bibbia-obiezioni.html",                             "Bibbia delle Obiezioni",                "vendita"),
    ("assets/docs/consulente-vendita/obiezioni-reframing.html",       "Obiezioni e Reframing",                 "vendita"),
    ("assets/docs/consulente-vendita/script-personalizzato.html",     "Script Personalizzato",                 "vendita"),
    ("assets/docs/consulente-vendita/roleplay.html",                  "Struttura del Roleplay",                "vendita"),
    ("assets/docs/consulente-vendita/roleplay-call.html",             "Roleplay e Gestione Call",              "vendita"),
    ("assets/docs/consulente-vendita/ascolto-chiamate.html",          "Analisi Call — Processo Operativo",     "vendita"),
    ("assets/docs/consulente-vendita/formazione-rinnovi.html",        "Rinnovi (Bozza)",                       "vendita"),
    ("assets/docs/consulente-vendita/rinnovi-completo.html",          "Rinnovi: Framework Completo",           "vendita"),
    ("assets/docs/consulente-vendita/produttivita-team.html",         "Produttività del Team",                 "vendita"),
    ("assets/docs/consulente-vendita/chat-sensibilizzazione.html",    "Chat e Sensibilizzazione",              "vendita"),
    ("assets/docs/consulente-vendita/selezione-completa.html",        "Selezione e Colloquio Completo",        "vendita"),
    ("assets/docs/consulente-marketing/format-angoli-contenuti.html", "Format Veloci e Angoli Comunicativi",  "marketing"),
    ("assets/docs/consulente-marketing/revisione-profili-contenuti.html", "Revisione Profilo e Contenuti",    "marketing"),
    ("assets/docs/consulente-marketing/stories-autorevole.html",      "Stories per Autorevolezza",             "marketing"),
    ("assets/docs/consulente-marketing/ads-dirette-competitor.html",  "Ads Dirette e Analisi Competitor",      "marketing"),
]

ANALYSIS_PROMPT = """Sei il coach di THETA SALES CONSULTING (TSC) che analizza una sales call.
Parla diretto. Niente introduzioni, niente conclusioni morbide.

Struttura la risposta esattamente così:

**SINTESI** — cosa è successo in questa call (2 righe max)

**FATTO BENE**
- [comportamento specifico] — perché funziona
- [altro comportamento]

**DA CORREGGERE**
- [errore specifico con esempio dalla call tra virgolette] — come si corregge: "[frase esatta da usare la prossima volta]"
- [altro errore]

**PROSSIMA CALL**
1. [azione concreta 1]
2. [azione concreta 2]
3. [azione concreta 3]

Regole:
- Usa l'imperativo. "Usa X. Evita Y. Quando succede Z, dì così."
- Vietato: "potresti", "sarebbe utile", "considera di"
- Ogni punto ancorato a un momento specifico della call
- Non attribuire mai a persone specifiche — cita le frasi tra virgolette senza nomi
- Non citare autori esterni, libri, nomi di file o fonti
- Non menzionare mai Scientology o organizzazioni correlate
- Tutto è metodologia interna di Theta Sales Consulting"""

SYSTEM_PROMPT = """Sei il coach operativo della knowledge base di THETA SALES CONSULTING (TSC).
Parli come un coach diretto, non come un assistente.

TONO E STILE:
- Usa l'imperativo presente. "Fai X. Poi Y. Se succede Z, rispondi così."
- Vietato: "potresti", "sarebbe utile", "ti consiglio di", "potresti provare", "considera di" — sostituisci sempre con l'imperativo
- Risposte brevi e dense. Max 5-6 punti. Niente paragrafi di introduzione o conclusione
- Se c'è uno script da usare, scrivilo parola per parola tra virgolette — pronto da usare in call
- Se il materiale non è sufficiente, dillo in una riga e suggerisci cosa fare

MEMORIA DI SESSIONE:
- Tieni traccia del contesto stabilito nella conversazione (cliente, situazione, obiettivo)
- Se il consulente ha menzionato con chi sta lavorando o qual è la situazione, usalo in ogni risposta successiva
- Adatta le risposte alla situazione specifica — non dare risposte generiche quando hai contesto

COMANDO "PREPARA CALL":
Quando ricevi "prepara call [nome]" o una frase simile, avvia questo protocollo:
1. Fai UNA domanda alla volta — aspetta sempre la risposta prima di fare la successiva
2. Le domande da fare (nell'ordine, adattale al contesto):
   - "Da quanto tempo lavorate insieme? È un rinnovo, una nuova proposta o altro?"
   - "Qual è la situazione attuale del cliente? (fatturato, team, problema principale)"
   - "Cosa ti aspetti come obiezione principale?"
   - "Qual è il tuo obiettivo preciso per questa call — cosa deve succedere perché vada bene?"
3. Dopo le risposte, genera il briefing con questo formato esatto:

**CLIENTE:** [nome]
**OBIETTIVO CALL:** [specifico e misurabile]
**APERTURA:** "[prima frase esatta da dire]"
**OBIEZIONI PROBABILI:**
- [obiezione 1] → "[risposta pronta parola per parola]"
- [obiezione 2] → "[risposta pronta parola per parola]"
**SEGNALE DI CHIUSURA:** [cosa ascoltare/cercare]
**SE SÌ:** [prossimo step preciso]
**SE NO:** [come gestire e cosa salvare]

REGOLE INVARIABILI:
- Rispondi sempre in italiano
- Non citare mai autori esterni, libri, nomi di file o documenti
- Tutto il materiale è metodologia interna di Theta Sales Consulting
- Non menzionare mai Scientology o organizzazioni correlate
- Non usare mai "il principio TSC", "secondo TSC" — esprimi i contenuti direttamente"""


def _check_auth(credentials: HTTPBasicCredentials = Depends(security)):
    valid, _ = _verify_credentials(credentials)
    if not valid:
        raise HTTPException(
            status_code=401,
            detail="Credenziali non valide",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


def _check_admin(credentials: HTTPBasicCredentials = Depends(security)):
    valid, entry = _verify_credentials(credentials)
    if not valid:
        raise HTTPException(
            status_code=401,
            detail="Credenziali non valide",
            headers={"WWW-Authenticate": "Basic"},
        )
    if not entry or not entry.get("admin", False):
        raise HTTPException(status_code=403, detail="Accesso riservato agli amministratori")
    return credentials.username


_ADMIN_HTML = Path(__file__).parent / "templates" / "admin.html"


@app.get("/admin", response_class=HTMLResponse)
async def admin_panel(_: str = Depends(_check_admin)):
    return FileResponse(_ADMIN_HTML)


@app.get("/api/admin/users")
async def list_users(_: str = Depends(_check_admin)):
    return [
        {"username": k, "admin": v.get("admin", False)}
        for k, v in _USERS.items()
    ]


@app.post("/api/admin/users")
async def add_user(
    username: str = Form(...),
    password: str = Form(...),
    is_admin: bool = Form(False),
    current_user: str = Depends(_check_admin),
):
    username = username.strip()
    if not username:
        return JSONResponse({"error": "Username non valido"}, status_code=400)
    if len(password) < 6:
        return JSONResponse({"error": "Password troppo corta (min. 6 caratteri)"}, status_code=400)
    if username in _USERS:
        return JSONResponse({"error": f"Utente '{username}' già esistente"}, status_code=400)

    hashed = await asyncio.to_thread(
        lambda: bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    )
    # If transitioning from legacy auth, persist the current admin too
    if current_user not in _USERS:
        legacy_hash = await asyncio.to_thread(
            lambda: bcrypt.hashpw(APP_PASSWORD.encode(), bcrypt.gensalt()).decode()
        )
        _USERS[current_user] = {"hash": legacy_hash, "admin": True}

    _USERS[username] = {"hash": hashed, "admin": is_admin}
    await asyncio.to_thread(_save_users, _USERS)
    return JSONResponse({"username": username, "admin": is_admin})


@app.delete("/api/admin/users/{username}")
async def remove_user(username: str, current_user: str = Depends(_check_admin)):
    if username not in _USERS:
        return JSONResponse({"error": f"Utente '{username}' non trovato"}, status_code=404)
    if username == current_user:
        return JSONResponse({"error": "Non puoi rimuovere te stesso"}, status_code=400)
    del _USERS[username]
    await asyncio.to_thread(_save_users, _USERS)
    return JSONResponse({"username": username, "removed": True})


@app.patch("/api/admin/users/{username}")
async def toggle_admin(username: str, current_user: str = Depends(_check_admin)):
    if username not in _USERS:
        return JSONResponse({"error": f"Utente '{username}' non trovato"}, status_code=404)
    if username == current_user:
        return JSONResponse({"error": "Non puoi modificare te stesso"}, status_code=400)
    _USERS[username]["admin"] = not _USERS[username].get("admin", False)
    await asyncio.to_thread(_save_users, _USERS)
    return JSONResponse({"username": username, "admin": _USERS[username]["admin"]})


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


async def _periodic_fathom():
    if not FATHOM_API_KEY:
        print("[fathom] FATHOM_API_KEY not set — skipping", flush=True)
        return
    from src.pipeline import run as pipeline_run
    print(f"[fathom] periodic task started — interval {FATHOM_INTERVAL}s", flush=True)
    while True:
        await asyncio.sleep(FATHOM_INTERVAL)
        try:
            n = await asyncio.to_thread(pipeline_run)
            if n:
                print(f"[fathom] {n} nuove sessioni importate, avvio rebuild", flush=True)
                await _run_rebuild_async()
            else:
                print("[fathom] nessuna nuova sessione", flush=True)
        except Exception as exc:
            print(f"[fathom] errore: {exc}", flush=True)


def _spawn(coro):
    task = asyncio.create_task(coro)
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)


@app.on_event("startup")
async def startup_event():
    _spawn(_init_background())
    _spawn(_periodic_rebuild())
    _spawn(_periodic_fathom())


@app.get("/", response_class=HTMLResponse)
async def index(_=Depends(_check_auth)):
    return FileResponse(_HTML_PATH)


@app.post("/api/rebuild")
async def rebuild(_=Depends(_check_auth)):
    _spawn(_run_rebuild_async())


@app.post("/api/run-pipeline")
async def run_pipeline_now(_=Depends(_check_admin)):
    async def _run():
        try:
            from src.pipeline import run as pipeline_run
            n = await asyncio.to_thread(pipeline_run)
            print(f"[pipeline-manual] {n} sessioni elaborate", flush=True)
            if n:
                await _run_rebuild_async()
        except Exception as e:
            print(f"[pipeline-manual] errore: {e}", flush=True)
    _spawn(_run())
    return JSONResponse({"ok": True, "msg": "Pipeline avviata — controlla i log Railway"})
    return JSONResponse({"status": "rebuild started"})


@app.get("/api/status")
async def status():
    try:
        doc_count = await asyncio.to_thread(count)
    except Exception:
        doc_count = 0
    return {"doc_count": doc_count, "status": "ok"}


_PREPARA_CALL_TRIGGERS = ("prepara call", "prepara la call", "preparo una call", "preparo call")

def _is_prepara_call(q: str) -> bool:
    return any(q.lower().startswith(t) or t in q.lower() for t in _PREPARA_CALL_TRIGGERS)

def _build_search_query(question: str, conv_history: list) -> str:
    """Arricchisce la query con contesto recente per una ricerca più mirata."""
    recent_user = [m["content"] for m in conv_history if m.get("role") == "user"][-2:]
    combined = " ".join(recent_user + [question])
    return combined[:600]

@app.post("/api/query")
async def query(request: Request, question: str = Form(...), history: str = Form("[]"), _=Depends(_check_auth)):
    if not question.strip():
        return JSONResponse({"error": "Domanda vuota"}, status_code=400)

    try:
        conv_history = json.loads(history)
    except Exception:
        conv_history = []

    # Per "prepara call" cerca materiale su call, obiezioni, rinnovi
    if _is_prepara_call(question):
        search_query = "struttura call obiezioni apertura chiusura rinnovo gestione cliente"
    else:
        search_query = _build_search_query(question, conv_history)

    results = search(search_query, n_results=5)

    if not results:
        context = "Il database non contiene ancora materiale indicizzato."
    else:
        context = "\n\n---\n\n".join(
            f"[{r['metadata'].get('titolo', 'Senza titolo')}]\n{r['text']}"
            for r in results
        )

    # Costruisce history per Claude (massimo ultimi 10 scambi per non sprecare token)
    messages = [
        {"role": m["role"], "content": m["content"]}
        for m in conv_history[-20:]
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

    return JSONResponse({
        "answer": message.content[0].text,
    })


_GROQ_LIMIT = 24 * 1024 * 1024  # 24 MB safety margin (Groq limit is 25 MB)
_CHUNK_SECONDS = 1200  # 20-minute chunks when splitting


def _compress_audio(content: bytes, filename: str) -> tuple[bytes, str]:
    import tempfile, subprocess as sp
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "mp3"
    with tempfile.NamedTemporaryFile(suffix=f".{ext}", delete=False) as f:
        f.write(content)
        input_path = f.name
    output_path = input_path + "_out.mp3"
    try:
        # 32 kbps mono 16 kHz — handles up to ~1h45m within the 24 MB limit
        result = sp.run(
            ["ffmpeg", "-y", "-i", input_path, "-ac", "1", "-ar", "16000", "-b:a", "32k", output_path],
            capture_output=True, timeout=300,
        )
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg: {result.stderr.decode()[-300:]}")
        with open(output_path, "rb") as out:
            return out.read(), "compressed.mp3"
    finally:
        os.unlink(input_path)
        if os.path.exists(output_path):
            os.unlink(output_path)


def _chunk_audio(content: bytes) -> list[tuple[bytes, str]]:
    """Split compressed audio into ~20-minute chunks for very long calls."""
    import tempfile, subprocess as sp, glob as glob_mod
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
        f.write(content)
        input_path = f.name
    tmpdir = tempfile.mkdtemp()
    pattern = os.path.join(tmpdir, "chunk_%03d.mp3")
    try:
        result = sp.run(
            ["ffmpeg", "-y", "-i", input_path, "-f", "segment",
             "-segment_time", str(_CHUNK_SECONDS), "-ac", "1", "-ar", "16000", "-b:a", "32k", pattern],
            capture_output=True, timeout=600,
        )
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg chunk: {result.stderr.decode()[-300:]}")
        chunks = sorted(glob_mod.glob(os.path.join(tmpdir, "chunk_*.mp3")))
        return [(open(p, "rb").read(), os.path.basename(p)) for p in chunks]
    finally:
        os.unlink(input_path)
        for p in glob_mod.glob(os.path.join(tmpdir, "chunk_*.mp3")):
            os.unlink(p)
        try:
            os.rmdir(tmpdir)
        except OSError:
            pass


def _transcribe_single(audio_name: str, content: bytes) -> str:
    t = _groq.audio.transcriptions.create(
        file=(audio_name, content),
        model="whisper-large-v3-turbo",
        language="it",
        response_format="text",
    )
    return t if isinstance(t, str) else t.text


async def _transcribe(content: bytes, filename: str) -> str:
    """Transcribes audio with Groq Whisper. Compresses first; chunks if still over limit."""
    if len(content) > _GROQ_LIMIT:
        content, filename = await asyncio.to_thread(_compress_audio, content, filename)
    if len(content) <= _GROQ_LIMIT:
        return await asyncio.to_thread(_transcribe_single, filename, content)
    # Still too large (call longer than ~1h45m): split into 20-minute chunks
    chunks = await asyncio.to_thread(_chunk_audio, content)
    parts = []
    for chunk_content, chunk_name in chunks:
        part = await asyncio.to_thread(_transcribe_single, chunk_name, chunk_content)
        parts.append(part)
    return "\n".join(parts)


def _cleanup_jobs():
    now = time.time()
    stale = [jid for jid, j in list(_jobs.items()) if now - j.get("created_at", 0) > _JOB_TTL]
    for jid in stale:
        _jobs.pop(jid, None)


async def _run_audio_analysis(job_id: str, content: bytes, filename: str, mode: str = "entrambe"):
    try:
        transcript = await _transcribe(content, filename)

        if mode == "trascrizione":
            _jobs[job_id].update({
                "status": "done",
                "result": {
                    "filename": filename,
                    "transcript": transcript,
                    "answer": None,
                    "mode": mode,
                },
            })
            return

        results = await asyncio.to_thread(lambda: search(transcript[:1000], n_results=6))
        context = "\n\n---\n\n".join(
            f"[{r['metadata'].get('titolo', 'Senza titolo')}]\n{r['text']}"
            for r in results
        ) if results else "Nessun materiale rilevante trovato nella knowledge base."

        message = await asyncio.to_thread(
            lambda: _claude.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=2048,
                system=ANALYSIS_PROMPT,
                messages=[{
                    "role": "user",
                    "content": f"MATERIALE DALLA KNOWLEDGE BASE:\n\n{context}\n\n---\n\nTRASCRIZIONE DELLA CALL:\n\n{transcript}",
                }],
            )
        )

        _jobs[job_id].update({
            "status": "done",
            "result": {
                "filename": filename,
                "transcript": transcript,
                "answer": message.content[0].text,
                "mode": mode,
            },
        })
    except ValueError as e:
        _jobs[job_id].update({"status": "error", "error": str(e)})
    except Exception as e:
        _jobs[job_id].update({"status": "error", "error": f"Errore analisi: {e}"})


@app.post("/api/analyze-audio")
async def analyze_audio(
    request: Request,
    file: UploadFile = File(...),
    mode: str = Form("entrambe"),
    _=Depends(_check_auth),
):
    if not _groq:
        return JSONResponse({"error": "GROQ_API_KEY non configurata."}, status_code=500)
    if mode not in ("trascrizione", "analisi", "entrambe"):
        mode = "entrambe"
    _cleanup_jobs()
    content = await file.read()
    filename = file.filename
    job_id = str(uuid.uuid4())
    _jobs[job_id] = {"status": "pending", "created_at": time.time()}
    _spawn(_run_audio_analysis(job_id, content, filename, mode))
    return JSONResponse({"job_id": job_id})


@app.get("/api/analyze-status/{job_id}")
async def analyze_status(job_id: str, _=Depends(_check_auth)):
    job = _jobs.get(job_id)
    if not job:
        return JSONResponse({"error": "Job non trovato o scaduto"}, status_code=404)
    if job["status"] == "pending":
        return JSONResponse({"status": "pending"})
    if job["status"] == "error":
        _jobs.pop(job_id, None)
        return JSONResponse({"status": "error", "error": job["error"]})
    result = job["result"]
    _jobs.pop(job_id, None)
    return JSONResponse({"status": "done", **result})


@app.post("/api/sync-hatting")
async def sync_hatting(request: Request, _=Depends(_check_admin)):
    job_id = str(uuid.uuid4())
    _jobs[job_id] = {"status": "pending", "progress": "Avvio sync...", "done": 0, "total": len(_HATTING_DOCS), "errors": []}
    _spawn(_run_hatting_sync(job_id))
    return JSONResponse({"job_id": job_id})


@app.get("/api/sync-hatting/{job_id}")
async def sync_hatting_status(job_id: str, request: Request, _=Depends(_check_admin)):
    job = _jobs.get(job_id)
    if not job:
        return JSONResponse({"status": "not_found"}, status_code=404)
    return JSONResponse(job)


async def _run_hatting_sync(job_id: str):
    import io
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        _jobs[job_id] = {"status": "error", "error": "beautifulsoup4 non installato sul server."}
        return

    from src.vectorstore.client import index_page
    import requests as _req

    job = _jobs[job_id]
    total = len(_HATTING_DOCS)
    errors = []

    for i, (path, title, category) in enumerate(_HATTING_DOCS):
        job["progress"] = f"[{i+1}/{total}] {title}..."
        url = _HATTING_BASE + path
        try:
            resp = await asyncio.to_thread(_req.get, url, timeout=15)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.content, "html.parser")
            for tag in soup(["script", "style", "nav", "header", "footer"]):
                tag.decompose()
            text = soup.get_text(separator="\n", strip=True)
            text = "\n".join(line for line in text.splitlines() if line.strip())
            if len(text) < 50:
                errors.append(f"{title}: contenuto troppo breve")
                continue
            page_id = "hatting_" + path.replace("/", "_").replace(".", "_")
            await asyncio.to_thread(
                index_page,
                page_id,
                text,
                {"titolo": title, "categoria": category, "source": "hatting", "url": url},
            )
            job["done"] = i + 1
        except Exception as e:
            errors.append(f"{title}: {e}")

    job["status"] = "done"
    job["errors"] = errors
    job["progress"] = f"Completato: {job['done']}/{total} documenti indicizzati."


@app.post("/api/ingest-text")
async def ingest_text(
    request: Request,
    file: UploadFile = File(...),
    title: str = Form(""),
    client_name: str = Form(""),
    _=Depends(_check_admin),
):
    content = await file.read()
    fname = file.filename or ""

    if fname.lower().endswith(".pdf"):
        try:
            import io, pdfplumber
            with pdfplumber.open(io.BytesIO(content)) as pdf:
                text = "\n".join(p.extract_text() or "" for p in pdf.pages)
        except ImportError:
            return JSONResponse({"error": "pdfplumber non installato sul server."}, status_code=500)
        except Exception as e:
            return JSONResponse({"error": f"Impossibile leggere il PDF: {e}"}, status_code=400)
    elif fname.lower().endswith(".html") or fname.lower().endswith(".htm"):
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(content, "html.parser")
            for tag in soup(["script", "style", "nav", "header", "footer"]):
                tag.decompose()
            text = soup.get_text(separator="\n", strip=True)
        except ImportError:
            text = content.decode("utf-8", errors="replace")
    else:
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            try:
                text = content.decode("latin-1")
            except Exception:
                return JSONResponse(
                    {"error": "Impossibile leggere il file. Usa un file .txt in UTF-8."},
                    status_code=400,
                )

    if not text.strip():
        return JSONResponse({"error": "Il file è vuoto."}, status_code=400)

    try:
        data = await asyncio.to_thread(extract_call_data, text)
    except Exception as e:
        return JSONResponse({"error": f"Errore nell'analisi del documento: {e}"}, status_code=500)

    doc_title = title.strip() or data.get("titolo") or file.filename
    data["titolo"] = doc_title

    try:
        notion_url = await asyncio.to_thread(save_call, file.filename, text, data)
    except Exception as e:
        return JSONResponse({"error": f"Errore salvataggio Notion: {e}"}, status_code=500)

    _spawn(_run_rebuild_async())

    result: dict = {"url": notion_url, "titolo": doc_title, "programma_operativo": None}

    if client_name.strip():
        try:
            po = await asyncio.to_thread(
                programmi.create, text, client_name.strip(), "admin", _claude
            )
            result["programma_operativo"] = po
        except Exception as e:
            result["programma_operativo"] = {"error": str(e)}

    return JSONResponse(result)


@app.post("/api/save-coaching")
async def save_coaching(
    request: Request,
    file: UploadFile = File(...),
    title: str = Form(""),
    analysis: str = Form(...),
    _=Depends(_check_auth),
):
    if not _groq:
        return JSONResponse({"error": "GROQ_API_KEY non configurata."}, status_code=500)
    if not analysis.strip():
        return JSONResponse({"error": "L'analisi non può essere vuota."}, status_code=400)

    content = await file.read()
    try:
        transcript = await _transcribe(content, file.filename)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except Exception as e:
        return JSONResponse({"error": f"Errore trascrizione: {e}"}, status_code=500)

    try:
        data = await asyncio.to_thread(extract_from_coaching, transcript, analysis)
    except Exception as e:
        return JSONResponse({"error": f"Errore strutturazione analisi: {e}"}, status_code=500)

    call_title = title.strip() or data.get("titolo") or file.filename
    data["titolo"] = call_title

    try:
        notion_url = await asyncio.to_thread(save_call, file.filename, transcript, data)
    except Exception as e:
        return JSONResponse({"error": f"Errore salvataggio Notion: {e}"}, status_code=500)

    _spawn(_run_rebuild_async())
    return JSONResponse({"url": notion_url, "titolo": call_title})


# ── Sessions Analysis ────────────────────────────────────────────────────────

_CAT_ORDER = [
    "formazione_vendita", "formazione_chat", "reclutamento", "revisione_call",
    "strategia_cliente", "onboarding_cliente", "programma_operativo",
    "interno_tsc", "altro", "nessuna_trascrizione", "errore",
]

_SESSIONS_ANALYSIS_PROMPT = """Analizza questa sessione TSC e rispondi SOLO con JSON valido, nessun altro testo.

Titolo Fathom: "{title}"
Trascrizione (primi 5000 caratteri):
{transcript}

Genera:
- "titolo": titolo descrittivo in italiano (max 65 caratteri), dice esattamente cosa è stato trattato
- "categoria": una di ["formazione_vendita","formazione_chat","reclutamento","programma_operativo","revisione_call","strategia_cliente","onboarding_cliente","interno_tsc","altro"]
- "riassunto": 2 frasi max che spiegano il contenuto principale della sessione
- "cliente": nome del cliente se identificabile, altrimenti null

JSON valido:"""


async def _run_sessions_analysis(job_id: str):
    import requests as _req

    def _list_all():
        recs, cursor = [], None
        while True:
            params = {"limit": 50}
            if cursor:
                params["cursor"] = cursor
            r = _req.get(
                f"https://api.fathom.ai/external/v1/meetings",
                headers={"X-Api-Key": FATHOM_API_KEY}, params=params,
            )
            r.raise_for_status()
            body = r.json()
            recs.extend(body.get("items", []))
            cursor = body.get("next_cursor")
            if not cursor:
                break
        return recs

    def _get_transcript(recording_id):
        r = _req.get(
            f"https://api.fathom.ai/external/v1/recordings/{recording_id}/transcript",
            headers={"X-Api-Key": FATHOM_API_KEY},
        )
        if r.status_code != 200:
            return ""
        lines = []
        for seg in r.json().get("transcript", []):
            speaker = seg.get("speaker", {}).get("display_name", "Unknown")
            text = seg.get("text", "").strip()
            if text:
                lines.append(f"{speaker}: {text}")
        return "\n".join(lines)

    def _analyze(title, transcript):
        prompt = _SESSIONS_ANALYSIS_PROMPT.format(title=title, transcript=transcript[:5000])
        resp = _claude.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text.strip()
        raw = re.sub(r"^```[a-z]*\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw)
        return json.loads(raw)

    try:
        _jobs[job_id]["progress"] = "Recupero lista sessioni da Fathom..."
        recordings = await asyncio.to_thread(_list_all)
        total = len(recordings)
        results = []

        for i, rec in enumerate(recordings):
            rec_id       = str(rec["recording_id"])
            fathom_title = rec.get("title") or f"recording_{rec_id}"
            date_str     = (rec.get("started_at") or "")[:10]
            _jobs[job_id]["progress"] = f"[{i+1}/{total}] {fathom_title[:55]}"

            try:
                transcript = await asyncio.to_thread(_get_transcript, rec_id)
                if not transcript.strip():
                    results.append({"id": rec_id, "data": date_str, "fathom_title": fathom_title,
                                    "titolo": fathom_title, "categoria": "nessuna_trascrizione",
                                    "riassunto": "Trascrizione non disponibile.", "cliente": None})
                    continue
                analysis = await asyncio.to_thread(_analyze, fathom_title, transcript)
                analysis.update({"id": rec_id, "data": date_str, "fathom_title": fathom_title})
                results.append(analysis)
            except Exception as e:
                results.append({"id": rec_id, "data": date_str, "fathom_title": fathom_title,
                                "titolo": fathom_title, "categoria": "errore",
                                "riassunto": str(e), "cliente": None})
            await asyncio.sleep(0.2)

        out = programmi._DATA_DIR / "sessions_analysis.json"
        out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        _jobs[job_id].update({"status": "done", "results": results, "total": total})

    except Exception as e:
        _jobs[job_id].update({"status": "error", "error": str(e)})


@app.post("/api/sessions-analysis")
async def sessions_analysis_start(_: str = Depends(_check_admin)):
    if not FATHOM_API_KEY:
        raise HTTPException(500, "FATHOM_API_KEY non configurata")
    _cleanup_jobs()
    job_id = str(uuid.uuid4())
    _jobs[job_id] = {"status": "pending", "created_at": time.time(), "progress": "Avvio..."}
    _spawn(_run_sessions_analysis(job_id))
    return JSONResponse({"job_id": job_id})


@app.get("/api/sessions-analysis/result")
async def sessions_analysis_saved(_: str = Depends(_check_admin)):
    path = programmi._DATA_DIR / "sessions_analysis.json"
    if path.exists():
        return JSONResponse({"status": "done", "results": json.loads(path.read_text(encoding="utf-8"))})
    return JSONResponse({"status": "not_found"})


@app.get("/api/sessions-analysis/{job_id}")
async def sessions_analysis_status(job_id: str, _: str = Depends(_check_admin)):
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job non trovato o scaduto")
    return JSONResponse(job)


# ── Programmi Operativi ───────────────────────────────────────────────────────

def _check_po_key(request: Request):
    key = request.headers.get("X-API-Key", "")
    if not HATTING_API_KEY or key != HATTING_API_KEY:
        raise HTTPException(status_code=401, detail="API key non valida")


@app.post("/api/programma-operativo")
async def create_po(request: Request, _=Depends(_check_po_key)):
    body        = await request.json()
    transcript  = body.get("transcript", "").strip()
    client_name = body.get("client_name", "").strip()
    created_by  = body.get("created_by", "hatting")
    if not transcript or not client_name:
        raise HTTPException(400, "transcript e client_name obbligatori")
    result = await asyncio.to_thread(programmi.create, transcript, client_name, created_by, _claude)
    return JSONResponse(result)


@app.get("/api/programmi-operativi/{client_name}")
async def list_po(client_name: str):
    idx = programmi.load_index()
    return JSONResponse({"programmi": idx.get(client_name, [])})


@app.get("/api/po-pdf/{filename}")
async def get_po_pdf(filename: str):
    if ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(400, "Filename non valido")
    path = programmi.PO_DIR / filename
    if not path.exists():
        raise HTTPException(404, "File non trovato")
    return FileResponse(str(path), media_type="application/pdf",
                        headers={"Content-Disposition": f'inline; filename="{filename}"'})


# ── Wonder Empire — Call Analysis ─────────────────────────────────────────────

def _check_we_key(request: Request):
    key = request.headers.get("X-API-Key", "")
    if not WE_API_KEY or key != WE_API_KEY:
        raise HTTPException(status_code=401, detail="WE API key non valida")


async def _run_we_sync(job_id: str, since_date: str | None, limit: int | None = None):
    from app.wonder_empire import (
        fetch_recordings, fetch_transcript, classify_call,
        analyze_call, get_existing_ids, save_to_supabase,
    )

    if not WE_FATHOM_API_KEY:
        _jobs[job_id].update({"status": "error", "error": "WE_FATHOM_API_KEY non configurata"})
        return
    if not WE_SUPABASE_URL or not WE_SUPABASE_KEY:
        _jobs[job_id].update({"status": "error", "error": "Credenziali Supabase WE non configurate"})
        return

    try:
        _jobs[job_id]["progress"] = "Recupero lista call da Fathom..."
        recordings = await asyncio.to_thread(fetch_recordings, WE_FATHOM_API_KEY, since_date)
        total = len(recordings)
        _jobs[job_id]["total"] = total

        if limit:
            recordings = recordings[:limit]
            total = len(recordings)
            _jobs[job_id]["total"] = total

        existing_ids = await asyncio.to_thread(get_existing_ids, WE_SUPABASE_URL, WE_SUPABASE_KEY)

        processed, skipped, errors = [], 0, []

        for i, rec in enumerate(recordings):
            rec_id = str(rec["recording_id"])
            title  = rec.get("title") or f"Call {rec_id}"
            date   = (rec.get("recording_start_time") or rec.get("scheduled_start_time") or rec.get("created_at") or "")[:10]

            _jobs[job_id]["progress"] = f"[{i+1}/{total}] {title[:55]}"

            if rec_id in existing_ids:
                skipped += 1
                continue

            try:
                transcript, speakers = await asyncio.to_thread(
                    fetch_transcript, WE_FATHOM_API_KEY, rec_id
                )
                if not transcript.strip():
                    skipped += 1
                    continue

                meta = await asyncio.to_thread(
                    classify_call, title, speakers, transcript, _claude
                )
                # Override coach with recorded_by (most reliable source)
                from app.wonder_empire import detect_coach_from_recorder
                recorder_name = rec.get("recorded_by", {}).get("name", "")
                coach_override = detect_coach_from_recorder(recorder_name)
                if coach_override:
                    meta["coach"] = coach_override
                analysis_text = await asyncio.to_thread(
                    analyze_call, meta["call_type"], transcript, search, _claude
                )

                saved = await asyncio.to_thread(
                    save_to_supabase,
                    WE_SUPABASE_URL, WE_SUPABASE_KEY,
                    rec_id, date,
                    meta.get("coach"),
                    meta.get("client_name"),
                    meta["call_type"],
                    analysis_text,
                    transcript,
                )

                saved, save_err = saved
                if saved:
                    processed.append({
                        "id": rec_id, "date": date, "title": title,
                        "call_type": meta["call_type"],
                        "coach": meta.get("coach"),
                        "client_name": meta.get("client_name"),
                        "confidence": meta.get("confidence"),
                    })
                else:
                    print(f"[we-sync] save failed for {rec_id}: {save_err}", flush=True)
                    errors.append({"id": rec_id, "title": title, "error": save_err})

            except Exception as e:
                print(f"[we-sync] exception on {rec_id} ({title!r}): {type(e).__name__}: {e}", flush=True)
                errors.append({"id": rec_id, "title": title, "error": str(e)})

            await asyncio.sleep(0.3)

        _jobs[job_id].update({
            "status": "done",
            "processed": processed,
            "skipped": skipped,
            "errors": errors,
        })

    except Exception as e:
        _jobs[job_id].update({"status": "error", "error": str(e)})


@app.get("/api/we/recordings-sample")
async def we_recordings_sample(request: Request, limit: int = 3, _=Depends(_check_we_key)):
    """Returns raw fields of the first N WE recordings from Fathom, for debugging."""
    from app.wonder_empire import fetch_recordings
    if not WE_FATHOM_API_KEY:
        return JSONResponse({"error": "WE_FATHOM_API_KEY non configurata"})
    recs = await asyncio.to_thread(fetch_recordings, WE_FATHOM_API_KEY, None)
    return JSONResponse(recs[:limit])


@app.get("/api/we/test")
async def we_test(request: Request, _=Depends(_check_we_key)):
    """Diagnostic: tests Supabase connectivity and write permission."""
    from app.wonder_empire import test_supabase
    if not WE_SUPABASE_URL or not WE_SUPABASE_KEY:
        return JSONResponse({"error": "Credenziali Supabase non configurate"})
    result = await asyncio.to_thread(test_supabase, WE_SUPABASE_URL, WE_SUPABASE_KEY)
    return JSONResponse(result)


@app.post("/api/we/sync")
async def we_sync(request: Request, _=Depends(_check_we_key)):
    """
    Start async sync of WE Fathom calls.
    Optional body: {"since": "YYYY-MM-DD"} to limit sync window.
    Returns {job_id}.
    """
    _cleanup_jobs()
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass

    since = body.get("since")
    limit = body.get("limit")
    job_id = str(uuid.uuid4())
    _jobs[job_id] = {"status": "pending", "created_at": time.time(), "progress": "Avvio...", "total": 0}
    _spawn(_run_we_sync(job_id, since, limit))
    return JSONResponse({"job_id": job_id})


@app.get("/api/we/sync/{job_id}")
async def we_sync_status(job_id: str, _=Depends(_check_we_key)):
    """Poll sync job status."""
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job non trovato o scaduto")
    return JSONResponse(job)
