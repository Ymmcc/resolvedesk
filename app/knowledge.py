import asyncio
from collections import Counter, OrderedDict
import json
import re
import threading

import numpy as np

from .db import encode, new_id, now
from .providers import ProviderError, tokens


def chunk_text(content, limit=650, overlap=80):
    """Keep heading/paragraph boundaries; split long paragraphs with bounded overlap."""
    chunks, current = [], ""
    for paragraph in re.split(r"\n\s*\n|(?=^#{1,3} )", content, flags=re.M):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        if current and len(current) + len(paragraph) + 2 > limit:
            chunks.append(current)
            current = ""
        while len(paragraph) > limit:
            chunks.append(paragraph[:limit])
            paragraph = paragraph[limit - overlap:]
        current = (current + "\n\n" + paragraph).strip()
    if current:
        chunks.append(current)
    return chunks


class Knowledge:
    def __init__(self, db, provider):
        self.db, self.provider = db, provider
        self._lock = threading.RLock()
        self._revision = -1
        self._rows = []
        self._terms = []
        self._matrix = np.zeros((0, 384), dtype=np.float32)
        self._query_cache = OrderedDict()

    async def prepare(self, doc_id, title, content):
        parts = chunk_text(content)
        vectors = await self.provider.embed([title + "\n" + p for p in parts])
        return [(f"{doc_id}:{n}", doc_id, n, part, encode(vector.tolist()), self.provider.fingerprint)
                for n, (part, vector) in enumerate(zip(parts, vectors))]

    async def initialize(self):
        # A model/config change rebuilds all published chunks before serving traffic.
        docs = self.db.all("""SELECT d.* FROM documents d WHERE NOT EXISTS
            (SELECT 1 FROM chunks c WHERE c.document_id=d.id) OR EXISTS
            (SELECT 1 FROM chunks c WHERE c.document_id=d.id AND c.fingerprint<>?)""",
                           (self.provider.fingerprint,))
        prepared = [(d, await self.prepare(d["id"], d["title"], d["content"])) for d in docs]
        if prepared:
            with self.db.transaction() as con:
                for doc, chunks in prepared:
                    con.execute("DELETE FROM chunks WHERE document_id=?", (doc["id"],))
                    con.executemany("INSERT INTO chunks VALUES (?,?,?,?,?,?)", chunks)
                self.db.bump_index(con)

    def _snapshot(self):
        revision = self.db.one("SELECT value FROM metadata WHERE key='index_revision'")["value"]
        with self._lock:
            if revision != self._revision:
                revision, rows = self.db.index_snapshot()
                try:
                    matrix = np.array([json.loads(r["vector"]) for r in rows], dtype=np.float32)
                    if rows and (matrix.ndim != 2 or not np.isfinite(matrix).all()):
                        raise ValueError("invalid persisted embeddings")
                except ValueError as exc:
                    raise ProviderError("知识索引不一致，需要重建") from exc
                self._rows, self._matrix = rows, matrix
                self._terms = [Counter(tokens(r["title"] + "\n" + r["content"])) for r in rows]
                self._revision = revision
            return self._rows, self._terms, self._matrix

    async def search(self, query, version, module="general", strategy="hybrid", top_k=5, customer_id="acme"):
        vector = None
        if strategy != "bm25":
            if query in self._query_cache:
                vector = self._query_cache[query]
                self._query_cache.move_to_end(query)
            else:
                vector = (await self.provider.embed([query]))[0]
                self._query_cache[query] = vector
                if len(self._query_cache) > 128:
                    self._query_cache.popitem(last=False)
        return await asyncio.to_thread(self._rank, query, version, module, strategy, top_k, customer_id, vector)

    def _rank(self, query, version, module, strategy, top_k, customer_id, vector):
        rows, terms, matrix = self._snapshot()
        selected = [i for i, r in enumerate(rows) if r["version"] in (version, "all")
                    and (module == "general" or r["module"] in (module, "general"))
                    and (r["customer_id"] is None or r["customer_id"] == customer_id)]
        if not selected:
            return []
        bm25 = np.zeros(len(selected))
        counts = [terms[i] for i in selected]
        lengths = np.array([sum(c.values()) for c in counts])
        avg_len = max(float(lengths.mean()), 1)
        for word in set(tokens(query)):
            frequencies = np.array([c[word] for c in counts])
            df = np.count_nonzero(frequencies)
            idf = np.log(1 + (len(counts) - df + 0.5) / (df + 0.5))
            bm25 += idf * frequencies * 2.5 / (frequencies + 1.5 * (0.25 + 0.75 * lengths / avg_len))
        cosine = np.zeros(len(selected))
        if vector is not None:
            if matrix.shape[1] != len(vector):
                raise ProviderError("查询向量与索引维度不同，请检查 Embedding 配置并重启重建")
            cosine = matrix[selected] @ vector
        lexical = sorted((i for i in range(len(selected)) if bm25[i] > 0), key=lambda i: (-bm25[i], selected[i]))
        threshold = 0.08 if self.provider.settings.mode == "demo" else 0.25
        dense = sorted((i for i in range(len(selected)) if cosine[i] > threshold), key=lambda i: (-cosine[i], selected[i]))
        scores = {}
        lists = [lexical] if strategy == "bm25" else [dense] if strategy == "vector" else [lexical, dense]
        for ranked in lists:
            for rank, i in enumerate(ranked[:30], 1):
                scores[i] = scores.get(i, 0) + 1 / (60 + rank)
        order = sorted(scores, key=lambda i: (-scores[i], selected[i]))[:top_k]
        return [{"id": rows[selected[i]]["id"], "document_id": rows[selected[i]]["document_id"],
                 "title": rows[selected[i]]["title"], "version": rows[selected[i]]["version"],
                 "module": rows[selected[i]]["module"], "content": rows[selected[i]]["content"],
                 "scope": "customer" if rows[selected[i]]["customer_id"] else "shared",
                 "rrf_score": round(scores[i], 6), "bm25_score": round(float(bm25[i]), 4),
                 "cosine": round(float(cosine[i]), 4)} for i in order]

    async def add_document(self, payload, customer_id=None, doc_id=None):
        doc_id = doc_id or new_id("DOC")
        chunks = await self.prepare(doc_id, payload.title, payload.content)
        with self.db.transaction() as con:
            con.execute("INSERT INTO documents VALUES (?,?,?,?,?,?,?)", (doc_id, payload.title, payload.module,
                        payload.version, payload.content, customer_id, now()))
            con.executemany("INSERT INTO chunks VALUES (?,?,?,?,?,?)", chunks)
            self.db.bump_index(con)
        return {"id": doc_id, "chunks": len(chunks)}
