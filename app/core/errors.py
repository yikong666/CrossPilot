from collections.abc import Sequence


class CrossPilotError(Exception):
    """Base class for expected CrossPilot application errors."""


class ConfigurationError(CrossPilotError):
    """Raised when required runtime configuration is invalid or missing."""


class StructuredOutputError(CrossPilotError):
    """Raised when a model response cannot satisfy its structured contract."""


class GraphQueryError(CrossPilotError):
    """Raised when a governed graph query cannot complete safely."""


class CypherValidationError(GraphQueryError):
    """Raised when generated Cypher violates validation requirements."""


class MissingFieldsError(CrossPilotError):
    def __init__(self, fields: Sequence[str]) -> None:
        self.fields = tuple(fields)
        super().__init__(f"Missing required fields: {', '.join(self.fields)}")
