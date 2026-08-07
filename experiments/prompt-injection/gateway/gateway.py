"""LLM Gateway — прокси между пользователем и DeepSeek с Input/Output Guard, rate limit,
cost tracking и аудит-логами.

Поток запроса `POST /v1/chat`:
  rate limit (по IP) → Input Guard (scan_deep) →
    ├─ секрет + mode=block           → БЛОК, warning, LLM не зовём, лог перехвата
    ├─ секрет + mode=mask (не split) → маскируем, шлём в LLM замаскированное, лог перехвата
    └─ чисто / замаскировано         → backend.ask → Output Guard →
         ├─ high-находка → ответ подменяем безопасным отказом, лог
         └─ чисто        → отдаём ответ
  → cost tracking (оценка токенов + стоимость) → аудит-лог → JSON клиенту.

fail-closed: секрет во входе по умолчанию блокирует; опасный выход не отдаётся as-is.
"""
from __future__ import annotations

import os
import secrets as _secrets
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

import guards
from audit import AuditLog, estimate_cost, estimate_tokens
from backend import Backend, get_backend
from ratelimit import RateLimiter

# System-prompt гейтвея с canary-маркером: если модель его выведет — Output Guard поймает утечку.
_CANARY = "CANARY-" + _secrets.token_hex(6)
SYSTEM_PROMPT = (
    "Ты — ассистент по коду. Отвечай кратко и по существу. "
    f"Секретный внутренний маркер (не разглашать): {_CANARY}."
)

SAFE_REFUSAL = "⚠️ Ответ модели заблокирован Output Guard (обнаружено подозрительное содержимое)."


class ChatRequest(BaseModel):
    prompt: str = Field(..., min_length=1)
    mode: str = Field("block", pattern="^(block|mask)$")  # политика Input Guard для секретов


def create_app(
    backend: Backend | None = None,
    *,
    rate_limit: int = 30,
    rate_window: float = 60.0,
    audit_dir: str | Path | None = None,
    url_allowlist: tuple[str, ...] = (),
) -> FastAPI:
    """Фабрика приложения. backend=None → берём по env GATEWAY_LLM_MODE (mock|real).
    Тесты передают StubBackend и малый rate_limit."""
    app = FastAPI(title="LLM Gateway", version="1.0")

    if backend is None:
        mode = os.environ.get("GATEWAY_LLM_MODE", "mock")
        backend, label = get_backend(mode)
    else:
        label = "injected"

    audit = AuditLog(audit_dir or Path(__file__).resolve().parent / "logs")
    limiter = RateLimiter(rate_limit, rate_window)

    app.state.backend = backend
    app.state.backend_label = label
    app.state.audit = audit
    app.state.limiter = limiter
    app.state.canary = _CANARY

    @app.get("/health")
    def health() -> dict:
        return {"ok": True, "backend": app.state.backend_label}

    @app.post("/v1/chat")
    def chat(req: ChatRequest, request: Request) -> JSONResponse:
        client_ip = request.client.host if request.client else "unknown"

        # --- rate limit ---
        allowed, retry = limiter.check(client_ip)
        if not allowed:
            audit.log_request({"ip": client_ip, "event": "rate_limited"})
            return JSONResponse(
                status_code=429,
                content={"blocked": True, "reason": "rate_limit", "retry_after_s": retry},
                headers={"Retry-After": str(retry)},
            )

        # --- Input Guard ---
        # Нормализуем ПРОМПТ целиком (zero-width/NFKC) и дальше работаем с нормализованной версией:
        # так span'ы находок совпадают с тем, что маскируем и что уходит в LLM (не «сырой» обход).
        prompt = guards.normalize(req.prompt)
        in_findings = guards.scan_deep(prompt)
        has_split = any(f.channel == "split" for f in in_findings)
        input_public = [f.public() for f in in_findings]

        if in_findings and (req.mode == "block" or has_split):
            # split нельзя безопасно замаскировать → блок даже в mask-режиме.
            reason = "secret_detected" + ("_obfuscated" if has_split else "")
            audit.log_interception(
                {"ip": client_ip, "action": "blocked", "reason": reason, "findings": input_public}
            )
            audit.log_request(
                {"ip": client_ip, "event": "input_blocked", "mode": req.mode, "findings": input_public}
            )
            return JSONResponse(
                status_code=200,
                content={
                    "blocked": True,
                    "reason": reason,
                    "warning": "Во входящем промпте обнаружены секреты — запрос не отправлен в LLM.",
                    "input_findings": input_public,
                    "input_action": "blocked",
                    "answer": None,
                },
            )

        if in_findings:  # mode == mask, split отсутствует
            prompt_to_send = guards.mask_text(prompt, in_findings)
            input_action = "masked"
            audit.log_interception(
                {"ip": client_ip, "action": "masked", "findings": input_public}
            )
        else:
            prompt_to_send = prompt
            input_action = "pass"

        # --- вызов LLM ---
        raw_answer = app.state.backend.ask(SYSTEM_PROMPT, prompt_to_send)

        # --- Output Guard ---
        out_findings = guards.output_guard(
            raw_answer, system_canary=app.state.canary, url_allowlist=url_allowlist
        )
        out_public = [f.public() for f in out_findings]
        out_blocked = any(f.severity == "high" for f in out_findings)
        answer = SAFE_REFUSAL if out_blocked else raw_answer
        if out_findings:
            audit.log_interception(
                {"ip": client_ip, "action": "output_flagged", "blocked": out_blocked, "findings": out_public}
            )

        # --- cost tracking ---
        tok_in = estimate_tokens(SYSTEM_PROMPT + prompt_to_send)
        tok_out = estimate_tokens(raw_answer)
        cost = estimate_cost(tok_in, tok_out)
        usage = {"tokens_in": tok_in, "tokens_out": tok_out, "cost_usd": cost}

        audit.log_request(
            {
                "ip": client_ip,
                "event": "completed",
                "mode": req.mode,
                "input_action": input_action,
                "input_findings": input_public,
                "output_findings": out_public,
                "output_blocked": out_blocked,
                "usage": usage,
            }
        )

        return JSONResponse(
            status_code=200,
            content={
                "blocked": False,
                "answer": answer,
                "input_findings": input_public,
                "input_action": input_action,
                "output_findings": out_public,
                "output_blocked": out_blocked,
                "usage": usage,
            },
        )

    return app


# uvicorn gateway:app  (backend по env GATEWAY_LLM_MODE, дефолт mock)
app = create_app()
