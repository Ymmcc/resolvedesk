from dataclasses import dataclass
from pathlib import Path
import os

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Settings:
    db_path: str = str(ROOT / "data" / "resolvedesk.db")
    mode: str = "demo"
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = ""
    llm_model: str = ""
    embed_base_url: str = "https://api.openai.com/v1"
    embed_api_key: str = ""
    embed_model: str = ""
    max_active_runs: int = 8
    run_timeout: float = 75.0
    http_timeout: float = 20.0
    customer_a_token: str = "demo-customer-a"
    customer_b_token: str = "demo-customer-b"
    support_token: str = "demo-support"
    engineer_token: str = "demo-engineer"

    @classmethod
    def from_env(cls):
        load_dotenv(ROOT / ".env")
        values = {}
        for name, field in cls.__dataclass_fields__.items():
            value = os.getenv("RD_" + ("DB" if name == "db_path" else name.upper()))
            if value is not None:
                values[name] = type(field.default)(value)
        result = cls(**values)
        if result.mode not in {"demo", "live"}:
            raise ValueError("RD_MODE must be demo or live")
        if result.max_active_runs < 1 or result.run_timeout <= 0 or result.http_timeout <= 0:
            raise ValueError("Concurrency and timeouts must be positive")
        tokens = [result.customer_a_token, result.customer_b_token, result.support_token, result.engineer_token]
        if any(not t for t in tokens) or len(set(tokens)) != 4:
            raise ValueError("Four nonempty distinct role tokens are required")
        return result
