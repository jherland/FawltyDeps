"""An interactive view of a FawltyDeps analysis."""

from dataclasses import fields
from operator import attrgetter
from pathlib import Path

from pydantic import BaseModel  # pylint: disable=no-name-in-module
from rich.highlighter import ReprHighlighter
from rich.text import Text
from textual.app import App, ComposeResult
from textual.widgets import Footer, Header, Tree
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


def get_analysis() -> Analysis:
    """Prepare Analysis object for interactive rendering."""
    settings = Settings.config(config_file=Path("pyproject.toml"))()
    analysis = Analysis.create(settings)

    # Sort imports and declared_deps by .source
    if analysis.imports is not None:
        analysis.imports.sort(key=attrgetter("source", "name"))
    if analysis.declared_deps is not None:
        analysis.declared_deps.sort(key=attrgetter("source", "name"))

    return analysis


def render_location(location: Location) -> Text:
    """Convert Location objects into a Rich renderable."""
    return Text.from_markup(f"[bold]{location}[/]")  # TODO: CSS, link to code view


# pylint: disable=too-many-branches
def add_node(name: str, node: TreeNode[None], obj: object) -> None:
    """Recursively build TUI representation of Analysis data structure."""
    if name not in {"settings", "imports", "declared_deps", "resolved_deps"}:
        node.expand()

    if isinstance(obj, (ParsedImport, DeclaredDependency)):
        color = "green" if isinstance(obj, ParsedImport) else "blue"
        node.allow_expand = False
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
    """

    def compose(self) -> ComposeResult:
        yield Header()
        yield Footer()
        yield Tree("FawltyDeps analysis")

    def on_mount(self) -> None:
        """Load analysis when the app starts."""
        self.title = "FawltyDeps"
        self.sub_title = "Analysis"

        analysis = get_analysis()
        tree = self.query_one(Tree)
        add_node("", tree.root, analysis)

        tree.show_root = False
        tree.root.expand()
        tree.focus()

    def action_toggle_dark(self) -> None:
        """An action to toggle dark mode."""
        self.dark = not self.dark


if __name__ == "__main__":
    app = FawltyDepsApp()
    app.run()
