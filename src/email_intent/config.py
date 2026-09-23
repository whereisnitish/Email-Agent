"""Runtime configuration and free-LLM provider selection."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# --------------------------------------------------------------------------
# .env loading (no external dependency)
# --------------------------------------------------------------------------

def load_dotenv(path: str | Path | None = None) -> None:
    """Load KEY=VALUE pairs from a .env file into os.environ (does not override)."""
    candidate = Path(path) if path else Path.cwd() / ".env"
    if not candidate.is_file():
        return
    for line in candidate.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


# --------------------------------------------------------------------------
# Provider registry -- all free tiers
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class ProviderSpec:
    name: str
    env_key: str | None
    default_model: str
    notes: str


PROVIDERS: dict[str, ProviderSpec] = {
    "groq": ProviderSpec(
        "groq", "GROQ_API_KEY", "llama-3.3-70b-versatile",
        "Free tier at console.groq.com. Fast, JSON mode, best default.",
    ),
    "gemini": ProviderSpec(
        "gemini", "GEMINI_API_KEY", "gemini-2.0-flash",
        "Free tier at aistudio.google.com. Native JSON mime type.",
    ),
    "openrouter": ProviderSpec(
        "openrouter", "OPENROUTER_API_KEY", "meta-llama/llama-3.3-70b-instruct:free",
        "Free models at openrouter.ai (':free' suffix).",
    ),
    "ollama": ProviderSpec(
        "ollama", None, "llama3.1",
        "Fully local and free. Requires `ollama serve` on localhost:11434.",
    ),
    "heuristic": ProviderSpec(
        "heuristic", None, "rule-engine",
        "No LLM. Deterministic rule engine, offline, zero cost.",
    ),
}

#: Order in which providers are auto-detected when --provider is not given.
AUTO_ORDER = ("groq", "gemini", "openrouter", "ollama", "heuristic")


@dataclass
class Config:
    """All tunables in one place so behaviour is auditable and testable."""

    provider: str = "auto"
    model: str | None = None
    temperature: float = 0.0
    max_retries: int = 2
    timeout_seconds: int = 45
    ollama_host: str = "http://localhost:11434"

    #: When True, an email asking for 2+ different products is reported as
    #: `multiple_requests`. When False (default, matches the brief's examples)
    #: `multiple_requests` is reserved for emails that mix *different kinds of
    #: ask* (e.g. pricing for one item + availability for another).
    multi_product_is_multiple_requests: bool = False

    #: Fields a sourcing/quoting bot needs before it can act. Anything null
    #: lands in `missing_information` -- computed, never guessed by the model.
    required_product_fields: tuple[str, ...] = (
        "product_type", "quantity", "budget_per_unit", "delivery_deadline",
    )

    #: Values the model returns that cannot be traced back to the email text
    #: are dropped (anti-hallucination). Set False to keep them as warnings.
    drop_ungrounded_values: bool = True
    grounding_similarity: float = 0.82

    #: Fuzzy-match threshold for misspelled product words ("tumbelers").
    fuzzy_threshold: float = 0.80

    #: Fill gaps in the LLM result from the rule engine's reading of the same
    #: email. Only ever fills nulls -- it cannot overwrite a model value, and
    #: everything it contributes is grounded in the text by construction.
    merge_rule_findings: bool = True

    extra: dict = field(default_factory=dict)

    # -- provider resolution -------------------------------------------------

    def resolve_provider(self) -> tuple[str, str]:
        """Return (provider_name, model_name), auto-detecting from env keys."""
        load_dotenv()
        name = (self.provider or "auto").lower()
        if name == "auto":
            name = os.environ.get("LLM_PROVIDER", "auto").lower()
        if name == "auto":
            for candidate in AUTO_ORDER:
                spec = PROVIDERS[candidate]
                if spec.env_key and os.environ.get(spec.env_key):
                    name = candidate
                    break
                if candidate == "ollama" and os.environ.get("OLLAMA_HOST"):
                    name = candidate
                    break
            else:
                name = "heuristic"
        if name not in PROVIDERS:
            raise ValueError(
                f"Unknown provider {name!r}. Choose from: {', '.join(PROVIDERS)}"
            )
        model = self.model or os.environ.get("LLM_MODEL") or PROVIDERS[name].default_model
        return name, model
