"""launchd deployment support (A1.1).

Renders the com.tradingagents.ops.plist.template with resolved absolute
paths. Rendering is deliberately separated from installation: this module
(and the `ops install-service` CLI) only produces the file and prints the
`launchctl bootstrap` command — loading the agent stays an explicit,
reviewable action by the user, never a side effect of running a command.
"""
from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import escape

_TEMPLATE_PATH = Path(__file__).with_name("com.tradingagents.ops.plist.template")
_SCREEN_TEMPLATE_PATH = Path(__file__).with_name("com.tradingagents.screen.plist.template")
_RESEARCH_TEMPLATE_PATH = Path(__file__).with_name("com.tradingagents.research.plist.template")

SERVICE_LABEL = "com.tradingagents.ops"
SCREEN_LABEL = "com.tradingagents.screen"
RESEARCH_LABEL = "com.tradingagents.research"
DEFAULT_PLIST_PATH = "~/Library/LaunchAgents/com.tradingagents.ops.plist"
DEFAULT_SCREEN_PLIST_PATH = "~/Library/LaunchAgents/com.tradingagents.screen.plist"
DEFAULT_RESEARCH_PLIST_PATH = "~/Library/LaunchAgents/com.tradingagents.research.plist"
DEFAULT_LOG_DIR = "~/.local/state/tradingagents/logs"


def _render(template_path: Path, substitutions: dict[str, str]) -> str:
    """Substitute {{PLACEHOLDER}} markers, XML-escaping every value (an '&'
    in an SEC user agent must not produce an invalid plist). Raises if any
    marker survives — a half-rendered plist would fail at launchd load time
    with a far less helpful error."""
    text = template_path.read_text()
    for name, value in substitutions.items():
        text = text.replace("{{" + name + "}}", escape(value))
    if "{{" in text or "}}" in text:
        raise ValueError(
            f"unrendered placeholder left in {template_path.name}")
    return text


def render_launchd_plist(
    *, repo_root: str, venv_python: str, log_dir: str,
) -> str:
    """Render the always-on ops service launchd plist template."""
    return _render(_TEMPLATE_PATH, {
        "REPO_ROOT": repo_root,
        "VENV_PYTHON": venv_python,
        "LOG_DIR": log_dir,
    })


def render_screen_plist(
    *, python_path: str, repo_dir: str, log_dir: str, sec_edgar_user_agent: str = "",
) -> str:
    """Render the weekly screen launchd plist template."""
    return _render(_SCREEN_TEMPLATE_PATH, {
        "VENV_PYTHON": python_path,
        "REPO_ROOT": repo_dir,
        "LOG_DIR": log_dir,
        "SEC_EDGAR_USER_AGENT": sec_edgar_user_agent,
    })


def render_research_plist(
    *, python_path: str, repo_dir: str, log_dir: str, sec_edgar_user_agent: str = "",
    managed_backend: str = "",
) -> str:
    """Render the Saturday-12:00 research-drain launchd plist template.

    Runs two hours after the 10:00 screen (render_screen_plist) so the
    pending-hits queue it fills has settled before this job drains it.
    An empty managed_backend renders OPS_LLM_MANAGED_BACKEND="", which
    load_managed_backend_config() (ops/llm_backend.py) treats as unmanaged
    (falls back to "none") — no conditional templating needed here.
    """
    return _render(_RESEARCH_TEMPLATE_PATH, {
        "VENV_PYTHON": python_path,
        "REPO_ROOT": repo_dir,
        "LOG_DIR": log_dir,
        "SEC_EDGAR_USER_AGENT": sec_edgar_user_agent,
        "OPS_LLM_MANAGED_BACKEND": managed_backend,
    })
