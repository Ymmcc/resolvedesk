"""Small live compatibility probe. Reads local .env; never logs secrets or raw error bodies."""
import asyncio
import json
from pathlib import Path
import sys
import time

import httpx

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.config import ROOT, Settings
from app.db import now
from app.providers import ProviderError, Providers
from app.models import Triage


async def main():
    settings=Settings.from_env()
    if settings.mode!='live':raise SystemExit('Expected RD_MODE=live; check .env encoding/configuration')
    report={"created_at":now(),"llm_model":settings.llm_model,"embedding_model":settings.embed_model,"checks":{}}
    async with httpx.AsyncClient(timeout=settings.http_timeout) as client:
        try:
            response=await client.get(settings.embed_base_url.rstrip('/')+'/models',headers={'Authorization':'Bearer '+settings.embed_api_key})
            ids=[r['id'] for r in (response.json().get('data') or []) if isinstance(r,dict) and isinstance(r.get('id'),str)] if response.is_success else []
            report['checks']['embedding_catalog']={"status":response.status_code,"configured_model_present":settings.embed_model in ids,
                "embedding_candidates":[i for i in ids if any(w in i.lower() for w in ['embedding','bge-m3','bge-large','gte'])][:20]}
        except (httpx.HTTPError,ValueError,KeyError):
            report['checks']['embedding_catalog']={"error":"catalog unavailable"}
    provider=Providers(settings)
    try:
        start=time.perf_counter()
        try:
            vectors=await provider.embed(['订单导入失败，提示旧模板字段。','表格上传时报字段不兼容。'])
            report['checks']['embedding']={"ok":True,"shape":list(vectors.shape),"duration_ms":round((time.perf_counter()-start)*1000,2)}
        except ProviderError as e:report['checks']['embedding']={"ok":False,"error":str(e)}
        start=time.perf_counter()
        try:
            result=await provider.structured('你是售后分诊，请识别订单导入问题，只输出 JSON。',{'question':'任务 IMP-1001 导入失败 E2013'},Triage)
            report['checks']['structured_chat']={"ok":True,"output":result.model_dump(),"duration_ms":round((time.perf_counter()-start)*1000,2)}
        except ProviderError as e:report['checks']['structured_chat']={"ok":False,"error":str(e)}
    finally:await provider.close()
    output=ROOT/'reports'/'provider-probe.json';output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':asyncio.run(main())
