import os
import time
from typing import Any, Dict, List, Optional
from pinecone import Pinecone

# You will embed text with OpenAI embeddings (or any). We keep a small helper stub.
from openai import OpenAI

EMBED_MODEL = os.getenv("OPENAI_EMBED_MODEL", "text-embedding-3-small")
#EMBED_MODEL = os.getenv("OPENAI_EMBED_MODEL", "").strip()

def _openai() -> OpenAI:
    return OpenAI(api_key=os.environ["OPENAI_API_KEY"])

def embed(texts: List[str]) -> List[List[float]]:
    if not EMBED_MODEL:
        raise RuntimeError("OPENAI_EMBED_MODEL no está definido. No se puede hacer embedding.")
    resp = _openai().embeddings.create(model=EMBED_MODEL, input=texts)
    return [d.embedding for d in resp.data]

def index():
    pc = Pinecone(api_key=os.environ["PINECONE_API_KEY"])
    host = os.environ["PINECONE_INDEX_HOST"]
    return pc.Index(host=host)

def namespace(repo: str, issue_number: str) -> str:
    prefix = os.getenv("PINECONE_NAMESPACE_PREFIX", "").strip()
    base = f"{repo.replace('/','__')}__issue_{issue_number}"
    return f"{prefix}{base}" if prefix else base

def upsert_texts(repo: str, issue_number: str, items: List[Dict[str, Any]]) -> None:
    """
    items: [{ "id": "...", "text": "...", "metadata": {...}}]
    """
    idx = index()
    ns = namespace(repo, issue_number)
    texts = [it["text"] for it in items]
    vecs = embed(texts)
    vectors = []
    for it, v in zip(items, vecs):
        md = dict(it.get("metadata", {}))
        md["ts"] = int(time.time())
        vectors.append((it["id"], v, md))
    idx.upsert(vectors=vectors, namespace=ns)

def query(repo: str, issue_number: str, text: str, top_k: int = 8) -> List[Dict[str, Any]]:
    idx = index()
    ns = namespace(repo, issue_number)
    qv = embed([text])[0]
    res = idx.query(vector=qv, top_k=top_k, include_metadata=True, namespace=ns)
    out = []
    for m in res.matches or []:
        out.append({
            "id": m.id,
            "score": float(m.score),
            "metadata": m.metadata or {}
        })
    return out
