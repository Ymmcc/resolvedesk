from contextlib import asynccontextmanager
from dataclasses import asdict
import json
import sqlite3

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .agents import AgentFlow
from .auth import authenticate
from .business import Business
from .config import ROOT, Settings
from .db import Database, new_id, now
from .knowledge import Knowledge
from .models import (Ask, DocumentCreate, DraftEdit, Escalate, ProblemCreate,
                     ProblemUpdate, Search, TicketUpdate)
from .providers import ProviderError, Providers
from .seed import seed


def create_app(settings=None, provider_client=None):
    settings = settings or Settings.from_env()
    db = Database(settings.db_path)

    @asynccontextmanager
    async def lifespan(app):
        db.initialize()
        seed(db)
        db.execute("UPDATE runs SET status='failed',error='服务重启中断；请重新发起调查' WHERE status='running'")
        provider = Providers(settings, client=provider_client)
        try:
            knowledge = Knowledge(db, provider)
            await knowledge.initialize()
            app.state.db, app.state.knowledge, app.state.provider = db, knowledge, provider
            app.state.business = Business(db, knowledge)
            app.state.agents = AgentFlow(db, knowledge, provider, settings)
            yield
        finally:
            await provider.close()

    app = FastAPI(title="ResolveDesk", version="0.1.0", lifespan=lifespan,
                  description="单产品 SaaS 售后诊断、人工确认与工单协同。默认演示数据；/docs 可调试 API。")

    def user(authorization: str | None = Header(default=None)):
        return authenticate(settings, authorization)

    @app.exception_handler(ProviderError)
    async def provider_error(_, exc):
        return JSONResponse(status_code=502, content={"detail": str(exc)})

    @app.exception_handler(sqlite3.OperationalError)
    async def database_error(_, exc):
        return JSONResponse(status_code=503, content={"detail": "数据库暂时不可用，请稍后重试"}, headers={"Retry-After": "2"})

    @app.get("/health")
    def health():
        db.one("SELECT 1 AS ready")
        return {"status": "ok", "mode": settings.mode}

    @app.get("/api/config")
    def config():
        return {"product": "ResolveDesk", "mode": settings.mode,
                "embedding": "lexical-hash (非语义模型)" if settings.mode == "demo" else settings.embed_model,
                "llm": "规则式演示（非 LLM）" if settings.mode == "demo" else settings.llm_model}

    @app.get("/api/me")
    def me(principal=Depends(user)):
        customers = db.all("SELECT * FROM customers WHERE id=?", (principal.customer_id,)) if principal.customer_id else db.all("SELECT * FROM customers ORDER BY id")
        return {**asdict(principal), "customers": customers}

    @app.get("/api/tasks")
    def tasks(customer_id: str = "acme", principal=Depends(user)):
        scope = principal.scope(customer_id)
        return db.all("SELECT * FROM tasks WHERE customer_id=? ORDER BY id", (scope,))

    @app.post("/api/ask")
    async def ask(payload: Ask, principal=Depends(user)):
        scope = principal.scope(payload.customer_id)
        env = app.state.business.customer(scope)
        history = []
        if payload.ticket_id:
            ticket = app.state.business.ticket(payload.ticket_id, principal)
            if ticket["customer_id"] != scope:
                raise HTTPException(403, "工单与当前客户范围不一致")
            history = [{"note": ticket["evidence"]["question"]}] + ticket["events"][-8:]
        return await app.state.agents.run(payload, env, history)

    @app.post("/api/search")
    async def search(payload: Search, principal=Depends(user)):
        scope = principal.scope(payload.customer_id)
        env = app.state.business.customer(scope)
        if principal.role == "customer" and payload.version != env["version"]:
            raise HTTPException(403, "客户只能检索当前使用版本")
        hits = await app.state.knowledge.search(payload.query, payload.version, payload.module,
                                               payload.strategy, payload.top_k, scope)
        return {"hits": hits, "strategy": payload.strategy, "mode": settings.mode}

    @app.get("/api/tickets")
    def tickets(customer_id: str = "acme", status: str | None = None, principal=Depends(user)):
        scope = principal.scope(customer_id)
        rows = db.all("""SELECT id,customer_id,title,module,version,status,problem_id,revision,created_at,updated_at
            FROM tickets WHERE customer_id=? AND (? IS NULL OR status=?) ORDER BY created_at DESC,id DESC LIMIT 100""", (scope, status, status))
        return rows

    @app.get("/api/tickets/{ticket_id}")
    def ticket(ticket_id: str, principal=Depends(user)):
        return app.state.business.ticket(ticket_id, principal)

    @app.post("/api/runs/{run_id}/escalate")
    def escalate(run_id: str, payload: Escalate, principal=Depends(user)):
        return app.state.business.escalate(run_id, payload.idempotency_key, principal)

    @app.patch("/api/tickets/{ticket_id}")
    def update_ticket(ticket_id: str, payload: TicketUpdate, principal=Depends(user)):
        return app.state.business.update_ticket(ticket_id, payload, principal)

    @app.post("/api/tickets/{ticket_id}/knowledge-draft")
    def create_draft(ticket_id: str, principal=Depends(user)):
        return app.state.business.create_draft(ticket_id, principal)

    @app.get("/api/problems")
    def problems(principal=Depends(user)):
        principal.require_staff()
        return db.all("SELECT * FROM problems ORDER BY created_at DESC")

    @app.post("/api/problems", status_code=201)
    def create_problem(payload: ProblemCreate, principal=Depends(user)):
        if principal.role != "engineer":
            raise HTTPException(403, "需要技术人员权限")
        problem_id = new_id("PRB")
        db.execute("INSERT INTO problems (id,title,module,version,description,status,created_at) VALUES (?,?,?,?,?,?,?)",
                   (problem_id, payload.title, payload.module, payload.version, payload.description, "investigating", now()))
        return {"id": problem_id}

    @app.patch("/api/problems/{problem_id}")
    def update_problem(problem_id: str, payload: ProblemUpdate, principal=Depends(user)):
        if principal.role != "engineer":
            raise HTTPException(403, "需要技术人员权限")
        with db.transaction() as con:
            problem = con.execute("SELECT * FROM problems WHERE id=?", (problem_id,)).fetchone()
            if not problem:
                raise HTTPException(404, "问题不存在")
            if problem["revision"] != payload.expected_revision:
                raise HTTPException(409, "问题已更新，请刷新后操作")
            con.execute("UPDATE problems SET status=?,resolution=?,revision=revision+1 WHERE id=?",
                        (payload.status, payload.resolution, problem_id))
            affected = []
            if payload.status == "fixed":
                rows = con.execute("SELECT id FROM tickets WHERE problem_id=? AND status IN ('waiting_engineering','in_progress')", (problem_id,)).fetchall()
                affected = [r["id"] for r in rows]
                for ticket_id in affected:
                    con.execute("UPDATE tickets SET status='pending_confirmation',resolution=?,revision=revision+1,updated_at=? WHERE id=?", (payload.resolution, now(), ticket_id))
                    con.execute("INSERT INTO ticket_events (ticket_id,actor,status,note,created_at) VALUES (?,?,?,?,?)",
                                (ticket_id, principal.id, "pending_confirmation", f"关联问题 {problem_id} 已处理：{payload.resolution}；等待客户确认", now()))
        return {"id": problem_id, "pending_confirmation_tickets": affected}

    @app.get("/api/knowledge")
    def documents(customer_id: str = "acme", principal=Depends(user)):
        scope = principal.scope(customer_id)
        return db.all("""SELECT d.id,d.title,d.module,d.version,d.content,d.customer_id,d.created_at,
            (SELECT COUNT(*) FROM chunks c WHERE c.document_id=d.id) AS chunks FROM documents d
            WHERE customer_id IS NULL OR customer_id=? ORDER BY created_at DESC,d.id""", (scope,))

    @app.post("/api/knowledge", status_code=201)
    async def add_document(payload: DocumentCreate, principal=Depends(user)):
        if principal.role != "engineer":
            raise HTTPException(403, "需要技术人员权限以发布共享文档")
        return await app.state.knowledge.add_document(payload)

    @app.get("/api/knowledge-drafts")
    def drafts(principal=Depends(user)):
        principal.require_staff()
        return db.all("SELECT * FROM knowledge_drafts ORDER BY created_at DESC")

    @app.patch("/api/knowledge-drafts/{draft_id}")
    def edit_draft(draft_id: str, payload: DraftEdit, principal=Depends(user)):
        return app.state.business.edit_draft(draft_id, payload, principal)

    @app.post("/api/knowledge-drafts/{draft_id}/publish")
    async def publish_draft(draft_id: str, principal=Depends(user)):
        return await app.state.business.publish_draft(draft_id, principal)

    @app.get("/api/runs")
    def runs(customer_id: str = "acme", limit: int = Query(default=30, ge=1, le=100), principal=Depends(user)):
        scope = principal.scope(customer_id)
        return db.all("SELECT id,question,status,mode,created_at,duration_ms,error FROM runs WHERE customer_id=? ORDER BY created_at DESC,id DESC LIMIT ?", (scope, limit))

    @app.get("/api/runs/{run_id}")
    def run(run_id: str, principal=Depends(user)):
        return app.state.business.run(run_id, principal)

    @app.get("/api/stats")
    def stats(customer_id: str = "acme", principal=Depends(user)):
        scope = principal.scope(customer_id)
        return {"ticket_states": db.all("SELECT status,COUNT(*) AS count FROM tickets WHERE customer_id=? GROUP BY status", (scope,)),
                "run_states": db.all("SELECT status,COUNT(*) AS count FROM runs WHERE customer_id=? GROUP BY status", (scope,)),
                "documents": db.one("SELECT COUNT(*) AS count FROM documents WHERE customer_id IS NULL OR customer_id=?", (scope,))["count"],
                "chunks": db.one("SELECT COUNT(*) AS count FROM chunks c JOIN documents d ON d.id=c.document_id WHERE d.customer_id IS NULL OR d.customer_id=?", (scope,))["count"],
                "active_runs": app.state.agents.active}

    @app.get("/")
    def index():
        return FileResponse(ROOT / "web" / "index.html")

    app.mount("/static", StaticFiles(directory=ROOT / "web", check_dir=False), name="static")
    return app


app = create_app()
