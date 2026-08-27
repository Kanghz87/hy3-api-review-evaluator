"""Public, secret-safe application errors."""


class EvaluatorError(Exception):
    """Base class for errors safe to display in the UI or CLI."""


class ConfigurationError(EvaluatorError):
    """Invalid or missing local configuration."""


class SpecInputError(EvaluatorError):
    """Unsafe, malformed, or unsupported OpenAPI input."""


class ProviderError(EvaluatorError):
    """Sanitized error returned when a Hy3 call cannot complete."""


class BudgetExceededError(EvaluatorError):
    """A Hy3 call was refused because it could exceed the configured budget."""


class StructuredOutputError(EvaluatorError):
    """Hy3 returned content that cannot be validated as the required schema."""
