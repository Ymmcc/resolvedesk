"""Retrieval regression on a clean temporary database. Not a model quality benchmark."""
import argparse
import asyncio
from dataclasses import replace
import json
from pathlib import Path
import platform
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import ROOT, Settings
from app.db import Database, now
from app.knowledge import Knowledge
from app.providers import Providers
from app.seed import seed


async def main(args):
    settings = Settings.from_env()
    provider = Providers(settings)
    cases = json.loads((ROOT / "eval" / "retrieval.json").read_text(encoding="utf-8"))
    try:
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(str(Path(tmp) / "eval.db"))
            db.initialize(); seed(db)
            kb = Knowledge(db, provider)
            await kb.initialize()
            results = {}
            for strategy in ["bm25", "vector", "hybrid"]:
                details = []
                for case in cases:
                    started = time.perf_counter()
                    hits = await kb.search(case["query"], case["version"], case["module"], strategy, args.k)
                    # Collapse chunks before computing document-level metrics.
                    ids = list(dict.fromkeys(h["document_id"] for h in hits))
                    relevant = set(case["relevant"])
                    rank = next((i for i,d in enumerate(ids,1) if d in relevant), None)
                    details.append({**case, "retrieved":ids, "hit":rank is not None,
                                    "recall":len(relevant.intersection(ids))/len(relevant),
                                    "rr":1/rank if rank else 0, "duration_ms":round((time.perf_counter()-started)*1000,2),
                                    "wrong_version_chunks":sum(h["version"] not in {case["version"],"all"} for h in hits)})
                results[strategy] = {"hit_at_k":sum(d["hit"] for d in details)/len(details),
                    "recall_at_k":sum(d["recall"] for d in details)/len(details),
                    "mrr_at_k":sum(d["rr"] for d in details)/len(details),
                    "wrong_version_chunks":sum(d["wrong_version_chunks"] for d in details), "cases":details}
            report = {"created_at":now(),"mode":settings.mode,"embedding":provider.fingerprint,
                      "python":platform.python_version(),"k":args.k,"queries":len(cases),
                      "documents":db.one("SELECT COUNT(*) AS n FROM documents")["n"],
                      "chunks":db.one("SELECT COUNT(*) AS n FROM chunks")["n"],
                      "limitation":"人工构造的小型回归集，与文档存在用词关联，不是独立真实用户测试。demo 向量为词法哈希，不能证明语义能力。",
                      "strategies":results}
            output = ROOT / args.output; output.parent.mkdir(parents=True,exist_ok=True)
            output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
            for strategy,result in results.items():
                print(strategy, {k:v for k,v in result.items() if k!="cases"})
            print("Saved:",output)
    finally:
        await provider.close()


if __name__ == "__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--k",type=int,default=3)
    parser.add_argument("--output",default="reports/retrieval-demo.json")
    args=parser.parse_args()
    if not 1 <= args.k <= 10: parser.error("k must be 1..10")
    asyncio.run(main(args))
