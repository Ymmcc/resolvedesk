import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.providers import hash_vectors
from app.providers import ProviderError, Providers
from app.models import Resolution, Triage


TOKENS = {"a": "demo-customer-a", "b": "demo-customer-b", "s": "demo-support", "e": "demo-engineer"}


class PlatformTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = create_app(Settings(db_path=str(Path(self.tmp.name) / "test.db")))
        self.client = TestClient(self.app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.tmp.cleanup()

    def request(self, method, path, actor="a", **kwargs):
        return self.client.request(method, path, headers={"Authorization": "Bearer " + TOKENS[actor]}, **kwargs)

    def ask(self, question="EXP-1002 导出超时，帮我调查并提交工单", actor="a", customer_id="acme", **extra):
        response = self.request("POST", "/api/ask", actor, json={"question": question, "customer_id": customer_id, **extra})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def make_ticket(self):
        run = self.ask()
        response = self.request("POST", f"/api/runs/{run['id']}/escalate", json={"idempotency_key": "test-key-123"})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["id"], run

    def patch(self, ticket_id, status, actor="s", note="已经核实处理记录", problem_id=None, revision=None):
        ticket = self.request("GET", f"/api/tickets/{ticket_id}", actor).json()
        return self.request("PATCH", f"/api/tickets/{ticket_id}", actor,
            json={"status": status, "note": note, "expected_revision": ticket["revision"] if revision is None else revision, "problem_id": problem_id})

    def resolved_ticket(self):
        ticket_id, _ = self.make_ticket()
        self.assertEqual(self.patch(ticket_id, "in_progress").status_code, 200)
        self.assertEqual(self.patch(ticket_id, "pending_confirmation", actor="e", note="已缩小日期范围，重新导出成功，客户可验证下载。").status_code, 200)
        self.assertEqual(self.patch(ticket_id, "resolved", actor="a", note="已确认可以下载报表").status_code, 200)
        return ticket_id

    def test_startup_and_seed(self):
        self.assertEqual(self.client.get("/health").json()["status"], "ok")
        self.assertEqual(len(self.request("GET", "/api/knowledge").json()), 12)
        self.assertGreater(self.request("GET", "/api/stats").json()["chunks"], 0)

    def test_authentication_required(self):
        self.assertEqual(self.client.get("/api/me").status_code, 401)
        self.assertEqual(self.client.get("/api/me", headers={"Authorization": "Bearer wrong"}).status_code, 401)

    def test_customer_scope_is_server_enforced(self):
        self.assertEqual(self.request("GET", "/api/tasks?customer_id=beta").status_code, 403)
        self.assertEqual(self.request("POST", "/api/ask", json={"question": "测试查询", "customer_id": "beta"}).status_code, 403)

    def test_foreign_task_does_not_leak(self):
        result = self.ask("请调查 IMP-2001 的导入故障")
        task = result["evidence"]["task"]
        self.assertEqual(task, {"error": "任务不存在或不可访问"})
        self.assertNotIn("远山", json.dumps(result, ensure_ascii=False))

    def test_version_filter_and_customer_version_lock(self):
        body = {"query": "E2013", "version": "2.0", "module": "import", "strategy": "hybrid"}
        hits = self.request("POST", "/api/search", json=body).json()["hits"]
        self.assertTrue(hits)
        self.assertTrue(all(h["version"] in {"2.0", "all"} for h in hits))
        body["version"] = "1.0"
        self.assertEqual(self.request("POST", "/api/search", json=body).status_code, 403)
        hits = self.request("POST", "/api/search", "s", json=body).json()["hits"]
        self.assertTrue(all(h["version"] in {"1.0", "all"} for h in hits))

    def test_three_search_strategies(self):
        for strategy in ["bm25", "vector", "hybrid"]:
            response = self.request("POST", "/api/search", json={"query": "E2013 导入旧模板", "strategy": strategy})
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.json()["hits"])

    def test_three_agents_and_read_only_investigation(self):
        result = self.ask()
        self.assertEqual({e["agent"] for e in result["trace"]}, {"triage", "investigator", "coordinator"})
        self.assertEqual(result["resolution"]["decision"], "escalate")
        self.assertEqual(self.request("GET", "/api/tickets").json(), [])
        self.assertTrue(result["resolution"]["citation_ids"])

    def test_clarification_skips_investigation(self):
        result = self.ask("帮我看看")
        self.assertEqual(result["resolution"]["decision"], "clarify")
        self.assertNotIn("investigator", {e["agent"] for e in result["trace"]})

    def test_import_advice_matches_version(self):
        result = self.ask("IMP-1001 提示 E2013，如何处理？")
        self.assertIn("external_order_id", result["resolution"]["answer"])
        self.assertIn("2.0", result["resolution"]["answer"])

    def test_permission_question_does_not_route_to_export(self):
        result = self.ask("AUTH-1003 提示 E4030，为什么我不能导出报表？")
        self.assertEqual(result["triage"]["module"], "permission")
        self.assertIn("permission-v2:0", result["resolution"]["citation_ids"])

    def test_idempotent_escalation_and_unique_run(self):
        ticket_id, run = self.make_ticket()
        for key in ["test-key-123", "another-key-123"]:
            response = self.request("POST", f"/api/runs/{run['id']}/escalate", json={"idempotency_key": key})
            self.assertEqual(response.json()["id"], ticket_id)
        self.assertEqual(len(self.request("GET", "/api/tickets").json()), 1)

    def test_idempotency_key_conflict(self):
        self.make_ticket()
        run = self.ask()
        response = self.request("POST", f"/api/runs/{run['id']}/escalate", json={"idempotency_key": "test-key-123"})
        self.assertEqual(response.status_code, 409)

    def test_concurrent_escalation_creates_one_ticket(self):
        run = self.ask()
        def submit(n):
            return self.request("POST", f"/api/runs/{run['id']}/escalate", json={"idempotency_key": f"parallel-{n:03d}"})
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(submit, range(8)))
        self.assertTrue(all(r.status_code == 200 for r in results))
        self.assertEqual(len({r.json()["id"] for r in results}), 1)

    def test_customer_cannot_read_other_ticket_or_run(self):
        ticket_id, run = self.make_ticket()
        self.assertEqual(self.request("GET", f"/api/tickets/{ticket_id}", "b").status_code, 404)
        self.assertEqual(self.request("GET", f"/api/runs/{run['id']}", "b").status_code, 404)
        self.assertEqual(self.request("POST", f"/api/runs/{run['id']}/escalate", "b", json={"idempotency_key": "foreign-123"}).status_code, 404)

    def test_roles_and_state_transitions(self):
        ticket_id, _ = self.make_ticket()
        self.assertEqual(self.patch(ticket_id, "in_progress", actor="a").status_code, 409)
        self.assertEqual(self.patch(ticket_id, "resolved", actor="s").status_code, 409)
        self.assertEqual(self.patch(ticket_id, "pending_confirmation", actor="e").status_code, 409)
        self.assertEqual(self.patch(ticket_id, "in_progress").status_code, 200)
        self.assertEqual(self.patch(ticket_id, "pending_confirmation", actor="e").status_code, 200)
        self.assertEqual(self.patch(ticket_id, "resolved", actor="a").status_code, 200)

    def test_optimistic_revision_conflict(self):
        ticket_id, _ = self.make_ticket()
        self.assertEqual(self.patch(ticket_id, "in_progress", revision=0).status_code, 200)
        self.assertEqual(self.patch(ticket_id, "waiting_engineering", revision=0).status_code, 409)

    def test_concurrent_updates_do_not_overwrite(self):
        ticket_id, _ = self.make_ticket()
        def update(status):
            return self.request("PATCH", f"/api/tickets/{ticket_id}", "s", json={"status":status,"expected_revision":0,"note":"并发更新检查"})
        with ThreadPoolExecutor(max_workers=2) as pool:
            codes = sorted(r.status_code for r in pool.map(update, ["in_progress", "waiting_engineering"]))
        self.assertEqual(codes, [200, 409])

    def test_problem_fix_requires_customer_confirmation(self):
        ticket_id, _ = self.make_ticket()
        self.assertEqual(self.patch(ticket_id, "waiting_engineering", problem_id="PRB-001").status_code, 200)
        response = self.request("PATCH", "/api/problems/PRB-001", "e", json={"status":"fixed","resolution":"已优化导出查询，请客户重试验证。","expected_revision":0})
        self.assertIn(ticket_id, response.json()["pending_confirmation_tickets"])
        self.assertEqual(self.request("GET", f"/api/tickets/{ticket_id}").json()["status"], "pending_confirmation")

    def test_wrong_version_problem_rejected(self):
        ticket_id, _ = self.make_ticket()
        p = self.request("POST", "/api/problems", "e", json={"title":"旧版本导出问题","module":"export","version":"1.0","description":"只影响旧版本导出"}).json()["id"]
        self.assertEqual(self.patch(ticket_id, "waiting_engineering", problem_id=p).status_code, 409)

    def test_unresolved_ticket_cannot_create_knowledge(self):
        ticket_id, _ = self.make_ticket()
        self.assertEqual(self.request("POST", f"/api/tickets/{ticket_id}/knowledge-draft", "s").status_code, 409)

    def test_knowledge_approval_scope_and_reopen_withdrawal(self):
        ticket_id = self.resolved_ticket()
        d = self.request("POST", f"/api/tickets/{ticket_id}/knowledge-draft", "s").json()["id"]
        edit = {"title":"星河专属导出案例", "content":"星河专属案例：将导出日期范围缩小到一天，重新生成任务，已验证下载成功，仅适用于本客户的确认记录。"}
        self.assertEqual(self.request("PATCH", f"/api/knowledge-drafts/{d}", "s", json=edit).status_code, 200)
        self.assertEqual(self.request("POST", f"/api/knowledge-drafts/{d}/publish", "s").status_code, 403)
        result = self.request("POST", f"/api/knowledge-drafts/{d}/publish", "e")
        self.assertEqual(result.status_code, 200, result.text)
        doc_id = result.json()["id"]
        self.assertIn(doc_id, {r["id"] for r in self.request("GET", "/api/knowledge").json()})
        self.assertNotIn(doc_id, {r["id"] for r in self.request("GET", "/api/knowledge?customer_id=beta", "b").json()})
        beta_hits = self.request("POST", "/api/search", "s", json={"query":"星河专属案例","version":"2.0","customer_id":"beta"}).json()["hits"]
        self.assertNotIn(doc_id, {r["document_id"] for r in beta_hits})
        self.assertEqual(self.patch(ticket_id, "in_progress", actor="a", note="仍然失败，重新打开调查").status_code, 200)
        self.assertNotIn(doc_id, {r["id"] for r in self.request("GET", "/api/knowledge").json()})
        self.assertEqual(self.request("GET", "/api/knowledge-drafts", "s").json(), [])

    def test_shared_document_indexed_atomically(self):
        body = {"title":"新发布的批次规则","module":"import","version":"2.0","content":"苍穹批次是新的导入标识，使用苍穹批次时必须先核对唯一订单号和模板字段，避免重复提交。"}
        self.assertEqual(self.request("POST", "/api/knowledge", json=body).status_code, 403)
        r = self.request("POST", "/api/knowledge", "e", json=body)
        self.assertEqual(r.status_code, 201, r.text)
        hits = self.request("POST", "/api/search", json={"query":"苍穹批次","strategy":"bm25"}).json()["hits"]
        self.assertIn(r.json()["id"], {c["document_id"] for c in hits})

    def test_extra_parameters_and_large_payload_rejected(self):
        self.assertEqual(self.request("POST", "/api/ask", json={"question":"修改权限","role":"engineer"}).status_code, 422)
        self.assertEqual(self.request("POST", "/api/ask", json={"question":"问"*2001}).status_code, 422)

    def test_admission_limit_returns_429(self):
        self.app.state.agents.active = self.app.state.agents.settings.max_active_runs
        try:
            response = self.request("POST", "/api/ask", json={"question":"导入说明"})
            self.assertEqual(response.status_code, 429)
            self.assertEqual(response.headers["Retry-After"], "2")
        finally:
            self.app.state.agents.active = 0

    def test_followup_context_access(self):
        ticket_id, _ = self.make_ticket()
        self.assertEqual(self.request("POST", "/api/ask", "b", json={"question":"继续调查","customer_id":"beta","ticket_id":ticket_id}).status_code, 404)
        run = self.ask("还是失败，请继续调查", ticket_id=ticket_id)
        self.assertEqual(run["triage"]["task_id"], "EXP-1002")

    def test_invalid_citation_is_blocked(self):
        with patch.object(self.app.state.agents, "demo_resolution", return_value=Resolution(
                answer="无依据的回答", decision="answer", citation_ids=["invented:999"])):
            result = self.ask("IMP-1001 E2013")
        self.assertEqual(result["resolution"]["decision"], "escalate")
        self.assertEqual(result["resolution"]["citation_ids"], [])
        self.assertIn("已拦截", result["resolution"]["answer"])

    def test_embedding_failure_does_not_publish_partial_document(self):
        before = len(self.request("GET", "/api/knowledge").json())
        with patch.object(self.app.state.provider,"embed",new=AsyncMock(side_effect=ProviderError("模型服务超时"))):
            result = self.request("POST","/api/knowledge","e",json={"title":"失败文档测试","module":"import","version":"2.0","content":"这篇文档需要在向量服务失败时保持未发布状态，避免事实和索引不一致。"})
        self.assertEqual(result.status_code,502)
        self.assertEqual(len(self.request("GET", "/api/knowledge").json()),before)

    def test_total_timeout_is_persisted_and_admission_released(self):
        self.app.state.agents.settings = replace(self.app.state.agents.settings,run_timeout=.005)
        async def slow(*args,**kwargs):
            await asyncio.sleep(.05)
        with patch.object(self.app.state.agents.graph,"ainvoke",new=slow):
            response=self.request("POST","/api/ask",json={"question":"IMP-1001 导入失败"})
        self.assertEqual(response.status_code,504)
        run_id=response.json()["detail"]["run_id"]
        self.assertEqual(self.request("GET",f"/api/runs/{run_id}").json()["status"],"failed")
        self.assertEqual(self.app.state.agents.active,0)

    def test_model_failure_still_records_duration(self):
        flow = self.app.state.agents
        flow.settings = replace(flow.settings, mode="live")

        plan = Triage(
            intent="diagnose",
            module="import",
            task_id="IMP-1001"
        )

        with (
            patch.object(
                flow.provider,
                "structured",
                new=AsyncMock(return_value=plan)
            ),
            patch.object(
                flow.provider,
                "chat",
                new=AsyncMock(
                    side_effect=ProviderError("模拟模型超时")
                )
            ),
        ):
            response = self.request(
                "POST",
                "/api/ask",
                json={"question": "IMP-1001 导入失败"}
            )

        self.assertEqual(response.status_code, 502)

        run_id = response.json()["detail"]["run_id"]
        run = self.request("GET", f"/api/runs/{run_id}").json()
        self.assertEqual(run["status"], "failed")

        events = [
            event for event in run["result"]["trace"]
            if event["event"] == "model_request"
        ]
        self.assertEqual(len(events), 1)

class LiveContractTests(unittest.TestCase):
    def test_live_adapter_three_agents_and_denied_tool(self):
        seen = []
        def handle(request):
            body = json.loads(request.content)
            if request.url.path.endswith("/embeddings"):
                vectors = hash_vectors(body["input"]).tolist()
                return httpx.Response(200, json={"data":[{"index":i,"embedding":v} for i,v in enumerate(vectors)]})
            prompt = body["messages"][0]["content"]
            if "接待分诊" in prompt:
                seen.append("triage")
                message = {"content":json.dumps({"intent":"diagnose","module":"import","task_id":"IMP-1001","clarification":""})}
            elif "问题调查" in prompt:
                seen.append("investigator")
                if body["messages"][-1]["role"] != "tool":
                    message = {"content":None,"tool_calls":[{"id":"call-1","type":"function","function":{"name":"create_ticket","arguments":"{}"}}]}
                else:
                    self.assertIn("error", body["messages"][-1]["content"])
                    message = {"content":"任务事实确认旧模板字段，建议下载 v2.0 模板。"}
            else:
                seen.append("coordinator")
                message = {"content":json.dumps({"answer":"任务显示旧模板，请核对 v2.0 字段。","decision":"answer","citation_ids":["import-v2:0"],"summary":"模板不兼容"})}
            return httpx.Response(200, json={"choices":[{"message":message}]})
        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings(db_path=str(Path(tmp)/"live.db"),mode="live",llm_api_key="test-secret",llm_model="fixture",embed_api_key="test-secret",embed_model="fixture")
            client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
            with TestClient(create_app(settings, client)) as c:
                r = c.post("/api/ask",headers={"Authorization":"Bearer demo-customer-a"},json={"question":"IMP-1001 导入失败"})
                self.assertEqual(r.status_code,200,r.text)
                self.assertEqual(r.json()["mode"],"live")
                self.assertEqual(set(seen),{"triage","investigator","coordinator"})
                self.assertEqual(c.get('/api/tickets',headers={"Authorization":"Bearer demo-customer-a"}).json(),[])
                tools = [e for e in r.json()["trace"] if e["event"]=="tool" and e["detail"]["name"]=="create_ticket"]
                self.assertTrue(tools[0]["detail"]["result"].get("error"))

    def test_missing_live_config_fails_loudly(self):
        from app.providers import Providers
        with self.assertRaises(ValueError):
            Providers(Settings(mode="live"))


class ProviderFailureTests(unittest.IsolatedAsyncioTestCase):
    async def test_upstream_error_does_not_expose_secrets(self):
        settings=replace(Settings(),mode="live",llm_model="fixture",embed_model="fixture",llm_api_key="private-key",embed_api_key="private-key")
        client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(500,text="private-key and internal detail")))
        provider=Providers(settings,client)
        try:
            with self.assertRaises(ProviderError) as error:
                await provider.chat([{"role":"user","content":"test"}])
            self.assertNotIn("private-key",str(error.exception))
        finally:await provider.close()


    async def test_malformed_embedding_is_rejected(self):
        settings=replace(Settings(),mode="live",llm_model="fixture",embed_model="fixture",llm_api_key="key",embed_api_key="key")
        client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(200,json={"data":[{"index":0,"embedding":[0,0,0]}]})))
        provider=Providers(settings,client)
        try:
            with self.assertRaises(ProviderError):await provider.embed(["test"])
        finally:await provider.close()


class ConfigurationTests(unittest.TestCase):
    def test_fractional_timeout_environment(self):
        with patch.dict(os.environ,{"RD_MODE":"demo","RD_RUN_TIMEOUT":"0.5","RD_HTTP_TIMEOUT":"1.25"}):
            settings=Settings.from_env()
        self.assertEqual(settings.run_timeout,.5)
        self.assertEqual(settings.http_timeout,1.25)


if __name__ == "__main__":
    unittest.main()
