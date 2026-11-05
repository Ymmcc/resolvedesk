import json

from fastapi import HTTPException

from .db import encode, new_id, now


class Business:
    def __init__(self, db, knowledge):
        self.db, self.knowledge = db, knowledge

    def customer(self, customer_id):
        row = self.db.one("SELECT * FROM customers WHERE id=?", (customer_id,))
        if not row:
            raise HTTPException(404, "客户不存在")
        return row

    def ticket(self, ticket_id, user):
        row = self.db.one("SELECT * FROM tickets WHERE id=?", (ticket_id,))
        if not row or (user.customer_id and row["customer_id"] != user.customer_id):
            raise HTTPException(404, "工单不存在或不可访问")
        row["evidence"] = json.loads(row["evidence"])
        row["events"] = self.db.all("SELECT * FROM ticket_events WHERE ticket_id=? ORDER BY id", (ticket_id,))
        return row

    def run(self, run_id, user):
        row = self.db.one("SELECT * FROM runs WHERE id=?", (run_id,))
        if not row or (user.customer_id and row["customer_id"] != user.customer_id):
            raise HTTPException(404, "运行记录不存在或不可访问")
        row["result"] = json.loads(row["result"]) if row["result"] else None
        return row

    def escalate(self, run_id, key, user):
        run = self.run(run_id, user)
        if run["status"] != "completed" or run["result"]["resolution"]["decision"] != "escalate":
            raise HTTPException(409, "只有已完成且建议升级的调查可以提交工单")
        result = run["result"]
        operation = "escalate:" + run_id
        with self.db.transaction() as con:
            previous = con.execute("SELECT * FROM idempotency WHERE actor=? AND key=?", (user.id, key)).fetchone()
            if previous:
                if previous["operation"] != operation:
                    raise HTTPException(409, "此幂等键已用于其他操作")
                return {"id": previous["resource_id"], "replayed": True}
            existing = con.execute("SELECT id FROM tickets WHERE run_id=?", (run_id,)).fetchone()
            ticket_id = existing["id"] if existing else new_id("TKT")
            if not existing:
                stamp = now()
                con.execute("""INSERT INTO tickets (id,customer_id,run_id,title,module,version,status,evidence,created_at,updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?)""", (ticket_id, run["customer_id"], run_id,
                    result["resolution"]["summary"] or run["question"][:100], result["triage"]["module"],
                    result["environment"]["version"], "new", encode({"question": run["question"], **result}), stamp, stamp))
                con.execute("INSERT INTO ticket_events (ticket_id,actor,status,note,created_at) VALUES (?,?,?,?,?)",
                            (ticket_id, user.id, "new", "确认提交技术工单；AI 仅生成草稿，未自动执行修复", stamp))
            con.execute("INSERT INTO idempotency VALUES (?,?,?,?)", (user.id, key, operation, ticket_id))
        return {"id": ticket_id, "replayed": bool(existing)}

    def update_ticket(self, ticket_id, update, user):
        self.ticket(ticket_id, user)
        staff_transitions = {
            "new": {"in_progress", "waiting_customer", "waiting_engineering"},
            "in_progress": {"waiting_customer", "waiting_engineering", "pending_confirmation"},
            "waiting_customer": {"in_progress", "waiting_engineering", "pending_confirmation"},
            "waiting_engineering": {"in_progress", "waiting_customer", "pending_confirmation"},
            "pending_confirmation": {"in_progress", "waiting_customer", "waiting_engineering"},
            "resolved": {"in_progress"},
        }
        with self.db.transaction() as con:
            ticket = dict(con.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone())
            if ticket["revision"] != update.expected_revision:
                raise HTTPException(409, "工单已更新，请刷新后操作")
            allowed = staff_transitions.get(ticket["status"], set())
            if user.role == "customer":
                allowed = {"pending_confirmation": {"resolved", "in_progress"},
                           "waiting_customer": {"in_progress"}, "resolved": {"in_progress"}}.get(ticket["status"], set())
                if update.problem_id:
                    raise HTTPException(403, "客户不能关联研发问题")
            if update.status not in allowed:
                raise HTTPException(409, "不允许的状态流转；解决必须由客户确认")
            problem_id = ticket["problem_id"]
            if update.problem_id:
                problem = con.execute("SELECT * FROM problems WHERE id=?", (update.problem_id,)).fetchone()
                if not problem or problem["version"] != ticket["version"] or problem["module"] != ticket["module"]:
                    raise HTTPException(409, "关联问题必须与工单版本和模块一致")
                problem_id = update.problem_id
            resolution = update.note if update.status == "pending_confirmation" else ticket["resolution"]
            if update.status == "in_progress" and ticket["status"] == "resolved":
                # Reopening invalidates unpublished drafts and removes prior published case evidence.
                drafts = con.execute("SELECT document_id FROM knowledge_drafts WHERE ticket_id=?", (ticket_id,)).fetchall()
                con.execute("DELETE FROM knowledge_drafts WHERE ticket_id=?", (ticket_id,))
                for draft in drafts:
                    if draft["document_id"]:
                        con.execute("DELETE FROM documents WHERE id=?", (draft["document_id"],))
                        self.db.bump_index(con)
            con.execute("""UPDATE tickets SET status=?,resolution=?,problem_id=?,revision=revision+1,updated_at=?
                WHERE id=? AND revision=?""", (update.status, resolution, problem_id, now(), ticket_id, update.expected_revision))
            con.execute("INSERT INTO ticket_events (ticket_id,actor,status,note,created_at) VALUES (?,?,?,?,?)",
                        (ticket_id, user.id, update.status, update.note, now()))
        return self.ticket(ticket_id, user)

    def create_draft(self, ticket_id, user):
        user.require_staff()
        with self.db.transaction() as con:
            ticket = con.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone()
            if not ticket or ticket["status"] != "resolved":
                raise HTTPException(409, "仅可从已解决工单生成知识草稿")
            existing = con.execute("SELECT id FROM knowledge_drafts WHERE ticket_id=?", (ticket_id,)).fetchone()
            if existing:
                return {"id": existing["id"], "replayed": True}
            draft_id = new_id("KDR")
            content = f"# 问题现象\n{ticket['title']}\n\n# 已确认解决方案\n{ticket['resolution']}\n\n# 适用范围\n产品版本 {ticket['version']}，仅当前客户可检索。发布前请人工核对事实并清理敏感信息。"
            con.execute("""INSERT INTO knowledge_drafts
                (id,ticket_id,title,content,module,version,customer_id,status,created_at) VALUES (?,?,?,?,?,?,?,?,?)""",
                (draft_id, ticket_id, ticket["title"], content, ticket["module"], ticket["version"], ticket["customer_id"], "draft", now()))
            con.execute("INSERT INTO ticket_events (ticket_id,actor,status,note,created_at) VALUES (?,?,?,?,?)",
                        (ticket_id, user.id, "resolved", f"生成案例知识草稿 {draft_id}", now()))
        return {"id": draft_id, "replayed": False}

    def edit_draft(self, draft_id, edit, user):
        user.require_staff()
        with self.db.transaction() as con:
            row = con.execute("SELECT * FROM knowledge_drafts WHERE id=?", (draft_id,)).fetchone()
            if not row or row["status"] != "draft":
                raise HTTPException(409, "草稿不存在或已发布")
            con.execute("UPDATE knowledge_drafts SET title=?,content=? WHERE id=?", (edit.title, edit.content, draft_id))
        return {"id": draft_id}

    async def publish_draft(self, draft_id, user):
        if user.role != "engineer":
            raise HTTPException(403, "需要技术人员审核发布")
        draft = self.db.one("SELECT * FROM knowledge_drafts WHERE id=?", (draft_id,))
        if not draft:
            raise HTTPException(404, "草稿不存在")
        if draft["status"] == "published":
            return {"id": draft["document_id"], "replayed": True}
        doc_id = new_id("CASE")
        chunks = await self.knowledge.prepare(doc_id, draft["title"], draft["content"])
        with self.db.transaction() as con:
            current = con.execute("SELECT * FROM knowledge_drafts WHERE id=?", (draft_id,)).fetchone()
            if not current:
                raise HTTPException(409, "工单已重新打开，草稿失效")
            if current["status"] == "published":
                return {"id": current["document_id"], "replayed": True}
            if current["content"] != draft["content"] or current["title"] != draft["title"]:
                raise HTTPException(409, "草稿在索引期间被编辑，请重新发布")
            ticket = con.execute("SELECT status FROM tickets WHERE id=?", (draft["ticket_id"],)).fetchone()
            if ticket["status"] != "resolved":
                raise HTTPException(409, "工单未解决，不可发布")
            con.execute("INSERT INTO documents VALUES (?,?,?,?,?,?,?)", (doc_id, draft["title"], draft["module"],
                        draft["version"], draft["content"], draft["customer_id"], now()))
            con.executemany("INSERT INTO chunks VALUES (?,?,?,?,?,?)", chunks)
            con.execute("UPDATE knowledge_drafts SET status='published',document_id=? WHERE id=?", (doc_id, draft_id))
            con.execute("INSERT INTO ticket_events (ticket_id,actor,status,note,created_at) VALUES (?,?,?,?,?)",
                        (draft["ticket_id"], user.id, "resolved", f"审核发布案例知识 {doc_id}，保留当前客户范围", now()))
            self.db.bump_index(con)
        return {"id": doc_id, "chunks": len(chunks), "replayed": False}
