"""Hy3-only asynchronous client with sanitized errors and token-budget enforcement."""

from __future__ import annotations

from dataclasses import dataclass

from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI

from .budget import TokenBudgetLedger
from .config import Settings
from .errors import ProviderError
from .models import Usage
from .redaction import redact_text


@dataclass(frozen=True, slots=True)
class ModelReply:
    content: str
    usage: Usage


class Hy3Client:
    """Call the configured Hy3 endpoint; no alternate-model fallback exists."""

    def __init__(self, settings: Settings, ledger: TokenBudgetLedger) -> None:
        self._settings = settings
        self._ledger = ledger
        self._client = AsyncOpenAI(
            api_key=settings.require_api_key(),
            base_url=settings.base_url,
            timeout=settings.timeout_seconds,
            max_retries=settings.max_retries,
        )

    async def complete(self, *, system: str, user: str, purpose: str) -> ModelReply:
        combined_prompt = f"SYSTEM\n{system}\nUSER\n{user}"
        reservation = self._ledger.reserve(
            prompt=combined_prompt,
            max_output_tokens=self._settings.max_output_tokens,
        )
        try:
            response = await self._client.chat.completions.create(
                model=self._settings.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=0.1,
                top_p=1.0,
                max_tokens=self._settings.max_output_tokens,
                extra_body={
                    "chat_template_kwargs": {
                        "reasoning_effort": self._settings.reasoning_effort,
                    }
                },
            )
        except APITimeoutError as exc:
            self._ledger.commit(
                reservation,
                Usage(),
                purpose=f"{purpose}:timeout-usage-unknown",
            )
            raise ProviderError("Hy3 request timed out; retry with a smaller document") from exc
        except APIConnectionError as exc:
            self._ledger.commit(
                reservation,
                Usage(),
                purpose=f"{purpose}:connection-usage-unknown",
            )
            raise ProviderError("Could not connect to the configured Hy3 endpoint") from exc
        except APIStatusError as exc:
            self._ledger.release(reservation)
            if exc.status_code in {401, 403}:
                message = "Hy3 rejected the API key or model access"
            elif exc.status_code == 429:
                message = "Hy3 rate limit exceeded; retry later"
            else:
                message = f"Hy3 returned provider error HTTP {exc.status_code}"
            raise ProviderError(message) from exc
        except Exception as exc:
            self._ledger.commit(
                reservation,
                Usage(),
                purpose=f"{purpose}:provider-usage-unknown",
            )
            safe_type = redact_text(
                type(exc).__name__, exact_secrets=[self._settings.api_key or ""]
            )
            raise ProviderError(f"Hy3 request failed safely ({safe_type})") from exc

        try:
            content = response.choices[0].message.content if response.choices else None
            provider_usage = response.usage
            usage = Usage(
                prompt_tokens=getattr(provider_usage, "prompt_tokens", 0) or 0,
                completion_tokens=getattr(provider_usage, "completion_tokens", 0) or 0,
                total_tokens=getattr(provider_usage, "total_tokens", 0) or 0,
            )
        except Exception as exc:
            self._ledger.commit(
                reservation,
                Usage(),
                purpose=f"{purpose}:malformed-response-usage-unknown",
            )
            safe_type = redact_text(
                type(exc).__name__, exact_secrets=[self._settings.api_key or ""]
            )
            raise ProviderError(f"Hy3 response could not be read safely ({safe_type})") from exc
        self._ledger.commit(reservation, usage, purpose=purpose)
        if not content or not content.strip():
            raise ProviderError("Hy3 returned an empty response; reported usage was recorded")
        return ModelReply(content=content.strip(), usage=usage)
