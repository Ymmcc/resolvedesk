from dataclasses import dataclass
import hmac

from fastapi import HTTPException


@dataclass(frozen=True)
class Principal:
    id: str
    role: str
    customer_id: str | None = None

    def require_staff(self):
        if self.role not in {"support", "engineer"}:
            raise HTTPException(403, "需要客服或技术人员权限")

    def scope(self, requested):
        if self.customer_id and requested != self.customer_id:
            raise HTTPException(403, "无权访问此客户范围")
        return self.customer_id or requested


def authenticate(settings, authorization):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "请提供 Bearer 访问凭证")
    token = authorization[7:]
    choices = [
        (settings.customer_a_token, Principal("customer-acme", "customer", "acme")),
        (settings.customer_b_token, Principal("customer-beta", "customer", "beta")),
        (settings.support_token, Principal("support-lin", "support")),
        (settings.engineer_token, Principal("engineer-chen", "engineer")),
    ]
    for expected, principal in choices:
        if hmac.compare_digest(token.encode(), expected.encode()):
            return principal
    raise HTTPException(401, "访问凭证无效")
