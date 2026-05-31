"""KWhale embedding service — BAAI/bge-m3, 1024-dim, OpenAI-compatible.

Standalone (no external config deps). Serves /v1/embeddings and /embed so the
worker (lyrics embeddings at index time) and api (query embeddings for the
prompt agent's semantic_search) can call it over the internal network.
"""
import os
import time

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sentence_transformers import SentenceTransformer

MODEL_NAME = os.environ.get("EMBEDDING_MODEL", "BAAI/bge-m3")
app = FastAPI(title="KWhale Embedding Service")

_model: SentenceTransformer | None = None


def get_model() -> SentenceTransformer:
    global _model
    if _model is None:
        _model = SentenceTransformer(MODEL_NAME)
    return _model


class EmbedRequest(BaseModel):
    text: str
    normalize: bool = True


class OpenAIEmbedRequest(BaseModel):
    input: str | list[str]
    model: str = "bge-m3"


@app.get("/health")
async def health():
    return {"status": "ok", "model": MODEL_NAME}


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


@app.post("/embed")
async def embed(req: EmbedRequest):
    model = get_model()
    start = time.perf_counter()
    vec = model.encode(req.text, normalize_embeddings=req.normalize)
    return {
        "embedding": vec.tolist(),
        "dim": len(vec),
        "model": MODEL_NAME,
        "took_ms": round((time.perf_counter() - start) * 1000, 2),
    }


@app.post("/v1/embeddings")
async def openai_embeddings(req: OpenAIEmbedRequest):
    model = get_model()
    inputs = req.input if isinstance(req.input, list) else [req.input]
    if len(inputs) > 100:
        raise HTTPException(status_code=422, detail="Maximum 100 inputs per request")

    vectors = model.encode(inputs, normalize_embeddings=True)
    data = [
        {"object": "embedding", "index": i, "embedding": vec.tolist()}
        for i, vec in enumerate(vectors)
    ]
    total_tokens = sum(len(s.split()) for s in inputs)
    return {
        "object": "list",
        "data": data,
        "model": MODEL_NAME,
        "usage": {"prompt_tokens": total_tokens, "total_tokens": total_tokens},
    }
