from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Module = Literal["import", "export", "permission", "general"]
Strategy = Literal["bm25", "vector", "hybrid"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Ask(StrictModel):
    question: str = Field(min_length=2, max_length=2000)
    customer_id: str = "acme"
    strategy: Strategy = "hybrid"
    ticket_id: str | None = None


class Triage(StrictModel):
    intent: Literal["consult", "diagnose", "progress", "clarify"] = "diagnose"
    module: Module = "general"
    task_id: str | None = None
    clarification: str = ""


class Resolution(StrictModel):
    answer: str = Field(min_length=1, max_length=6000)
    decision: Literal["answer", "clarify", "escalate"]
    citation_ids: list[str] = Field(default_factory=list, max_length=10)
    summary: str = Field(default="", max_length=200)
    reproduction: str = Field(default="", max_length=3000)
    suggested_problem_id: str | None = None


class Search(StrictModel):
    query: str = Field(min_length=1, max_length=2000)
    version: Literal["1.0", "2.0"] = "2.0"
    module: Module = "general"
    strategy: Strategy = "hybrid"
    top_k: int = Field(default=5, ge=1, le=10)
    customer_id: str = "acme"


class Escalate(StrictModel):
    idempotency_key: str = Field(min_length=8, max_length=100)


class TicketUpdate(StrictModel):
    status: Literal["in_progress", "waiting_customer", "waiting_engineering", "pending_confirmation", "resolved"]
    note: str = Field(min_length=2, max_length=3000)
    expected_revision: int = Field(ge=0)
    problem_id: str | None = None


class ProblemCreate(StrictModel):
    title: str = Field(min_length=3, max_length=200)
    module: Module
    version: Literal["1.0", "2.0"]
    description: str = Field(min_length=3, max_length=4000)


class ProblemUpdate(StrictModel):
    status: Literal["investigating", "fixed"]
    resolution: str = Field(min_length=3, max_length=3000)
    expected_revision: int = Field(ge=0)


class DocumentCreate(StrictModel):
    title: str = Field(min_length=2, max_length=200)
    module: Module
    version: Literal["1.0", "2.0", "all"]
    content: str = Field(min_length=20, max_length=50000)


class DraftEdit(StrictModel):
    title: str = Field(min_length=2, max_length=200)
    content: str = Field(min_length=20, max_length=50000)


class ToolArgs(StrictModel):
    task_id: str | None = Field(default=None, max_length=80)
    query: str | None = Field(default=None, max_length=2000)
