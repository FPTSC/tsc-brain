# Guida di Setup — Brain Knowledge Base

Questa guida permette di replicare il sistema in circa 30 minuti in call assistita.

---

## STEP 1 — Account da creare (prima della call)

Il cliente deve creare account su questi servizi gratuiti:

| Servizio | Link | Serve per |
|---|---|---|
| GitHub | github.com | Ospitare il codice |
| Railway | railway.app | Hosting dell'applicazione |
| Anthropic | console.anthropic.com | Analisi AI delle call |
| Groq | console.groq.com | Trascrizione audio |
| Voyage AI | dash.voyageai.com | Ricerca nella knowledge base |
| Notion | notion.so | Database delle call (potrebbe averlo già) |

---

## STEP 2 — Ottenere le API Key

### 2a. Anthropic (ANTHROPIC_API_KEY)
1. Vai su **console.anthropic.com** → Log in
2. Menu sinistra → **API Keys**
3. **Create Key** → dai un nome (es. "Brain") → copia la chiave
4. Attiva un metodo di pagamento (serve carta, offre crediti gratuiti iniziali)

### 2b. Groq (GROQ_API_KEY)
1. Vai su **console.groq.com** → Log in
2. Menu sinistra → **API Keys**
3. **Create API Key** → copia la chiave
4. Gratuito fino a 7.000 minuti/giorno di trascrizione

### 2c. Voyage AI (VOYAGE_API_KEY)
1. Vai su **dash.voyageai.com** → Log in
2. **API Keys** → **Create new key** → copia la chiave
3. Gratuito fino a 50M token/mese

### 2d. Notion — Token e Database ID

**Creare l'integrazione:**
1. Vai su **notion.so/my-integrations**
2. **+ New integration** → nome: "Brain" → seleziona il workspace
3. Copia il **"Internal Integration Secret"** → questo è il `NOTION_TOKEN`

**Creare il database Notion:**
1. In Notion, crea una nuova pagina → inserisci un blocco **Database (full page)**
2. Dai un nome al database (es. "Knowledge Base")
3. Aggiungi queste proprietà esatte (nome e tipo devono essere identici):

| Nome proprietà | Tipo |
|---|---|
| Titolo | Title (già presente di default) |
| Categoria | Select |
| Sotto-categoria | Text |
| Tipo Sessione | Select |
| Pubblico | Select |
| Sintesi | Text |
| Venditori Coinvolti | Multi-select |
| Tags | Multi-select |
| File Sorgente | Text |
| Data Elaborazione | Date |

4. **Connetti l'integrazione al database**: apri il database → menu `...` in alto a destra → **Connections** → aggiungi "Brain"

**Trovare il Database ID:**
1. Apri il database Notion nel browser
2. Guarda l'URL: `https://notion.so/workspace/`**`xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx`**`?v=...`
3. La stringa di 32 caratteri tra lo slash e il `?` è il `NOTION_DATABASE_ID`

---

## STEP 3 — Fork del repo su GitHub

1. Vai su **github.com/FPTSC/tsc-brain**
2. Clicca **"Use this template"** → **"Create a new repository"**
3. Dai un nome al repo (es. `mia-azienda-brain`)
4. Lascia **Private** → **Create repository**

---

## STEP 4 — Deploy su Railway

1. Vai su **railway.app** → **New Project**
2. Seleziona **"Deploy from GitHub repo"** → autorizza GitHub → scegli il repo appena creato
3. Railway inizia il build (aspetta che finisca)

**Aggiungere le variabili d'ambiente:**
4. Clicca sul servizio → tab **Variables** → aggiungi queste:

| Variabile | Valore |
|---|---|
| `ANTHROPIC_API_KEY` | la chiave Anthropic |
| `GROQ_API_KEY` | la chiave Groq |
| `VOYAGE_API_KEY` | la chiave Voyage AI |
| `NOTION_TOKEN` | il token Notion |
| `NOTION_DATABASE_ID` | l'ID del database Notion |
| `APP_USER` | username admin (scegli tu, es. `admin`) |
| `APP_PASSWORD` | password admin (scegli tu, min. 8 caratteri) |
| `USERS_FILE` | `/data/users.json` |

**Creare il Volume (per salvare gli utenti in modo permanente):**
5. Nella pagina del progetto → **"+ New"** → **Volume**
6. Mount Path: `/data` → conferma
7. Railway farà un redeploy automatico

---

## STEP 5 — Primo accesso e configurazione

1. Railway → tab **Settings** → copia il dominio pubblico (es. `xxx.up.railway.app`)
2. Vai su `https://xxx.up.railway.app` → inserisci `APP_USER` e `APP_PASSWORD`
3. L'app è attiva. Il database è vuoto finché non si caricano le prime call.

**Creare account per i membri del team:**
4. Vai su `https://xxx.up.railway.app/admin`
5. Crea un utente per ogni membro del team (username + password, admin = no)

---

## STEP 6 — Primo caricamento (opzionale in call)

Per testare che tutto funzioni:
1. Dalla home, clicca l'icona microfono
2. Carica un file audio di una call (mp3, mp4, m4a, wav)
3. Aspetta l'analisi (1-3 minuti per call lunghe)
4. Verifica che l'analisi appaia correttamente

---

## Costi stimati mensili

| Servizio | Piano gratuito | Piano a pagamento |
|---|---|---|
| Railway | $5/mese (Hobby) | — |
| Anthropic | — | ~$5-20/mese secondo utilizzo |
| Groq | Gratuito (7k min/giorno) | — |
| Voyage AI | Gratuito (50M token/mese) | — |
| Notion | Gratuito | — |

**Stima totale: ~$10-25/mese** per un team di 5-10 persone con uso normale.
