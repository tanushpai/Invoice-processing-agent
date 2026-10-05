class APInvestigatorError(Exception):
    """Base error."""


class PipelineHalt(APInvestigatorError):
    """Raised by a workflow node when processing cannot safely continue -> routes to HUMAN_REVIEW."""
