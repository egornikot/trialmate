"""Minimal stand-in for the Droid SDK, shaped like the documented API, used only by tests."""
import enum
import json
from pathlib import Path


class Autonomy(enum.Enum):
    OFF = "off"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ToolConfirmationOutcome(enum.Enum):
    PROCEED_ONCE = "proceed_once"
    PROCEED_ALWAYS = "proceed_always"
    PROCEED_AUTO_RUN_HIGH = "proceed_auto_run_high"
    CANCEL = "cancel"


class PermissionOption:
    def __init__(self, label, value):
        self.label, self.value = label, value


class PermissionResponse:
    def __init__(self, outcome):
        self.outcome = outcome


class PermissionRequest:
    def __init__(self, actions):
        self.actions = actions
        self.options = [PermissionOption(o.name, o) for o in ToolConfirmationOutcome]

    def respond(self, outcome, comment=None):
        assert outcome in [o.value for o in self.options], outcome
        return PermissionResponse(outcome)


class InteractionHandlers:
    def __init__(self, on_permission=None):
        self.on_permission = on_permission


class CreateFile:
    def __init__(self, file_path, content):
        self.file_path, self.file_name, self.content = file_path, Path(file_path).name, content


class ExecuteAction:
    def __init__(self, command):
        self.command = self.full_command = command


class McpToolAction:
    def __init__(self, server_name, tool_name):
        self.server_name, self.tool_name, self.actual_tool_name = server_name, f"{server_name}___{tool_name}", tool_name


class SessionConfig:
    def __init__(self, autonomy=None, mcp_servers=None, **kw):
        self.autonomy, self.mcp_servers = autonomy, mcp_servers or []


class ToolCall:
    def __init__(self, name, input):
        self.name, self.input = name, input


class AssistantMessage:
    def __init__(self, text):
        self.text = text


class RunResult:
    def __init__(self, text):
        self.text, self.subtype, self.success = text, "success", True


class _Stream:
    def __init__(self, session):
        self.session, self.result = session, None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def __aiter__(self):
        return self._events()

    async def _events(self):
        cwd = Path(self.session.cwd)
        record = json.loads((cwd / "trial.json").read_text())
        yield AssistantMessage("Reading trial.json and SCHEMA.md")
        ask = self.session.interactions.on_permission  # real Droid asks before creating files
        assert ask(PermissionRequest([CreateFile(str(cwd / "study.json"), "{}")])).outcome == ToolConfirmationOutcome.PROCEED_ONCE
        assert ask(PermissionRequest([CreateFile("study.json", "{}")])).outcome == ToolConfirmationOutcome.PROCEED_ONCE
        assert ask(PermissionRequest([CreateFile(str(cwd.parent / "evil.json"), "{}")])).outcome == ToolConfirmationOutcome.CANCEL
        assert ask(PermissionRequest([ExecuteAction("python -c 'print(1)'")])).outcome == ToolConfirmationOutcome.CANCEL
        assert ask(PermissionRequest([McpToolAction("trialmate", "check_study")])).outcome == ToolConfirmationOutcome.PROCEED_ONCE
        from trialmate.study_builder import fallback_config
        study = fallback_config(record)
        study["red_flags"][0]["pattern"] = "(("  # first attempt has a bug, like a real agent might
        (cwd / "study.json").write_text(json.dumps(study))
        yield ToolCall("Create", {"file_path": "study.json"})
        check = self.session.config.mcp_servers[0]["tools"][0]
        yield ToolCall("check_study", {})
        first = await check()
        assert first.startswith("ERRORS"), first
        study = fallback_config(record)
        (cwd / "study.json").write_text(json.dumps(study))
        yield ToolCall("Edit", {"file_path": "study.json"})
        second = await check()
        assert second.startswith("OK"), second
        self.result = RunResult("Set up the study with 5 FAQ answers.")


class Session:
    def __init__(self, cwd=None, config=None, model=None, interactions=None, **kw):
        self.cwd, self.config, self.interactions = cwd, config, interactions

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def stream(self, prompt):
        assert "check_study" in prompt
        return _Stream(self)


async def run(prompt, cwd=None, config=None, **kw):
    return RunResult("OK")
