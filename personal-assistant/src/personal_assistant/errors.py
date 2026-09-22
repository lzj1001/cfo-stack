class PersonalAssistantError(Exception):
    """Base error for stable public error handling."""


class ValidationError(PersonalAssistantError):
    pass


class AmbiguousInputError(PersonalAssistantError):
    pass


class LLMUnavailableError(PersonalAssistantError):
    pass


class FeishuWriteError(PersonalAssistantError):
    def __init__(self, message: str, *, record_id: str | None = None) -> None:
        super().__init__(message)
        self.record_id = record_id


class DuplicateOperation(PersonalAssistantError):
    pass


class NotFoundError(PersonalAssistantError):
    pass


class SchedulerError(PersonalAssistantError):
    pass
