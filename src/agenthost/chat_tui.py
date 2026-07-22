"""Textual TUI for agenthost chat."""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from textual import events, on, work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.message import Message
from textual.reactive import reactive
from textual.screen import ModalScreen
from rich.markdown import Markdown as RichMarkdown
from rich.text import Text
from textual.widgets import (
    Button,
    Collapsible,
    Footer,
    Label,
    ListItem,
    ListView,
    Static,
    TextArea,
)

# Operations are scoped to the directory from which agenthost was invoked.
_REPO_ROOT = Path.cwd()

_IGNORED_DIRS = {
    ".git",
    ".hg",
    ".svn",
    "venv",
    ".venv",
    "__pycache__",
    "node_modules",
    ".pytest_cache",
    ".mypy_cache",
    ".tox",
    "dist",
    "build",
    ".egg-info",
    ".coverage",
    "target",
}

_PATH_MENTION_RE = re.compile(r"@([\w./-]+|\"[^\"]+\"|'[^']+')")


def _resolve_within_repo(path: str) -> Path | None:
    """Resolve a path relative to the repo root, refusing traversal escapes."""
    try:
        target = (_REPO_ROOT / path).resolve()
        target.relative_to(_REPO_ROOT.resolve())
    except (ValueError, OSError):
        return None
    return target


def _collect_repo_files(prefix: str = "") -> list[str]:
    """Collect file paths under the repo root for completion."""
    root = _REPO_ROOT.resolve()
    prefix = prefix.strip()

    if prefix and not prefix.endswith("/"):
        results: list[str] = []
        for item in root.rglob("*"):
            rel = item.relative_to(root)
            if any(part in _IGNORED_DIRS for part in rel.parts):
                continue
            if any(part.startswith(".") for part in rel.parts):
                continue
            rel_str = str(rel) + ("/" if item.is_dir() else "")
            if rel_str.startswith(prefix):
                results.append(rel_str)
        return sorted(results)[:50]

    base = (root / prefix).resolve() if prefix else root
    if not base.is_dir() or not str(base).startswith(str(root)):
        base = root

    results = []
    try:
        for child in sorted(base.iterdir()):
            rel = child.relative_to(root)
            if child.name in _IGNORED_DIRS:
                continue
            if child.name.startswith("."):
                continue
            results.append(str(rel) + ("/" if child.is_dir() else ""))
    except OSError:
        pass
    return sorted(results)


def _read_context_attachment(token: str) -> str | None:
    """Read a file or summarize a directory for @-mention context."""
    raw = token.strip("\"'").strip()
    target = _resolve_within_repo(raw)
    if target is None:
        return None
    if not target.exists():
        return None

    rel = target.relative_to(_REPO_ROOT.resolve())
    if target.is_file():
        try:
            text = target.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        return f"--- file: {rel} ---\n{text}\n--- end: {rel} ---"

    if target.is_dir():
        lines = [f"--- directory tree: {rel} ---"]
        for item in sorted(target.rglob("*")):
            rel_item = item.relative_to(_REPO_ROOT.resolve())
            if any(part in _IGNORED_DIRS for part in rel_item.parts):
                continue
            if any(part.startswith(".") for part in rel_item.parts):
                continue
            depth = len(rel_item.parts) - 1
            marker = "📁 " if item.is_dir() else "📄 "
            lines.append(f"{'  ' * depth}{marker}{rel_item.name}")
        lines.append(f"--- end: {rel} ---")
        return "\n".join(lines)

    return None


def _parse_context_mentions(text: str) -> tuple[str, list[str]]:
    """Extract @path mentions and build an augmented message.

    Returns the augmented message and a list of resolved attachment paths.
    """
    matches = list(_PATH_MENTION_RE.finditer(text))
    if not matches:
        return text, []

    attachments: list[str] = []
    context_blocks: list[str] = []
    for match in matches:
        token = match.group(1)
        context = _read_context_attachment(token)
        if context is not None:
            context_blocks.append(context)
            target = _resolve_within_repo(token.strip("\"'").strip())
            if target is not None:
                attachments.append(str(target.relative_to(_REPO_ROOT.resolve())))

    augmented = "\n\n".join(context_blocks + [text])
    return augmented, attachments


# ---------------------------------------------------------------------------
# Message widgets
# ---------------------------------------------------------------------------


class UserMessage(Static):
    """A message sent by the user."""

    DEFAULT_CSS = """
    UserMessage {
        width: 100%;
        padding: 0 2 1 2;
        text-style: bold;
        background: $primary-darken-2;
        color: $text;
        border-left: outer $primary;
    }
    """

    def __init__(self, text: str, **kwargs: Any) -> None:
        super().__init__(text, markup=False, **kwargs)


class AssistantMessage(Vertical):
    """A streaming assistant message using a read-only TextArea for text selection."""

    DEFAULT_CSS = """
    AssistantMessage {
        width: 100%;
        height: auto;
        min-height: 1;
        padding: 0 2 1 2;
    }
    AssistantMessage TextArea {
        width: 100%;
        height: auto;
        border: none;
        background: transparent;
        padding: 0;
    }
    AssistantMessage TextArea:focus {
        border: none;
    }
    """

    content = reactive("")

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)

    def compose(self) -> ComposeResult:
        yield TextArea("", read_only=True, show_line_numbers=False)

    def watch_content(self, content: str) -> None:
        try:
            text_area = self.query_one(TextArea)
        except NoMatches:
            return
        text_area.text = content
        self.refresh(layout=True)


class ToolCallCard(Collapsible):
    """Compact, expandable card for a tool call."""

    DEFAULT_CSS = """
    ToolCallCard {
        width: 100%;
        margin: 0 2 1 2;
    }
    """

    def __init__(self, name: str, arguments: dict[str, Any], **kwargs: Any) -> None:
        self.tool_name = name
        self.arguments = arguments
        summary = f"🛠️  {name}"
        super().__init__(title=summary, **kwargs)

    def compose(self) -> ComposeResult:
        yield Static(json.dumps(self.arguments, indent=2, default=str), markup=False)


class ToolResultCard(Collapsible):
    """Compact, expandable card for a tool result."""

    DEFAULT_CSS = """
    ToolResultCard {
        width: 100%;
        margin: 0 2 1 2;
    }
    """

    def __init__(self, name: str, result: str, **kwargs: Any) -> None:
        self.tool_name = name
        self.result = result
        summary = f"✅ {name} result"
        super().__init__(title=summary, collapsed=True, **kwargs)

    def compose(self) -> ComposeResult:
        try:
            payload = json.loads(self.result)
            text = json.dumps(payload, indent=2, default=str)
        except json.JSONDecodeError:
            text = self.result
        yield Static(text, markup=False)


class ThinkingCard(Collapsible):
    """Collapsible card showing the model's reasoning / thinking content."""

    DEFAULT_CSS = """
    ThinkingCard {
        width: 100%;
        margin: 0 2 1 2;
    }
    """

    content = reactive("")

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(title="🧠 thinking", collapsed=True, **kwargs)

    def compose(self) -> ComposeResult:
        yield Static("", markup=False)

    def watch_content(self, content: str) -> None:
        try:
            static = self.query_one(Static)
        except NoMatches:
            return
        static.update(content)
        self.refresh(layout=True)


# ---------------------------------------------------------------------------
# Context panel
# ---------------------------------------------------------------------------


class ThreadPicker(ModalScreen[str | None]):
    """Modal screen for selecting a previous conversation thread."""

    CSS = """
    ThreadPicker {
        align: center middle;
    }
    ThreadPicker > Vertical {
        width: 80;
        height: auto;
        max-height: 30;
        border: thick $background 80%;
        padding: 1 2;
        background: $surface;
    }
    ThreadPicker Label {
        width: 100%;
        text-style: bold;
        margin-bottom: 1;
    }
    ThreadPicker ListView {
        width: 100%;
        height: auto;
        max-height: 20;
        border: solid $primary;
    }
    ThreadPicker ListView > ListItem {
        height: auto;
        padding: 0 1;
    }
    ThreadPicker Button {
        width: 100%;
        margin-top: 1;
    }
    """

    def __init__(self, threads: list[dict[str, Any]], **kwargs: Any) -> None:
        self.threads = threads
        super().__init__(**kwargs)

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label("Select a thread to load:")
            yield ListView(id="thread-list")
            yield Button("Cancel", id="cancel", variant="error")

    def on_mount(self) -> None:
        list_view = self.query_one("#thread-list", ListView)
        for i, thread in enumerate(self.threads, start=1):
            preview = thread.get("latest_message_preview", "")
            role = thread.get("latest_message_role", "")
            ts = thread.get("latest_message_at", "")
            label = f"{i}. {thread['thread_id'][:16]}… [{role}] {ts} — {preview}"
            list_view.append(ListItem(Label(label)))

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        index = event.list_view.index
        if 0 <= index < len(self.threads):
            self.dismiss(self.threads[index]["thread_id"])
        else:
            self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.dismiss(None)


class ContextPanel(Vertical):
    """Side panel showing attached context and touched files."""

    DEFAULT_CSS = """
    ContextPanel {
        min-width: 24;
        height: 100%;
        border: solid $primary;
        padding: 1;
    }
    ContextPanel Static {
        width: 100%;
    }
    """

    def __init__(self, **kwargs: Any) -> None:
        self.attachments: list[str] = []
        self.touched: set[str] = set()
        super().__init__(**kwargs)

    def compose(self) -> ComposeResult:
        yield Label("Context", classes="panel-title")
        yield Static(self._build_text(), id="context-content", markup=False)

    def _build_text(self) -> str:
        lines: list[str] = []
        if self.attachments:
            lines.append("Attached now:")
            for item in self.attachments:
                lines.append(f"  • {item}")
            lines.append("")
        if self.touched:
            lines.append("Touched this thread:")
            for item in sorted(self.touched):
                lines.append(f"  • {item}")
        if not lines:
            lines.append("No context yet.")
            lines.append("Use @path to attach files or directories.")
        return "\n".join(lines)

    def update_content(self) -> None:
        try:
            self.query_one("#context-content", Static).update(self._build_text())
        except NoMatches:
            pass

    def set_attachments(self, items: list[str]) -> None:
        self.attachments = items
        self.update_content()

    def add_touched(self, items: list[str]) -> None:
        self.touched.update(items)
        self.update_content()


# ---------------------------------------------------------------------------
# Input area
# ---------------------------------------------------------------------------


class ChatTextArea(TextArea):
    """TextArea that submits on Enter and inserts a newline on Shift+Enter."""

    class Submit(Message):
        """Posted when the user presses Enter to submit."""

        def __init__(self, text: str) -> None:
            self.text = text
            super().__init__()

    def on_key(self, event: events.Key) -> None:
        if event.key == "enter":
            event.prevent_default()
            event.stop()
            self.post_message(self.Submit(self.text))
            return
        if event.key == "shift+enter":
            event.prevent_default()
            event.stop()
            self.insert("\n")
            return
        # Let all other keys bubble to TextArea's default handling.


class InputArea(Horizontal):
    """Multi-line input with history and @-path completion."""

    DEFAULT_CSS = """
    InputArea {
        height: auto;
        max-height: 12;
        padding: 0 1;
    }
    InputArea ChatTextArea {
        width: 1fr;
        height: auto;
        max-height: 10;
        border: solid $primary;
    }
    InputArea Button {
        width: 8;
        height: 3;
    }
    InputArea ListView {
        width: 1fr;
        height: auto;
        max-height: 8;
        border: solid $primary-darken-2;
        display: none;
    }
    InputArea ListView.-visible {
        display: block;
    }
    """

    class Submitted(Message):
        """Posted when the user submits a message."""

        def __init__(self, text: str) -> None:
            self.text = text
            super().__init__()

    class Command(Message):
        """Posted when the user types a slash command."""

        def __init__(self, name: str, args: str) -> None:
            self.name = name
            self.args = args
            super().__init__()

    def __init__(self, **kwargs: Any) -> None:
        self.history: list[str] = []
        self.history_index: int = -1
        self.pending_text_before_history: str = ""
        self._completions: list[str] = []
        self._completion_index: int = -1
        super().__init__(**kwargs)

    def compose(self) -> ComposeResult:
        with Vertical():
            yield ChatTextArea(id="chat-input")
            yield ListView(id="completion-list")
        yield Button("Send", id="send-button", variant="primary")

    @property
    def text_area(self) -> ChatTextArea:
        return self.query_one("#chat-input", ChatTextArea)

    @property
    def completion_list(self) -> ListView:
        return self.query_one("#completion-list", ListView)

    def _current_line(self) -> str:
        ta = self.text_area
        cursor = ta.cursor_location
        if cursor is None:
            return ta.text
        row, _ = cursor
        lines = ta.text.split("\n")
        if 0 <= row < len(lines):
            return lines[row]
        return ""

    def _mention_prefix(self) -> str | None:
        line = self._current_line()
        cursor = self.text_area.cursor_location
        if cursor is None:
            return None
        _, col = cursor
        before = line[:col]
        match = re.search(r"@([\w./-]*)$", before)
        if match:
            return match.group(1)
        return None

    def _refresh_completions(self) -> None:
        try:
            prefix = self._mention_prefix()
            if prefix is None:
                self._completions = []
                self._completion_index = -1
                self.completion_list.clear()
                self.completion_list.remove_class("-visible")
                return

            candidates = [
                p for p in _collect_repo_files(prefix) if p.startswith(prefix)
            ]
            self._completions = candidates[:20]
            self._completion_index = -1
            self.completion_list.clear()
            self.completion_list.extend(
                [ListItem(Label(candidate)) for candidate in self._completions]
            )
            if self._completions:
                self.completion_list.add_class("-visible")
            else:
                self.completion_list.remove_class("-visible")
        except Exception as exc:  # noqa: BLE001
            # Fail silently; completions are a convenience, not a requirement.
            self._completions = []
            self._completion_index = -1
            try:
                self.completion_list.clear()
                self.completion_list.remove_class("-visible")
            except Exception:
                pass

    def _accept_completion(self) -> None:
        if not self._completions or self._completion_index < 0:
            return
        selected = self._completions[self._completion_index]
        ta = self.text_area
        cursor = ta.cursor_location
        if cursor is None:
            return
        row, col = cursor
        lines = ta.text.split("\n")
        line = lines[row]
        before = line[:col]
        after = line[col:]
        new_before = re.sub(r"@[\w./-]*$", f"@{selected}", before)
        lines[row] = new_before + after
        ta.text = "\n".join(lines)
        ta.cursor_location = (row, len(new_before))
        self._completions = []
        self._completion_index = -1
        self.completion_list.clear()
        self.completion_list.remove_class("-visible")

    @on(TextArea.Changed)
    def _on_text_area_changed(self, event: TextArea.Changed) -> None:
        self._refresh_completions()

    def on_key(self, event: events.Key) -> None:
        key = event.key
        ta = self.text_area

        if key == "tab":
            if self._completions:
                event.prevent_default()
                event.stop()
                self._completion_index = (self._completion_index + 1) % len(
                    self._completions
                )
                self.completion_list.index = self._completion_index
                self._accept_completion()
            return

        if key == "up":
            if self._completions:
                event.prevent_default()
                event.stop()
                self._completion_index = (self._completion_index - 1) % len(
                    self._completions
                )
                self.completion_list.index = self._completion_index
                return
            if ta.cursor_location and ta.cursor_location[0] == 0:
                event.prevent_default()
                event.stop()
                self._history_up()
            return

        if key == "down":
            if self._completions:
                event.prevent_default()
                event.stop()
                self._completion_index = (self._completion_index + 1) % len(
                    self._completions
                )
                self.completion_list.index = self._completion_index
                return
            if ta.cursor_location and ta.cursor_location[0] == ta.text.count("\n"):
                event.prevent_default()
                event.stop()
                self._history_down()
            return

        if key == "escape":
            if self._completions:
                event.prevent_default()
                event.stop()
                self._completions = []
                self._completion_index = -1
                self.completion_list.clear()
                self.completion_list.remove_class("-visible")

    @on(ChatTextArea.Submit)
    def _on_chat_text_area_submit(self, event: ChatTextArea.Submit) -> None:
        text = event.text.strip()
        if not text:
            return
        self.history.append(text)
        self.history_index = -1
        self.pending_text_before_history = ""
        self.text_area.text = ""
        self._completions = []
        self._completion_index = -1
        self.completion_list.clear()
        self.completion_list.remove_class("-visible")

        if text.startswith("/"):
            parts = text.split(None, 1)
            name = parts[0][1:]
            args = parts[1] if len(parts) > 1 else ""
            self.post_message(self.Command(name, args))
        else:
            self.post_message(self.Submitted(text))

    def _history_up(self) -> None:
        if not self.history:
            return
        if self.history_index == -1:
            self.pending_text_before_history = self.text_area.text
        self.history_index = min(self.history_index + 1, len(self.history) - 1)
        self.text_area.text = self.history[-(self.history_index + 1)]

    def _history_down(self) -> None:
        if self.history_index == -1:
            return
        self.history_index -= 1
        if self.history_index == -1:
            self.text_area.text = self.pending_text_before_history
        else:
            self.text_area.text = self.history[-(self.history_index + 1)]

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "send-button":
            text = self.text_area.text.strip()
            if text:
                self._on_chat_text_area_submit(ChatTextArea.Submit(text))

    def focus_input(self) -> None:
        self.text_area.focus()


# ---------------------------------------------------------------------------
# Main app
# ---------------------------------------------------------------------------


class ChatApp(App):
    """Textual chat client for agenthost."""

    CSS = """
    Screen {
        layout: grid;
        grid-size: 2;
        grid-columns: 1fr 30%;
        grid-rows: auto 1fr auto;
    }
    #header {
        column-span: 2;
        height: auto;
        padding: 0 2;
        color: $text;
        text-style: bold;
    }
    #chat-scroll {
        column-span: 1;
        width: 100%;
        height: 100%;
        border: solid $primary-darken-1;
    }
    ContextPanel {
        column-span: 1;
        width: 100%;
        height: 100%;
    }
    #input-area {
        column-span: 2;
        height: auto;
    }
    #status-bar {
        column-span: 2;
        height: auto;
        padding: 0 2;
        color: $text-muted;
    }
    """

    BINDINGS = [
        ("ctrl+c", "interrupt", "Interrupt"),
        ("ctrl+shift+c", "copy", "Copy"),
        ("ctrl+q", "quit", "Quit"),
    ]

    def __init__(
        self,
        url: str,
        agent_name: str = "agent",
        thread_id: str | None = None,
    ) -> None:
        self.url = url
        self.agent_name = agent_name
        self.thread_id = thread_id
        self._client = httpx.AsyncClient(timeout=600.0)
        self._current_assistant: AssistantMessage | None = None
        self._current_thinking: ThinkingCard | None = None
        self._current_tool: ToolCallCard | None = None
        self._stream_task: asyncio.Task | None = None
        self._stream_id: int = 0
        super().__init__()

    def compose(self) -> ComposeResult:
        thread_display = self.thread_id or "<new>"
        yield Static(
            f"agenthost chat — {self.agent_name} — thread: {thread_display}",
            id="header",
        )
        with VerticalScroll(id="chat-scroll"):
            pass
        yield ContextPanel()
        yield InputArea(id="input-area")
        yield Static(
            "Ready. Type a message and press Enter to send.",
            id="status-bar",
            markup=False,
        )
        yield Footer()

    def on_mount(self) -> None:
        self.query_one(InputArea).focus_input()

    @property
    def chat_scroll(self) -> VerticalScroll:
        return self.query_one("#chat-scroll", VerticalScroll)

    @property
    def context_panel(self) -> ContextPanel:
        return self.query_one(ContextPanel)

    @property
    def status_bar(self) -> Static:
        return self.query_one("#status-bar", Static)

    def _set_status(self, text: str) -> None:
        self.status_bar.update(text)

    @on(InputArea.Submitted)
    async def _on_input_submitted(self, event: InputArea.Submitted) -> None:
        text = event.text
        await self.chat_scroll.mount(UserMessage(text))

        augmented, attachments = _parse_context_mentions(text)
        self.context_panel.set_attachments(attachments)

        # Start fresh state for the new assistant turn. Widgets are created lazily
        # when their first event arrives so that thinking cards appear before
        # content bubbles.
        self._current_assistant = None
        self._current_thinking = None
        self._current_tool = None
        self._set_status("Streaming…")
        self._stream_id += 1
        self._stream_task = self._stream_response(augmented, self._stream_id)

    @on(InputArea.Command)
    async def _on_command(self, event: InputArea.Command) -> None:
        if event.name == "clear":
            await self._do_clear()
        elif event.name == "thread":
            await self._do_thread()
        elif event.name == "stop":
            self.action_interrupt()
        elif event.name == "context":
            self._show_context()
        else:
            self._set_status(f"Unknown command: /{event.name}")

    async def _do_clear(self) -> None:
        self._stream_id += 1
        if self._stream_task is not None:
            self._stream_task.cancel()
            self._stream_task = None

        server_cleared = False
        try:
            clear_url = self.url.replace("/chat", "/clear")
            response = await self._client.post(
                clear_url,
                json={"message": "", "thread_id": self.thread_id},
                timeout=3.0,
            )
            response.raise_for_status()
            server_cleared = True
        except Exception as exc:  # noqa: BLE001
            self._set_status(f"Server clear failed ({exc}); UI cleared only.")

        for child in list(self.chat_scroll.children):
            await child.remove()
        self.context_panel.set_attachments([])
        self.context_panel.touched.clear()
        self.context_panel.update_content()
        self._current_assistant = None
        self._current_thinking = None
        self._current_tool = None

        # Start a fresh thread.
        self.thread_id = uuid.uuid4().hex
        self._update_header(self.agent_name, self.thread_id)

        await self.chat_scroll.mount(
            Static("Thread cleared. Started a new thread.", markup=False)
        )
        self.chat_scroll.scroll_end(animate=False)
        if server_cleared:
            self._set_status("Ready.")

    async def _do_thread(self) -> None:
        """Show a thread picker and load the selected thread with full history."""
        try:
            threads_url = self.url.replace("/chat", "/threads")
            response = await self._client.get(threads_url, timeout=5.0)
            response.raise_for_status()
            threads = response.json().get("threads", [])
        except Exception as exc:  # noqa: BLE001
            self._set_status(f"Failed to load threads: {exc}")
            return

        if not threads:
            self._set_status("No previous threads found.")
            return

        def on_thread_selected(selected_thread_id: str | None) -> None:
            if selected_thread_id is None:
                self._set_status("Thread selection cancelled.")
                return
            self.thread_id = selected_thread_id
            self._update_header(self.agent_name, self.thread_id)
            self._load_thread_history(selected_thread_id)

        self.push_screen(ThreadPicker(threads), on_thread_selected)

    @work(exclusive=True)
    async def _load_thread_history(self, thread_id: str) -> None:
        """Fetch and render the full history for a thread."""
        try:
            history_url = self.url.replace("/chat", "/history")
            response = await self._client.get(
                history_url,
                params={"thread_id": thread_id},
                timeout=5.0,
            )
            response.raise_for_status()
            messages = response.json().get("messages", [])
        except Exception as exc:  # noqa: BLE001
            self._set_status(f"Failed to load history: {exc}")
            return

        # Cancel any in-progress stream and clear the UI.
        self._stream_id += 1
        if self._stream_task is not None and not self._stream_task.done():
            self._stream_task.cancel()
        self._stream_task = None

        for child in list(self.chat_scroll.children):
            await child.remove()
        self.context_panel.set_attachments([])
        self.context_panel.touched.clear()
        self.context_panel.update_content()
        self._current_assistant = None
        self._current_thinking = None
        self._current_tool = None

        # Render the conversation history.
        # We use a while loop with an index so we can look ahead at tool messages
        # that follow an assistant's tool_calls.
        i = 0
        n = len(messages)
        while i < n:
            msg = messages[i]
            role = msg.get("role")

            if role == "user":
                content = msg.get("content") or ""
                if content:
                    await self.chat_scroll.mount(UserMessage(content))
                i += 1

            elif role == "assistant":
                tool_calls = msg.get("tool_calls")
                if tool_calls:
                    # Render each tool call and its matching tool response.
                    for tc in tool_calls:
                        name = tc.get("function", {}).get("name", "tool")
                        try:
                            arguments = json.loads(
                                tc.get("function", {}).get("arguments", "{}")
                            )
                        except json.JSONDecodeError:
                            arguments = {}
                        await self.chat_scroll.mount(ToolCallCard(name, arguments))
                        # Look for the matching tool response among following messages.
                        tc_id = tc.get("id")
                        if tc_id:
                            for j in range(i + 1, n):
                                next_msg = messages[j]
                                if (
                                    next_msg.get("role") == "tool"
                                    and next_msg.get("tool_call_id") == tc_id
                                ):
                                    tool_name = next_msg.get("name", name)
                                    result = next_msg.get("content", "")
                                    await self.chat_scroll.mount(
                                        ToolResultCard(tool_name, result)
                                    )
                                    break
                    # Skip past the assistant message with tool_calls.
                    i += 1
                else:
                    content = msg.get("content") or ""
                    if content:
                        assistant = AssistantMessage()
                        await self.chat_scroll.mount(assistant)
                        assistant.content = content
                    i += 1

            elif role == "tool":
                # Tool messages are handled when rendering the preceding assistant's
                # tool_calls above. Skip standalone orphan tool messages.
                i += 1

            else:
                i += 1

        self.chat_scroll.scroll_end(animate=False)
        self._set_status(f"Loaded thread {thread_id}.")

    def _show_context(self) -> None:
        lines: list[str] = ["Current context:"]
        if self.context_panel.attachments:
            lines.append("Attached now:")
            for item in self.context_panel.attachments:
                lines.append(f"  • {item}")
        else:
            lines.append("No files attached for the current prompt.")
        if self.context_panel.touched:
            lines.append("Touched this thread:")
            for item in sorted(self.context_panel.touched):
                lines.append(f"  • {item}")
        else:
            lines.append("No files touched by tools yet.")
        self.chat_scroll.mount(Static("\n".join(lines), markup=False))
        self.chat_scroll.scroll_end(animate=False)

    @work(exclusive=True)
    async def _stream_response(self, message: str, stream_id: int) -> None:
        payload: dict[str, object] = {"message": message}
        if self.thread_id:
            payload["thread_id"] = self.thread_id
        try:
            async with self._client.stream("POST", self.url, json=payload) as response:
                response.raise_for_status()
                current_event: str | None = None
                async for line in response.aiter_lines():
                    line = line.strip()
                    if not line:
                        current_event = None
                        continue
                    if line.startswith("event:"):
                        current_event = line.split(":", 1)[1].strip()
                        continue
                    if not line.startswith("data:") or current_event is None:
                        continue

                    data_part = line.split(":", 1)[1].strip()
                    if current_event == "meta":
                        meta = json.loads(data_part)
                        new_thread = meta.get("thread_id")
                        if new_thread:
                            self.thread_id = new_thread
                            self.call_next(
                                self._update_header, self.agent_name, self.thread_id
                            )
                    elif current_event == "message":
                        event = json.loads(data_part)
                        self.call_next(
                            self._handle_message_event_with_id, stream_id, event
                        )
                    elif current_event == "thinking":
                        event = json.loads(data_part)
                        self.call_next(
                            self._handle_message_event_with_id, stream_id, event
                        )
                    elif current_event == "heartbeat":
                        # Keep-alive event; keeps the HTTP read timeout from firing
                        # during long tool calls.
                        pass
                    elif current_event == "done":
                        self.call_next(self._set_status, "Ready.")
                    elif current_event == "error":
                        self.call_next(self._set_status, f"Error: {data_part}")
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text or str(exc)
            self.call_next(self._set_status, f"HTTP error: {detail}")
        except httpx.RequestError as exc:
            self.call_next(self._set_status, f"Connection error: {exc}")
        except Exception as exc:  # noqa: BLE001
            self.call_next(self._set_status, f"Stream error: {exc}")

    async def _handle_message_event_with_id(
        self, stream_id: int, event: dict[str, Any]
    ) -> None:
        if stream_id != self._stream_id:
            return
        await self._handle_message_event(event)

    def _update_header(self, agent_name: str, thread_id: str) -> None:
        header = self.query_one("#header", Static)
        header.update(f"agenthost chat — {agent_name} — thread: {thread_id}")

    async def _handle_message_event(self, event: dict[str, Any]) -> None:
        event_type = event.get("type")
        event_data = event.get("data")

        if event_type == "continuation":
            # A new LLM call is starting within the same turn (continuation
            # after hitting max_tokens). Reset thinking and content state so
            # the new call gets its own ThinkingCard and content bubble.
            self._current_thinking = None
            self._current_assistant = None

        elif event_type == "thinking":
            if self._current_thinking is None:
                self._current_thinking = ThinkingCard()
                await self.chat_scroll.mount(self._current_thinking)
            self._current_thinking.content += event_data
            self.chat_scroll.scroll_end(animate=False)

        elif event_type == "content":
            if self._current_assistant is None:
                self._current_assistant = AssistantMessage()
                await self.chat_scroll.mount(self._current_assistant)
            self._current_assistant.content += event_data
            self.chat_scroll.scroll_end(animate=False)

        elif event_type == "tool_start":
            # Subsequent assistant content belongs in a new message bubble
            # that appears after the tool cards.
            self._current_assistant = None
            self._current_thinking = None
            name = event_data.get("name", "tool")
            arguments = event_data.get("arguments", {})
            self._current_tool = ToolCallCard(name, arguments)
            await self.chat_scroll.mount(self._current_tool)
            self._track_touched(name, arguments)
            self.chat_scroll.scroll_end(animate=False)

        elif event_type == "tool_result":
            name = event_data.get("name", "tool")
            result = event_data.get("result", "")
            await self.chat_scroll.mount(ToolResultCard(name, result))
            self.chat_scroll.scroll_end(animate=False)

        elif event_type == "tool_error":
            name = event_data.get("name", "tool")
            error = event_data.get("error", "")
            card = ToolResultCard(name, f"ERROR: {error}")
            await self.chat_scroll.mount(card)
            self.chat_scroll.scroll_end(animate=False)

        elif event_type == "error":
            self._current_assistant = None
            self._current_thinking = None
            error = event_data if isinstance(event_data, str) else str(event_data)
            card = ToolResultCard("agent", f"ERROR: {error}")
            await self.chat_scroll.mount(card)
            self.chat_scroll.scroll_end(animate=False)

    def _track_touched(self, name: str, arguments: dict[str, Any]) -> None:
        path_tools = {"read_file", "write_file", "edit_file", "delete_file"}
        if name not in path_tools:
            return
        path = arguments.get("path")
        if not isinstance(path, str):
            return
        target = _resolve_within_repo(path)
        if target is not None:
            rel = str(target.relative_to(_REPO_ROOT.resolve()))
            self.context_panel.add_touched([rel])

    def action_copy(self) -> None:
        """Copy selected text from the currently focused widget to clipboard."""
        focused = self.focused
        if focused is not None and isinstance(focused, TextArea):
            selected = focused.selected_text
            if selected:
                import pyperclip

                pyperclip.copy(selected)
                self._set_status("Copied selected text.")
                return

        # Fallback: copy the full content of the last assistant message.
        for child in reversed(self.chat_scroll.children):
            if isinstance(child, AssistantMessage):
                try:
                    text_area = child.query_one(TextArea)
                    import pyperclip

                    pyperclip.copy(text_area.text)
                    self._set_status("Copied last response.")
                except NoMatches:
                    self._set_status("Nothing to copy.")
                return

        self._set_status("Nothing to copy.")

    def action_interrupt(self) -> None:
        if self._stream_task and not self._stream_task.done():
            self._stream_task.cancel()
            self._set_status("Interrupted.")
        else:
            self.exit()

    def action_quit(self) -> None:
        self.exit()

    async def on_shutdown(self) -> None:
        await self._client.aclose()


# ---------------------------------------------------------------------------
# Chatless monitor mode
# ---------------------------------------------------------------------------


class ChatlessApp(App):
    """Passive SSE monitor for watching an agent thread without chatting."""

    CSS = """
    Screen {
        layout: grid;
        grid-size: 1;
        grid-rows: auto 1fr auto;
    }
    #header {
        height: auto;
        padding: 0 2;
        color: $text;
        text-style: bold;
    }
    #event-log {
        width: 100%;
        height: 100%;
        border: solid $primary-darken-1;
    }
    #status-bar {
        height: auto;
        padding: 0 2;
        color: $text-muted;
    }
    .event-line {
        width: 100%;
        height: auto;
        padding: 0 1;
        text-wrap: wrap;
    }
    """

    BINDINGS = [
        ("ctrl+q", "quit", "Quit"),
    ]

    def __init__(
        self,
        url: str,
        agent_name: str = "agent",
        thread_id: str | None = None,
    ) -> None:
        self.url = url
        self.agent_name = agent_name
        self.thread_id = thread_id
        self._client = httpx.AsyncClient(timeout=600.0)
        self._stream_task: asyncio.Task | None = None
        self._stream_id: int = 0
        super().__init__()

    def compose(self) -> ComposeResult:
        thread_display = self.thread_id or "<new>"
        yield Static(
            f"agenthost monitor — {self.agent_name} — thread: {thread_display}",
            id="header",
        )
        yield VerticalScroll(id="event-log")
        yield Static("Connecting…", id="status-bar", markup=False)
        yield Footer()

    @property
    def event_log(self) -> VerticalScroll:
        return self.query_one("#event-log", VerticalScroll)

    @property
    def status_bar(self) -> Static:
        return self.query_one("#status-bar", Static)

    def _set_status(self, text: str) -> None:
        self.status_bar.update(text)

    def on_mount(self) -> None:
        self._stream_id += 1
        self._stream_task = self._stream_events(self._stream_id)

    @work(exclusive=True)
    async def _stream_events(self, stream_id: int) -> None:
        self._set_status(f"Streaming events from {self.url}…")
        try:
            async with self._client.stream("GET", self.url) as response:
                response.raise_for_status()
                current_event: str | None = None
                async for line in response.aiter_lines():
                    line = line.strip()
                    if not line:
                        current_event = None
                        continue
                    if line.startswith("event:"):
                        current_event = line.split(":", 1)[1].strip()
                        continue
                    if not line.startswith("data:") or current_event is None:
                        continue

                    data_part = line.split(":", 1)[1].strip()
                    self.call_next(
                        self._render_event, stream_id, current_event, data_part
                    )
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text or str(exc)
            self.call_next(self._render_system_line, stream_id, f"HTTP error: {detail}")
            self.call_next(self._set_status, f"HTTP error: {detail}")
        except httpx.RequestError as exc:
            self.call_next(
                self._render_system_line, stream_id, f"Connection error: {exc}"
            )
            self.call_next(self._set_status, f"Connection error: {exc}")
        except Exception as exc:  # noqa: BLE001
            self.call_next(self._render_system_line, stream_id, f"Stream error: {exc}")
            self.call_next(self._set_status, f"Stream error: {exc}")

    async def _render_event(self, stream_id: int, event_type: str, data: str) -> None:
        if stream_id != self._stream_id:
            return

        timestamp = time.strftime("%H:%M:%S", time.localtime())
        prefix = f"[{timestamp}] [{event_type}]"

        if event_type == "meta":
            await self.event_log.mount(Static(f"{prefix} {data}", classes="event-line"))
        elif event_type == "heartbeat":
            self._set_status(f"Heartbeat at {timestamp}")
        elif event_type == "done":
            await self.event_log.mount(
                Static(f"{prefix} turn complete", classes="event-line")
            )
            self._set_status("Turn complete. Waiting for next turn…")
        elif event_type == "error":
            await self.event_log.mount(
                Static(f"{prefix} ERROR: {data}", classes="event-line")
            )
            self._set_status(f"Error: {data}")
        elif event_type in ("message", "thinking"):
            try:
                payload = json.loads(data)
                subtype = payload.get("type", "unknown")
                payload_data = payload.get("data")
            except json.JSONDecodeError:
                subtype = "raw"
                payload_data = data

            if subtype == "content":
                await self.event_log.mount(
                    Static(f"{prefix} content: {payload_data}", classes="event-line")
                )
            elif subtype == "thinking":
                await self.event_log.mount(
                    Static(f"{prefix} thinking: {payload_data}", classes="event-line")
                )
            elif subtype == "tool_start":
                name = (
                    payload_data.get("name", "tool")
                    if isinstance(payload_data, dict)
                    else "tool"
                )
                args = (
                    payload_data.get("arguments", {})
                    if isinstance(payload_data, dict)
                    else {}
                )
                await self.event_log.mount(
                    Static(
                        f"{prefix} tool_start: {name}({json.dumps(args, default=str)})",
                        classes="event-line",
                    )
                )
            elif subtype == "tool_result":
                name = (
                    payload_data.get("name", "tool")
                    if isinstance(payload_data, dict)
                    else "tool"
                )
                result = (
                    payload_data.get("result", "")
                    if isinstance(payload_data, dict)
                    else ""
                )
                await self.event_log.mount(
                    Static(
                        f"{prefix} tool_result: {name} -> {str(result)[:200]}",
                        classes="event-line",
                    )
                )
            elif subtype == "tool_error":
                name = (
                    payload_data.get("name", "tool")
                    if isinstance(payload_data, dict)
                    else "tool"
                )
                error = (
                    payload_data.get("error", "")
                    if isinstance(payload_data, dict)
                    else ""
                )
                await self.event_log.mount(
                    Static(
                        f"{prefix} tool_error: {name} -> {error}",
                        classes="event-line",
                    )
                )
            else:
                await self.event_log.mount(
                    Static(f"{prefix} {subtype}: {payload_data}", classes="event-line")
                )
        else:
            await self.event_log.mount(Static(f"{prefix} {data}", classes="event-line"))

        self.event_log.scroll_end(animate=False)

    async def _render_system_line(self, stream_id: int, text: str) -> None:
        if stream_id != self._stream_id:
            return
        timestamp = time.strftime("%H:%M:%S", time.localtime())
        await self.event_log.mount(
            Static(f"[{timestamp}] [system] {text}", classes="event-line")
        )
        self.event_log.scroll_end(animate=False)

    def action_quit(self) -> None:
        self.exit()

    async def on_shutdown(self) -> None:
        await self._client.aclose()
