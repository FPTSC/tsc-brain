import logging
from pathlib import Path

try:
    import chromadb
    _CHROMADB_OK = True
except Exception as e:
    logging.warning(f"chromadb not available: {e}")
    _CHROMADB_OK = False

try:
    import voyageai
    _VOYAGE_OK = True
except Exception as e:
    logging.warning(f"voyageai not available: {e}")
    _VOYAGE_OK = False

import os
from config.settings import VOYAGE_API_KEY

logger = logging.getLogger(__name__)

_COLLECTION_NAME = "tsc_brain"
_MODEL_NAME = "voyage-3-lite"
_DB_PATH = os.environ.get(
    "VECTORSTORE_PATH",
    str(Path(__file__).parent.parent.parent / "vectorstore"),
)

_collection = None
_voyage = None


def _get_voyage():
    global _voyage
    if _voyage is None:
        _voyage = voyageai.Client(api_key=VOYAGE_API_KEY)
    return _voyage


def _get_collection():
    global _collection
    if _collection is None:
        client = chromadb.PersistentClient(path=_DB_PATH)
        _collection = client.get_or_create_collection(
            name=_COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )
    return _collection


def _embed(texts: list[str]) -> list[list[float]]:
    result = _get_voyage().embed(texts, model=_MODEL_NAME, input_type="document")
    return result.embeddings


def index_page(page_id: str, text: str, metadata: dict) -> None:
    if not _CHROMADB_OK or not _VOYAGE_OK:
        return
    embeddings = _embed([text])
    _get_collection().upsert(
        ids=[page_id],
        embeddings=embeddings,
        documents=[text],
        metadatas=[metadata],
    )
    logger.debug(f"Indicizzato: {metadata.get('titolo', page_id)}")


def index_pages_batch(pages: list) -> None:
    """pages: list of (page_id, text, metadata) — single Voyage AI call for all."""
    if not _CHROMADB_OK or not _VOYAGE_OK or not pages:
        return
    ids = [p[0] for p in pages]
    texts = [p[1] for p in pages]
    metadatas = [p[2] for p in pages]
    embeddings = _embed(texts)
    _get_collection().upsert(ids=ids, embeddings=embeddings, documents=texts, metadatas=metadatas)
    logger.info(f"Batch indicizzato: {len(pages)} documenti")


def search(query: str, n_results: int = 5) -> list[dict]:
    if not _CHROMADB_OK or not _VOYAGE_OK:
        return []
    col = _get_collection()
    if col.count() == 0:
        return []

    result = _get_voyage().embed([query], model=_MODEL_NAME, input_type="query")
    query_embedding = result.embeddings[0]

    results = col.query(
        query_embeddings=[query_embedding],
        n_results=min(n_results, col.count()),
    )

    return [
        {
            "id": results["ids"][0][i],
            "text": results["documents"][0][i],
            "metadata": results["metadatas"][0][i],
            "distance": results["distances"][0][i],
        }
        for i in range(len(results["ids"][0]))
    ]


def count() -> int:
    if not _CHROMADB_OK:
        return 0
    try:
        return _get_collection().count()
    except Exception:
        return 0
