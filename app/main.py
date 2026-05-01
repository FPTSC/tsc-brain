import asyncio
import json
import os
import sys
import threading
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi import FastAPI, Request, Form, File, UploadFile, Depends, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.security import HTTPBasic, HTTPBasicCredentials
import secrets
import bcrypt
import anthropic
from groq import Groq

from config.settings import ANTHROPIC_API_KEY, CLAUDE_MODEL, GROQ_API_KEY
from src.vectorstore.client import search, count
from src.processor.extractor import extract_from_coaching
from src.notion.client import save_call

APP_PASSWORD = os.environ.get("APP_PASSWORD", "tsc2024")
APP_USER = os.environ.get("APP_USER", "tsc")

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

app = FastAPI(title="TSC Brain")
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")

security = HTTPBasic()
_claude = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
_groq = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None
_HTML_PATH = Path(__file__).parent / "templates" / "index.html"
_bg_tasks: set = set()
_jobs: dict = {}
_JOB_TTL = 600

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


_GROQ_LIMIT = 24 * 1024 * 1024  # 24MB safety margin


def _compress_audio(content: bytes, filename: str) -> tuple[bytes, str]:
    import tempfile, subprocess, os
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "mp3"
    with tempfile.NamedTemporaryFile(suffix=f".{ext}", delete=False) as f:
        f.write(content)
        input_path = f.name
    output_path = input_path + "_out.mp3"
    try:
        result = subprocess.run(
            ["ffmpeg", "-y", "-i", input_path, "-ac", "1", "-ar", "16000", "-b:a", "64k", output_path],
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


async def _transcribe(content: bytes, filename: str) -> str:
    """Transcribes audio with Groq Whisper, compressing first if needed."""
    audio_name = filename
    if len(content) > _GROQ_LIMIT:
        content, audio_name = await asyncio.to_thread(_compress_audio, content, filename)
    if len(content) > _GROQ_LIMIT:
        raise ValueError("File troppo grande anche dopo la compressione.")
    transcription = await asyncio.to_thread(
        lambda: _groq.audio.transcriptions.create(
            file=(audio_name, content),
            model="whisper-large-v3-turbo",
            language="it",
            response_format="text",
        )
    )
    return transcription if isinstance(transcription, str) else transcription.text


def _cleanup_jobs():
    now = time.time()
    stale = [jid for jid, j in list(_jobs.items()) if now - j.get("created_at", 0) > _JOB_TTL]
    for jid in stale:
        _jobs.pop(jid, None)


async def _run_audio_analysis(job_id: str, content: bytes, filename: str):
    try:
        transcript = await _transcribe(content, filename)
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

        sources = [
            {"titolo": r["metadata"].get("titolo", ""), "url": r["metadata"].get("url", "")}
            for r in results
            if r["metadata"].get("titolo")
        ]
        _jobs[job_id].update({
            "status": "done",
            "result": {
                "filename": filename,
                "transcript": transcript,
                "answer": message.content[0].text,
                "sources": sources,
            },
        })
    except ValueError as e:
        _jobs[job_id].update({"status": "error", "error": str(e)})
    except Exception as e:
        _jobs[job_id].update({"status": "error", "error": f"Errore analisi: {e}"})


@app.post("/api/analyze-audio")
async def analyze_audio(request: Request, file: UploadFile = File(...), _=Depends(_check_auth)):
    if not _groq:
        return JSONResponse({"error": "GROQ_API_KEY non configurata."}, status_code=500)
    _cleanup_jobs()
    content = await file.read()
    filename = file.filename
    job_id = str(uuid.uuid4())
    _jobs[job_id] = {"status": "pending", "created_at": time.time()}
    _spawn(_run_audio_analysis(job_id, content, filename))
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


@app.post("/api/ingest-text")
async def ingest_text(
    request: Request,
    file: UploadFile = File(...),
    title: str = Form(""),
    _=Depends(_check_auth),
):
    content = await file.read()
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

    return JSONResponse({"url": notion_url, "titolo": doc_title})


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

    return JSONResponse({"url": notion_url, "titolo": call_title})
