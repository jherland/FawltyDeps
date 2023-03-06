"""An interactive view of a FawltyDeps analysis."""

from dataclasses import fields
from operator import attrgetter
from pathlib import Path
from typing import Optional

from pydantic import BaseModel  # pylint: disable=no-name-in-module
from rich.highlighter import ReprHighlighter
from rich.syntax import Syntax
from rich.text import Text
from rich.traceback import Traceback
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.widgets import Footer, Header, Static, Tree
from textual.widgets.tree import TreeNode

from fawltydeps.main import Analysis
from fawltydeps.packages import Package
from fawltydeps.settings import Settings
from fawltydeps.types import (
    DeclaredDependency,
    Location,
    ParsedImport,
    UndeclaredDependency,
    UnusedDependency,
)
from fawltydeps.utils import is_dataclass_instance

highlighter = ReprHighlighter()


def render_location(location: Location) -> Text:
    """Convert Location objects into a Rich renderable."""
    return Text.from_markup(f"[bold]{location}[/]")  # TODO: CSS, link to code view


# pylint: disable=too-many-branches
def add_node(name: str, node: TreeNode[Optional[Location]], obj: object) -> None:
    """Recursively build TUI representation of Analysis data structure."""
    if name not in {"settings", "imports", "declared_deps", "resolved_deps"}:
        node.expand()

    if isinstance(obj, (ParsedImport, DeclaredDependency)):
        color = "green" if isinstance(obj, ParsedImport) else "blue"
        node.allow_expand = False
        node.data = obj.source
        node.label = Text.assemble(
            render_location(obj.source),
            Text.from_markup(f": [{color}]{obj.name}[/]"),
        )
    elif isinstance(obj, Package):
        node.label = Text.from_markup(
            f"[blue]{name}[/] mapped to package [cyan]{obj.package_name}[/]:"
        )
        for mapping, import_names in obj.mappings.items():
            child = node.add("")
            child.label = Text.assemble(
                Text("provides imports "),
                Text.from_markup(
                    ", ".join(f"[green]{name}[/]" for name in sorted(import_names))
                    + f"[bold black] via {mapping} mapping[/]"
                ),
            )
            child.allow_expand = False
    elif isinstance(obj, (UndeclaredDependency, UnusedDependency)):
        color = "red" if isinstance(obj, UndeclaredDependency) else "blue"
        node.label = Text.from_markup(f"[bold {color}]{obj.name}[/]")
        for ref in obj.references:
            child = node.add(render_location(ref))
            child.data = ref
            child.allow_expand = False
    elif isinstance(obj, BaseModel):  # e.g. Settings
        # TODO: Use TOML formatting to render Settings members
        node.label = Text.from_markup(f"{name}: {obj.__class__.__name__}")
        for member in obj.__class__.__fields__.keys():
            child = node.add("")
            add_node(member, child, getattr(obj, member))
    elif is_dataclass_instance(obj):  # e.g. Analysis
        node.label = Text.from_markup(f"{name}: {obj.__class__.__name__}")
        for field in fields(obj):
            child = node.add("")
            add_node(field.name, child, getattr(obj, field.name))
    elif isinstance(obj, dict):  # e.g. Analysis.resolved_deps
        node.label = Text.from_markup(f"{name}: {{{len(obj)}}}")
        for key, value in sorted(obj.items()):
            child = node.add("")
            add_node(key, child, value)
    elif isinstance(obj, list):  # e.g. Analysis.imports, .declared_deps, etc.
        node.label = Text.from_markup(f"{name}: [{len(obj)}]")
        for index, value in enumerate(obj):
            child = node.add("")
            add_node(str(index), child, value)
    else:  # e.g. Settings members, Analysis.version
        # TODO: How to render enums?
        # if isinstance(obj, Enum) and isinstance(obj.value, str):
        #     str_rep = Text.from_markup(f"[i]{obj.value}[/i]")
        # else:
        str_rep = highlighter(repr(obj))
        node.allow_expand = False
        if name:
            label = Text.assemble(Text.from_markup(f"{name} = "), str_rep)
        else:
            label = str_rep
        node.label = label


class FawltyDepsApp(App[None]):
    """An interactive view of a FawltyDeps analysis."""

    BINDINGS = [
        ("d", "toggle_dark", "Toggle dark mode"),
        ("q", "quit", "Quit"),
    ]

    CSS = """
    Header {
        background: $accent;
        color: $text;
    }
    #tree-view {
        width: 50%;
    }
    #code-view {
        width: 50%;
    }
    """

    analysis: reactive[Optional[Analysis]] = reactive(None)

    def compose(self) -> ComposeResult:
        yield Header()
        yield Footer()
        yield Horizontal(
            Tree("FawltyDeps analysis", id="tree-view"),
            # TODO: code-view should dock/slide in from RHS
            # TODO: Esc to exit/hide code-view
            Vertical(
                Static(id="code", expand=False),
                id="code-view",
            ),
        )

    def set_analysis(self, analysis: Analysis) -> None:
        """Provide the analysis to be shown."""
        # Sort imports and declared_deps by .source
        if analysis.imports is not None:
            analysis.imports.sort(key=attrgetter("source", "name"))
        if analysis.declared_deps is not None:
            analysis.declared_deps.sort(key=attrgetter("source", "name"))
        self.analysis = analysis

    def on_mount(self) -> None:
        """Load analysis when the app starts."""
        self.title = "FawltyDeps"
        self.sub_title = "Analysis"

        tree = self.query_one(Tree)
        assert self.analysis is not None
        add_node("", tree.root, self.analysis)

        tree.show_root = False
        tree.root.expand()
        tree.focus()

    def action_toggle_dark(self) -> None:
        """An action to toggle dark mode."""
        self.dark = not self.dark

    def on_tree_node_selected(
        self, event: Tree.NodeSelected[Optional[Location]]
    ) -> None:
        """Called when the user selects a location in the analysis tree."""
        if event.node.data is None:
            return
        event.stop()
        code_view = self.query_one("#code", Static)
        path = event.node.data.path
        theme = "github-dark" if self.dark else "github-light"  # TODO: make reactive?
        # TODO: Highlight line number
        try:
            syntax = Syntax.from_path(
                str(path),
                line_numbers=True,
                word_wrap=False,
                indent_guides=True,
                theme=theme,
            )
        except Exception:  # pylint: disable=broad-except
            code_view.update(Traceback(theme=theme, width=None))
            self.sub_title = "ERROR"
        else:
            code_view.update(syntax)
            self.query_one("#code-view").scroll_home(animate=False)
            self.sub_title = str(path)


if __name__ == "__main__":
    app = FawltyDepsApp()
    settings = Settings.config(config_file=Path("pyproject.toml"))()
    app.set_analysis(Analysis.create(settings))
    app.run()
