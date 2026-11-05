"""Create a separate demo-only synthetic corpus for performance testing."""
import argparse
import asyncio
from dataclasses import replace
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.config import ROOT, Settings
from app.db import Database, now
from app.knowledge import Knowledge
from app.providers import Providers
from app.seed import seed


async def main(args):
    if Path(args.db).resolve() == (ROOT/"data"/"resolvedesk.db").resolve():
        raise SystemExit("Use a separate benchmark database, not the interactive demo database")
    db=Database(args.db);db.initialize();seed(db)
    with db.transaction() as con:
        for i in range(args.documents):
            module=["import","export","permission"][i%3]
            version=["1.0","2.0"][i%2]
            content=f"# 合成排障记录 {i}\n这是性能测试合成文本，不是真实客户案例。产品版本 {version}，模块 {module}，内部批次 BATCH-{i:05d}。\n\n调查时核对客户范围、版本、错误信息和处理历史。对于订单导入需要确认模板字段和数量格式；对于报表导出需要确认任务状态和日期范围；对于权限问题需要联系组织管理员。本记录用于测试索引扫描和并发读取开销，不能作为真实产品问题的解决证据。"
            con.execute("INSERT OR IGNORE INTO documents VALUES (?,?,?,?,?,?,?)", (f"SYN-{i:05d}",f"合成排障记录 {i}",module,version,content,None,now()))
    provider=Providers(replace(Settings(),db_path=args.db))
    try:
        kb=Knowledge(db,provider);await kb.initialize()
        print({"db":str(Path(args.db).resolve()),"documents":db.one("SELECT COUNT(*) AS n FROM documents")["n"],"chunks":db.one("SELECT COUNT(*) AS n FROM chunks")["n"]})
    finally:await provider.close()


if __name__=="__main__":
    p=argparse.ArgumentParser();p.add_argument("--db",default="data/benchmark.db");p.add_argument("--documents",type=int,default=500)
    args=p.parse_args()
    if not 0<=args.documents<=10000:p.error("documents must be 0..10000")
    asyncio.run(main(args))
