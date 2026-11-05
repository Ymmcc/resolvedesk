from collections import Counter
import hashlib
import json
import re

import httpx
import numpy as np


class ProviderError(RuntimeError):
    """Sanitized upstream error; never include headers, keys or response bodies."""


def tokens(text):
    result = []
    for part in re.findall(r"[a-z0-9_]+(?:[-.][a-z0-9_]+)*|[\u4e00-\u9fff]+", text.lower()):
        if re.fullmatch(r"[\u4e00-\u9fff]+", part):
            result.extend(part[i:i + 2] for i in range(max(1, len(part) - 1)))
        else:
            result.append(part)
    return result


def hash_vectors(texts, dimensions=384):
    """Lexical feature hashing, NOT a semantic embedding model."""
    result = np.zeros((len(texts), dimensions), dtype=np.float32)
    for row, content in enumerate(texts):
        for token, count in Counter(tokens(content)).items():
            digest = hashlib.blake2b(token.encode(), digest_size=8).digest()
            result[row, int.from_bytes(digest, "little") % dimensions] += 1 + np.log(count)
    norms = np.linalg.norm(result, axis=1, keepdims=True)
    return result / np.maximum(norms, 1e-12)


class Providers:
    def __init__(self, settings, client=None):
        self.settings = settings
        if settings.mode == "live" and not all([
            settings.llm_api_key, settings.llm_model, settings.embed_api_key, settings.embed_model
        ]):
            raise ValueError("live 模式需要配置聊天与 Embedding 的 API key 和 model，不会自动降级")
        self.client = client or httpx.AsyncClient(timeout=settings.http_timeout)
        self.fingerprint = ("hash-lexical-v1" if settings.mode == "demo" else
                            hashlib.sha256(f"{settings.embed_base_url}|{settings.embed_model}".encode()).hexdigest())

    async def close(self):
        await self.client.aclose()

    async def _post(self, base, path, key, payload):
        # No automatic retry for ambiguous requests: bounded latency and explicit failure.
        try:
            response = await self.client.post(base.rstrip("/") + path,
                                              headers={"Authorization": f"Bearer {key}"}, json=payload)
            response.raise_for_status()
            return response.json()
        except httpx.TimeoutException as exc:
            raise ProviderError("模型服务超时，请稍后重试") from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise ProviderError("模型服务请求失败，请检查配置或服务状态") from exc

    async def embed(self, texts):
        if self.settings.mode == "demo":
            return hash_vectors(texts)
        vectors = []
        for offset in range(0, len(texts), 32):
            batch = texts[offset:offset + 32]
            data = await self._post(self.settings.embed_base_url, "/embeddings", self.settings.embed_api_key,
                                    {"model": self.settings.embed_model, "input": batch, "encoding_format": "float"})
            try:
                items = sorted(data["data"], key=lambda r: r["index"])
                if [i["index"] for i in items] != list(range(len(batch))):
                    raise ValueError("invalid embedding indices")
                vectors.extend(item["embedding"] for item in items)
            except (KeyError, TypeError, ValueError) as exc:
                raise ProviderError("Embedding 返回结构不合法") from exc
        try:
            matrix = np.array(vectors, dtype=np.float32)
            norms = np.linalg.norm(matrix, axis=1, keepdims=True)
            if matrix.ndim != 2 or matrix.shape[0] != len(texts) or not np.isfinite(matrix).all() or (norms <= 0).any():
                raise ValueError("invalid embedding vectors")
            return matrix / norms
        except (ValueError, TypeError, np.exceptions.AxisError) as exc:
            raise ProviderError("Embedding 向量维度或数值不合法") from exc

    async def chat(self, messages, tools=None, structured=False):
        payload = {"model": self.settings.llm_model, "messages": messages, "temperature": 0, "max_tokens": 1600}
        if tools:
            payload.update(tools=tools, tool_choice="auto", parallel_tool_calls=True)
        if structured:
            payload["response_format"] = {"type": "json_object"}
        data = await self._post(self.settings.llm_base_url, "/chat/completions", self.settings.llm_api_key, payload)
        try:
            message = data["choices"][0]["message"]
            if not isinstance(message, dict):
                raise ValueError("message must be object")
            return message
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ProviderError("聊天模型返回结构不合法") from exc

    async def structured(self, system, context, schema):
        message = await self.chat([
            {"role": "system", "content": system + "\n只输出 JSON 对象，结构如下：" + json.dumps(schema.model_json_schema(), ensure_ascii=False)},
            {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
        ], structured=True)
        try:
            return schema.model_validate_json(message.get("content") or "")
        except ValueError as exc:
            raise ProviderError("模型结构化输出不符合约定，未执行任何写入") from exc
