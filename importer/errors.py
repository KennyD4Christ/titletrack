class ImportFileError(Exception):
    """The uploaded file cannot be used (wrong type, too big, unreadable, no header row...)."""


class MappingError(Exception):
    def __init__(self, messages):
        self.messages = list(messages)
        super().__init__("; ".join(self.messages))


class ImportStateError(Exception):
    """The batch is not in a state where this step is allowed."""


class StaleDryRun(Exception):
    """Data changed since the dry run; run it again before committing."""


class UndoBlocked(Exception):
    def __init__(self, reasons):
        self.reasons = list(reasons)
        super().__init__("; ".join(self.reasons))
