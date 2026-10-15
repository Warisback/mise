"""Agent memory. Two backends behind one interface:

  local  - JSONL file + keyword scoring. Zero setup, always on, also the fallback.
  cognee - Cognee 1.x remember/recall. Cloud if COGNEE_URL + COGNEE_API_KEY are set, else local Cognee
           (local Cognee needs embeddings configured; see .env.example).

Every write also goes to the local file, so recall never comes back empty because Cognee is still
building its graph or the venue wifi dropped.
"""

import asyncio
import json
import os
import re
import threading
import time

from . import config


class LocalMemory:
    name = "local"

    def __init__(self, path=config.MEMORY_PATH):
        self.path = path
        self.path.parent.mkdir(exist_ok=True)
        self.path.touch()

    def remember(self, text: str, meta: dict | None = None) -> None:
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": time.time(), "text": text, "meta": meta or {}}) + "\n")

    def _all(self) -> list[dict]:
        with open(self.path, encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]

    def recall(self, query: str, k: int = 5) -> list[str]:
        words = {w for w in re.findall(r"[a-z0-9£.-]+", query.lower()) if len(w) > 2}
        scored = []
        for i, m in enumerate(self._all()):
            hits = sum(1 for w in words if w in m["text"].lower())
            if hits:
                scored.append((hits, i, m["text"]))
        return [t for _, _, t in sorted(scored, reverse=True)[:k]]

    def reset(self) -> None:
        self.path.write_text("", encoding="utf-8")


class CogneeMemory(LocalMemory):
    name = "cognee"
    DATASET = os.getenv("COGNEE_DATASET", "finance_agent")

    def __init__(self):
        super().__init__()
        os.environ.setdefault("ENABLE_BACKEND_ACCESS_CONTROL", "false")  # single-user local mode
        self._loop = asyncio.new_event_loop()
        threading.Thread(target=self._loop.run_forever, daemon=True).start()
        self._ready = self._submit(self._connect())

    def _submit(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self._loop)

    async def _connect(self):
        import cognee  # slow import (~45s); keep it off the startup path for the local backend

        self.cognee = cognee
        url, key = os.getenv("COGNEE_URL"), os.getenv("COGNEE_API_KEY")
        if url and key:
            await cognee.serve(url=url, api_key=key)

    def remember(self, text: str, meta: dict | None = None) -> None:
        super().remember(text, meta)

        async def _go():
            await asyncio.wrap_future(self._ready)
            await self.cognee.remember(text, dataset_name=self.DATASET)

        self._submit(_go())  # fire and forget: graph building must not block a payment

    def recall(self, query: str, k: int = 5) -> list[str]:
        async def _go():
            await asyncio.wrap_future(self._ready)
            return await self.cognee.recall(query_text=query, datasets=[self.DATASET], top_k=k)

        try:
            results = self._submit(_go()).result(timeout=30)
            texts = [getattr(r, "text", None) or getattr(r, "answer", None) or str(r) for r in results]
            texts = [t for t in texts if t]
            if texts:
                return texts[:k]
        except Exception as e:  # noqa: BLE001 - fall back to local rather than fail the agent
            print(f"[memory] cognee recall failed, using local: {e!r}")
        return super().recall(query, k)

    def reset(self) -> None:
        super().reset()

        async def _go():
            await asyncio.wrap_future(self._ready)
            await self.cognee.forget(everything=True)

        try:
            self._submit(_go()).result(timeout=60)
        except Exception as e:  # noqa: BLE001
            print(f"[memory] cognee reset failed: {e!r}")


memory = CogneeMemory() if config.MEMORY_BACKEND == "cognee" else LocalMemory()
