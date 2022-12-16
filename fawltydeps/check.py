"Compare imports and dependencies"

from typing import Iterable, Tuple, Set
import isort


def compare_imports_to_dependencies(
    imports: Iterable[str], dependencies: Iterable[str]
) -> Tuple[Set[str], Set[str]]:
    """
    Compares imports to dependencies

    Returns set of undeclared non stdlib imports and set of unused dependencies
    """
    non_stdlib_imports = {
        module for module in imports if isort.place_module(module) != "STDLIB"
    }
    unique_dependencies = set(dependencies)
    undeclared = set(non_stdlib_imports) - unique_dependencies
    unused = unique_dependencies - set(non_stdlib_imports)
    return undeclared, unused
