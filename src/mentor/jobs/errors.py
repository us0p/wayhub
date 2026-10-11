"""Outcomes of reading a submission that aren't failures of ours."""


class NeedsManualError(Exception):
    """The source can't be read automatically; the user is asked for the text or prints.
    `reason` is a short code the job page words (`templates/jobs/_status.html`)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class NotAJobError(Exception):
    """The source was read, and it isn't a job posting."""
