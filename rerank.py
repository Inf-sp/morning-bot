import requests
import config

API_URL = "https://api.zeroentropy.dev/v1/models/rerank"

def rerank(query, documents, top_n=5, model="zerank-2"):
    """Возвращает [(текст_документа, score), ...] по убыванию релевантности."""
    if not config.ZEROENTROPY_API_KEY:
        raise Exception("Нет ключа ZeroEntropy (переменная ZEROENTROPY_API_KEY)")
    docs = [d for d in documents if d and d.strip()]
    if not docs:
        return []
    # лимит 5 000 000 байт: sum(150 + len(query) + len(doc)). Личных данных мало,
    # но на всякий случай режем длинные и ограничиваем количество.
    docs = [d[:600] for d in docs][:800]
    r = requests.post(API_URL,
        headers={"Authorization": f"Bearer {config.ZEROENTROPY_API_KEY}", "Content-Type": "application/json"},
        json={"model": model, "query": query[:2000], "documents": docs,
              "top_n": min(top_n, len(docs)), "latency": "fast"},
        timeout=40)
    r.raise_for_status()
    results = r.json().get("results", [])
    out = []
    for it in results:
        i = it.get("index")
        if isinstance(i, int) and 0 <= i < len(docs):
            out.append((docs[i], it.get("relevance_score")))
    return out