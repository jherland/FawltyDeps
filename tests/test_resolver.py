"""Verify behavior of packages resolver"""

from pathlib import Path
from typing import Dict, List, Optional

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from fawltydeps.packages import (
    DependenciesMapping,
    Package,
    TemporaryPipInstallResolver,
    UserDefinedMapping,
    resolve_dependencies,
)

# The deps in each category should be disjoint
other_deps = {"leftpadx": ["leftpad"], "SQLObject": ["sqlobject"]}
locally_installed_deps = {
    "setuptools": [
        "_distutils_hack",
        "pkg_resources",
        "setuptools",
    ],
    "pip": ["pip"],
    "isort": ["isort"],
}
user_defined_mapping = {"apache-airflow": ["airflow", "foo", "bar"]}


@st.composite
def dict_subset_strategy(draw, input_dict: Dict[str, List[str]]):
    """Returns a hypothesis strategy to choose items from a dict."""
    if not input_dict:
        return {}
    keys = draw(st.lists(st.sampled_from(list(input_dict.keys())), unique=True))
    return {k: input_dict[k] for k in keys}


@st.composite
def sample_dict_keys_and_values_strategy(draw, input_dict: Dict[str, List[str]]):
    """Returns a hypothesis strategy to choose keys and values from a dict."""
    keys = draw(st.lists(st.sampled_from(list(input_dict.keys())), unique=True))
    return {
        key: draw(st.lists(st.sampled_from(input_dict[key]), unique=True))
        for key in keys
    }


@st.composite
def user_mapping_strategy(draw, user_mapping: Dict[str, List[str]]):
    user_mapping_in_file = draw(sample_dict_keys_and_values_strategy(user_mapping))
    user_mapping_in_config = draw(sample_dict_keys_and_values_strategy(user_mapping))

    user_deps = []
    if user_mapping_in_config or user_mapping_in_file:
        drawn_deps = list(
            set.union(
                set(user_mapping_in_file.keys()),
                set(user_mapping_in_config.keys()),
            )
        )
        user_deps = draw(st.lists(st.sampled_from(drawn_deps), min_size=1, unique=True))

    return user_deps, user_mapping_in_file, user_mapping_in_config


def user_mapping_to_file_content(user_mapping: Dict[str, List[str]]) -> str:
    return "\n".join(
        [f'{dep} = ["{",".join(imports)}"]' for dep, imports in user_mapping.items()]
    )


def generate_expected_resolved_deps(
    locally_installed_deps: Dict[str, List[str]],
    other_deps: Dict[str, List[str]],
    user_defined_deps: List[str],
    user_mapping_from_config: Dict[str, List[str]],
    user_mapping_file: Optional[Path] = None,
    install_deps: bool = False,
):
    """
    Returns a dict of resolved packages.

    This function does not actually resolve its input dependencies.
    It just constructs a valid dict of resolved dependencies that respects
    the category of the dependencies in each argument.
    """
    ret = {}
    if locally_installed_deps:
        ret.update(
            {
                dep: Package(
                    dep,
                    {DependenciesMapping.LOCAL_ENV: set(imports)},
                )
                for dep, imports in locally_installed_deps.items()
            }
        )
    if user_defined_deps:
        user_mapping = UserDefinedMapping(
            set([user_mapping_file]) if user_mapping_file else None,
            user_mapping_from_config,
        )
        resolved_packages = user_mapping.lookup_packages(set(user_defined_deps))
        ret.update(resolved_packages)
    if other_deps:
        if install_deps:
            ret.update(
                {
                    dep: Package(
                        dep,
                        {DependenciesMapping.LOCAL_ENV: set(imports)},
                    )
                    for dep, imports in other_deps.items()
                }
            )
        else:
            ret.update(
                {
                    dep: Package(
                        dep,
                        {DependenciesMapping.IDENTITY: {Package.normalize_name(dep)}},
                    )
                    for dep in other_deps
                }
            )
    return ret


# Suppressing the warning on function scoped fixtures: the write_tmp_files
# fixture only runs once for all the test cases. But this is not a problem,
# as the fixture-generated content does not have to be reset between test examples.
# The test function only reads the file content and filters needed input.
@pytest.mark.usefixtures("local_pypi")
@given(
    user_mapping=user_mapping_strategy(user_defined_mapping),
    installed_deps=dict_subset_strategy(locally_installed_deps),
    other_deps=dict_subset_strategy(other_deps),
    install_deps=st.booleans(),
)
@settings(
    suppress_health_check=[HealthCheck.function_scoped_fixture],
    deadline=5000,
    max_examples=50,
)
def test_resolve_dependencies__generates_expected_mappings(
    installed_deps, other_deps, user_mapping, tmp_path, install_deps, request, mocker
):

    user_deps, user_file_mapping, user_config_mapping = user_mapping

    # The following should be true as the different categories of deps should be
    # disjoint. A change to these deps that does not respect this condition
    # will break the following assert.
    assert (
        set.intersection(
            set(installed_deps.keys()) if installed_deps else set(),
            set(user_deps) if user_deps else set(),
        )
        == set()
        and set.intersection(
            set(installed_deps.keys()) if installed_deps else set(),
            set(other_deps) if other_deps else set(),
        )
        == set()
    )

    dep_names = list(installed_deps.keys()) + user_deps + list(other_deps.keys())

    if user_file_mapping:
        custom_mapping_file = tmp_path / "mapping.toml"
        custom_mapping_file.write_text(user_mapping_to_file_content(user_file_mapping))
    else:
        custom_mapping_file = None

    expected = generate_expected_resolved_deps(
        user_defined_deps=user_deps,
        user_mapping_from_config=user_config_mapping,
        user_mapping_file=custom_mapping_file,
        locally_installed_deps=installed_deps,
        other_deps=other_deps,
        install_deps=install_deps,
    )

    # patch TemporaryPipInstallResolver so that it's called with the cache
    # option in the resolver
    tmp_pip_install_resolver = TemporaryPipInstallResolver(cache=request.config.cache)
    mocker.patch(
        "fawltydeps.packages.TemporaryPipInstallResolver",
        return_value=tmp_pip_install_resolver,
    )

    obtained = resolve_dependencies(
        dep_names,
        custom_mapping_files=set([custom_mapping_file])
        if custom_mapping_file
        else None,
        custom_mapping=user_config_mapping,
        install_deps=install_deps,
    )

    assert obtained == expected
