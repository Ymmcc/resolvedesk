"""Closed-loop HTTP load test with warmup, errors, throughput and latency percentiles."""
import argparse
import asyncio
from collections import Counter
import json
import math
import os
from pathlib import Path
import platform
import sys
import time

import httpx

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.db import Database, encode, new_id, now


def percentile(values,p):
    if not values:return None
    values=sorted(values);return round(values[max(0,math.ceil(len(values)*p)-1)],2)


async def main(args):
    headers={"Authorization":"Bearer "+os.getenv("RD_BENCH_TOKEN","demo-support")}
    async with httpx.AsyncClient(base_url=args.url,headers=headers,timeout=90,
                                  limits=httpx.Limits(max_connections=max(args.concurrency,10))) as client:
        config=(await client.get("/api/config")).json()
        if config["mode"]=="live" and not args.allow_live:
            raise SystemExit("Live load generates paid model requests; pass --allow-live explicitly")
        stats=(await client.get("/api/stats")).json()
        run_ids=[]
        if args.scenario=="write":
            if not args.db:raise SystemExit("write requires the same separate benchmark --db as the server")
            if Path(args.db).name=="resolvedesk.db":raise SystemExit("write fixtures must use a separate benchmark database")
            db=Database(args.db)
            fixture={"environment":{"id":"acme","version":"2.0"},"triage":{"module":"export"},
                     "resolution":{"decision":"escalate","summary":"合成写入压测单据","reproduction":"benchmark fixture"},"trace":[],"evidence":{}}
            with db.transaction() as con:
                for _ in range(args.requests+5):
                    run_id=new_id("BENCH");run_ids.append(run_id)
                    con.execute("INSERT INTO runs (id,customer_id,question,status,mode,result,created_at) VALUES (?,?,?,?,?,?,?)",
                                (run_id,"acme","synthetic write fixture","completed","demo",encode(fixture),now()))
        async def request_one(i):
            if args.scenario=="search":
                return await client.post("/api/search",json={"query":["E2013 旧模板导入失败","报表 E3008 导出超时","E4030 导出权限不足"][i%3],"strategy":"hybrid","version":"2.0","customer_id":"acme"})
            if args.scenario=="ask":
                return await client.post("/api/ask",json={"question":["IMP-1001 导入失败 E2013","EXP-1002 导出超时，请提交工单","AUTH-1003 权限问题"][i%3],"customer_id":"acme"})
            return await client.post(f"/api/runs/{run_ids[i]}/escalate",json={"idempotency_key":f"load-{run_ids[i]}"})
        for i in range(5):
            response=await request_one(i)
            if response.status_code!=200:raise SystemExit(f"Warmup failed: {response.status_code} {response.text[:200]}")
        next_index=5 if args.scenario=="write" else 0
        outcomes=[]
        async def worker():
            nonlocal next_index
            end=args.requests+(5 if args.scenario=="write" else 0)
            while next_index<end:
                i=next_index;next_index+=1;started=time.perf_counter()
                try:
                    response=await request_one(i)
                    code=str(response.status_code)
                except httpx.HTTPError:
                    code="transport_error"
                outcomes.append({"status":code,"duration_ms":(time.perf_counter()-started)*1000})
        started=time.perf_counter()
        await asyncio.gather(*(worker() for _ in range(args.concurrency)))
        elapsed=time.perf_counter()-started
        success=[r["duration_ms"] for r in outcomes if r["status"]=="200"]
        report={"created_at":now(),"scenario":args.scenario,"mode":config["mode"],"url":args.url,
                "python":platform.python_version(),"platform":platform.platform(),"cpu_count":os.cpu_count(),
                "corpus":stats,"warmup_requests":5,"requests":len(outcomes),"concurrency":args.concurrency,
                "elapsed_seconds":round(elapsed,3),"attempted_rps":round(len(outcomes)/elapsed,2),
                "successful_rps":round(len(success)/elapsed,2),"statuses":dict(Counter(r["status"] for r in outcomes)),
                "success_latency_ms":{"p50":percentile(success,.5),"p95":percentile(success,.95),"p99":percentile(success,.99)},
                "limitation":"单机 localhost、闭环客户端、热查询缓存、单 worker。demo 无真实模型网络延迟；不是生产容量或长期稳定性结论。write 使用合成已完成运行记录隔离测试建单事务。",
                "samples":outcomes}
        output=Path(args.output);output.parent.mkdir(parents=True,exist_ok=True)
        output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
        print(json.dumps({k:v for k,v in report.items() if k!="samples"},ensure_ascii=False,indent=2))
        print("Saved:",output.resolve())


if __name__=="__main__":
    p=argparse.ArgumentParser();p.add_argument("--url",default="http://127.0.0.1:8124")
    p.add_argument("--scenario",choices=["search","ask","write"],default="search")
    p.add_argument("--requests",type=int,default=300);p.add_argument("--concurrency",type=int,default=8)
    p.add_argument("--db");p.add_argument("--allow-live",action="store_true");p.add_argument("--output",default="reports/load.json")
    args=p.parse_args()
    if args.requests<1 or not 1<=args.concurrency<=256:p.error("invalid requests/concurrency")
    asyncio.run(main(args))
