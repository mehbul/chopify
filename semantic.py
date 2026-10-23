"""Optional semantic transcript search through a local Ollama embedding model."""
import hashlib
import json
import math
import urllib.error
import urllib.request
from pathlib import Path

HOST = "http://127.0.0.1:11434"
DEFAULT_MODEL = "nomic-embed-text"


def ollama_embed(texts, model):
    request = urllib.request.Request(HOST + "/api/embed", data=json.dumps(
        {"model": model, "input": texts, "truncate": True}).encode(),
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            vectors = json.load(response)["embeddings"]
    except (OSError, KeyError, ValueError) as exc:
        raise ValueError(f"Local semantic search unavailable. Start Ollama and run "
                         f"'ollama pull {model}', then retry. ({exc})") from exc
    return vectors


def validate_vectors(vectors, count):
    if not isinstance(vectors, list) or len(vectors) != count or not vectors:
        raise ValueError("Embedding model returned the wrong number of vectors.")
    if any(not isinstance(v, list) for v in vectors):
        raise ValueError("Embedding model returned invalid vectors.")
    size = len(vectors[0])
    if not size or any(len(v) != size or not all(isinstance(x, (float, int)) and math.isfinite(x)
                                                for x in v) or not any(v) for v in vectors):
        raise ValueError("Embedding model returned invalid vectors.")


def search(sentences, query, model=DEFAULT_MODEL, cache_dir=None, embed=None):
    if not query or not query.strip() or not sentences:
        raise ValueError("Semantic search needs a query and a non-empty transcript.")
    embed = embed or ollama_embed
    texts = [s["text"] for s in sentences]
    key = hashlib.sha256(json.dumps([model, texts], ensure_ascii=False).encode()).hexdigest()
    cache = Path(cache_dir) / "embeddings.json" if cache_dir else None
    vectors = None
    if cache and cache.exists():
        try:
            saved = json.loads(cache.read_text(encoding="utf-8"))
            if saved["key"] == key:
                validate_vectors(saved["vectors"], len(texts))
                vectors = saved["vectors"]
        except (ValueError, KeyError, TypeError):
            pass
    if vectors is None:
        vectors = []
        for i in range(0, len(texts), 64):
            batch = embed(texts[i:i + 64], model)
            validate_vectors(batch, len(texts[i:i + 64]))
            vectors.extend(batch)
        validate_vectors(vectors, len(texts))
        if cache:
            cache.parent.mkdir(parents=True, exist_ok=True)
            temp = cache.with_suffix(".tmp")
            temp.write_text(json.dumps({"key": key, "vectors": vectors}), encoding="utf-8")
            temp.replace(cache)
    query_vector = embed([query], model)
    validate_vectors(query_vector, 1)
    q = query_vector[0]
    if len(q) != len(vectors[0]):
        raise ValueError("Query and document embeddings have different dimensions.")
    def cosine(v):
        return sum(a * b for a, b in zip(q, v)) / math.sqrt(
            sum(x * x for x in q) * sum(x * x for x in v))
    return sorted([{"index": i, "similarity": cosine(v)} for i, v in enumerate(vectors)],
                  key=lambda r: r["similarity"], reverse=True)


def candidates(sentences, query, model, cache_dir, min_len, max_len, max_clips):
    from score_clips import _grow_window, _make_segment
    taken = [False] * len(sentences)
    result = []
    for match in search(sentences, query, model, cache_dir):
        seed = match["index"]
        if taken[seed]:
            continue
        window = _grow_window(sentences, seed, taken, min_len, max_len)
        if window:
            a, b = window
            item = _make_segment(sentences, a, b)
            item["similarity"] = round(match["similarity"], 4)
            result.append(item)
            for i in range(a, b + 1):
                taken[i] = True
        if len(result) >= max_clips:
            break
    return sorted(result, key=lambda s: s["start"])
