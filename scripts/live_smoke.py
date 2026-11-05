"""A small paid live smoke test, never a concurrent load test."""
import argparse
import asyncio
import json
from pathlib import Path
import sys
import time

import httpx

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.config import ROOT, Settings
from app.db import now


async def main(args):
    if not args.allow_live:raise SystemExit('Pass --allow-live: this makes a few real model requests')
    settings=Settings.from_env()
    cases=[
        ('import','IMP-1001 提示 E2013，升级后导入失败，请根据实际任务和适用版本说明如何处理。'),
        ('export','报表导出失败，任务 EXP-1002，E3008。请核实后帮我整理技术工单草稿。'),
        ('permission','AUTH-1003 提示 E4030，为什么不能导出报表？请查询后说明处理方法，不要替我修改权限。'),
    ]
    report={'created_at':now(),'mode':'live','llm_model':settings.llm_model,'embedding_model':settings.embed_model,
            'scope':'3 条自建虚构业务样例；只检查链路、适用版本、引用来源和读工具，无并发压测。','cases':[]}
    async with httpx.AsyncClient(base_url=args.url,timeout=settings.run_timeout+10,
                                  headers={'Authorization':'Bearer '+settings.customer_a_token}) as client:
        config=(await client.get('/api/config')).json()
        if config['mode']!='live':raise SystemExit('Target server is not live')
        for module,question in cases:
            start=time.perf_counter()
            response=await client.post('/api/ask',json={'question':question,'customer_id':'acme'})
            item={'module':module,'question':question,'status':response.status_code,'duration_ms':round((time.perf_counter()-start)*1000,2)}
            result=response.json()
            if response.is_success:
                available={c['id'] for c in result.get('evidence',{}).get('knowledge',[])}
                item.update(run_id=result['id'],resolution=result['resolution'],triage=result['triage'],
                    citation_ids_valid=all(c in available for c in result['resolution']['citation_ids']),
                    correct_version=all(c['version'] in {'2.0','all'} for c in result['evidence']['knowledge']),
                    tool_calls=[t['detail']['name'] for t in result['trace'] if t['event']=='tool'],
                    writes_executed=sum(t['detail'].get('writes_executed',0) for t in result['trace'] if isinstance(t['detail'],dict)))
            else:item['error']=result.get('detail')
            report['cases'].append(item)
            print(json.dumps(item,ensure_ascii=False))
            (ROOT/'reports'/'live-smoke.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    if any(c['status']!=200 or not c.get('citation_ids_valid') or not c.get('correct_version') for c in report['cases']):
        raise SystemExit('Live smoke did not pass; see sanitized report')


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--url',default='http://127.0.0.1:8125');p.add_argument('--allow-live',action='store_true')
    asyncio.run(main(p.parse_args()))
