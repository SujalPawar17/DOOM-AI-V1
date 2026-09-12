"""V8.7 ledger errors. Persistence is not authorization or execution."""

from __future__ import annotations

from orchestration.task.errors import TaskError


class LedgerUnavailable(TaskError):
    def __init__(self, code: str = "LEDGER_UNAVAILABLE") -> None:
        super().__init__(code)


class LedgerMigrationError(TaskError):
    def __init__(self) -> None:
        super().__init__("LEDGER_MIGRATION_ERROR")


class TaskAlreadyExists(TaskError):
    def __init__(self) -> None:
        super().__init__("TASK_ALREADY_EXISTS")


class LedgerConcurrencyError(TaskError):
    def __init__(self) -> None:
        super().__init__("LEDGER_CONCURRENCY_ERROR")


class LedgerCapacityError(TaskError):
    def __init__(self) -> None:
        super().__init__("LEDGER_CAPACITY_ERROR")


class LedgerValidationError(TaskError):
    def __init__(self, code: str = "LEDGER_VALIDATION_ERROR") -> None:
        super().__init__(code)


class LedgerSerializationError(TaskError):
    def __init__(self) -> None:
        super().__init__("LEDGER_SERIALIZATION_ERROR")


class LedgerTransactionError(TaskError):
    def __init__(self, code: str = "LEDGER_TRANSACTION_ERROR") -> None:
        super().__init__(code)
