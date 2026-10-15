"""KiCad AI Review - AI-gestütztes Schaltplan-Review für KiCad 10.

Öffentliche API::

    from kicad_ai import SchematicReviewer, LLMConfig, Severity

    reviewer = SchematicReviewer(llm_config=LLMConfig(backend="ollama", model="qwen2.5-coder:14b"))
    result = reviewer.review("/pfad/projekt.kicad_sch", use_ai=True)
    print(result.summary)
    for finding in result.findings[:10]:
        print(finding.severity.label, finding.title)
"""

from .context import build_context, render_prompt
from .loader import KicadCli, SchematicLoader, resolve_project
from .llm import LLMConfig, LLMError, build_client
from .model import Design, Net, PinType, SymInstance
from .reviewer import Requirement, ReviewCancelled, ReviewResult, SchematicReviewer
from .rules import Finding, RULES, RuleContext, Severity, check_all

__version__ = "0.1.0"
__all__ = [
    "__version__",
    "SchematicReviewer",
    "ReviewResult",
    "Requirement",
    "Finding",
    "Severity",
    "RuleContext",
    "check_all",
    "RULES",
    "Design",
    "SymInstance",
    "Net",
    "PinType",
    "SchematicLoader",
    "KicadCli",
    "resolve_project",
    "LLMConfig",
    "LLMError",
    "build_client",
    "build_context",
    "render_prompt",
    "ReviewCancelled",
]