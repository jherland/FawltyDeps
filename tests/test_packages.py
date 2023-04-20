"""Verify behavior of package lookup and mapping to import names."""

import logging
from textwrap import dedent

import pytest

from fawltydeps.packages import (
    IdentityMapping,
    LocalPackageResolver,
    Package,
    UserDefinedMapping,
    resolve_dependencies,
)
from fawltydeps.types import UnparseablePathException

from .utils import (
    SAMPLE_PROJECTS_DIR,
    default_sys_path_env_for_tests,
    expand_package_mappings_placeholders,
    test_vectors,
)


def test_package__empty_package__matches_nothing():
    p = Package("foobar")  # no mappings
    assert p.package_name == "foobar"
    assert not p.is_used(["foobar"])


@pytest.mark.parametrize(
    "package_name,matching_imports,non_matching_imports",
    [
        pytest.param(
            "foobar",
            ["foobar", "and", "other", "names"],
            ["only", "other", "names", "foo_bar", "Foobar", "FooBar", "FOOBAR"],
            id="simple_lowercase_name__matches_itself_only",
        ),
        pytest.param(
            "FooBar",
            ["foobar", "and", "other", "names"],
            ["only", "other", "names", "foo_bar", "Foobar", "FooBar", "FOOBAR"],
            id="mixed_case_name__matches_lowercase_only",
        ),
        pytest.param(
            "typing-extensions",
            ["typing_extensions", "and", "other", "names"],
            ["typing-extensions", "typingextensions"],
            id="name_with_hyphen__matches_name_with_underscore_only",
        ),
        pytest.param(
            "Foo-Bar",
            ["foo_bar", "and", "other", "names"],
            ["foo-bar", "Foobar", "FooBar", "FOOBAR"],
            id="weird_name__matches_normalized_name_only",
        ),
    ],
)
def test_package__identity_mapping(
    package_name, matching_imports, non_matching_imports
):
    id_mapping = IdentityMapping()
    p = id_mapping.lookup_package(package_name)
    assert p.package_name == package_name  # package name is not normalized
    assert p.is_used(matching_imports)
    assert not p.is_used(non_matching_imports)


@pytest.mark.parametrize(
    "package_name,import_names,matching_imports,non_matching_imports",
    [
        pytest.param(
            "foobar",
            ["foobar"],
            ["foobar", "and", "other", "names"],
            ["only", "other", "names", "foo_bar", "Foobar", "FooBar", "FOOBAR"],
            id="simple_name_mapped_to_itself__matches_itself_only",
        ),
        pytest.param(
            "FooBar",
            ["FooBar"],
            ["FooBar", "and", "other", "names"],
            ["only", "other", "names", "foo_bar", "foobar", "FOOBAR"],
            id="mixed_case_name_mapped_to_itself__matches_exact_spelling_only",
        ),
        pytest.param(
            "typing-extensions",
            ["typing_extensions"],
            ["typing_extensions", "and", "other", "names"],
            ["typing-extensions", "typingextensions"],
            id="hyphen_name_mapped_to_underscore_name__matches_only_underscore_name",
        ),
        pytest.param(
            "Foo-Bar",
            ["blorp"],
            ["blorp", "and", "other", "names"],
            ["Foo-Bar", "foo-bar", "foobar", "FooBar", "FOOBAR", "Blorp", "BLORP"],
            id="weird_name_mapped_diff_name__matches_diff_name_only",
        ),
        pytest.param(
            "foobar",
            ["foo", "bar", "baz"],
            ["foo", "and", "other", "names"],
            ["foobar", "and", "other", "names"],
            id="name_with_three_imports__matches_first_import",
        ),
        pytest.param(
            "foobar",
            ["foo", "bar", "baz"],
            ["bar", "and", "other", "names"],
            ["foobar", "and", "other", "names"],
            id="name_with_three_imports__matches_second_import",
        ),
        pytest.param(
            "foobar",
            ["foo", "bar", "baz"],
            ["baz", "and", "other", "names"],
            ["foobar", "and", "other", "names"],
            id="name_with_three_imports__matches_third_import",
        ),
    ],
)
def test_package__local_env_mapping(
    package_name, import_names, matching_imports, non_matching_imports
):
    p = Package(package_name)
    p.add_import_names(*import_names, description="Python env at /some/path")
    assert p.package_name == package_name  # package name is not normalized
    assert p.is_used(matching_imports)
    assert not p.is_used(non_matching_imports)


def test_package__both_mappings():
    id_mapping = IdentityMapping()
    p = id_mapping.lookup_package("FooBar")
    import_names = ["foo", "bar", "baz"]
    p.add_import_names(*import_names, description="Python env at /some/path")
    assert p.package_name == "FooBar"  # package name is not normalized
    assert p.is_used(["foobar"])  # but identity-mapped import name _is_.
    assert p.is_used(["foo"])
    assert p.is_used(["bar"])
    assert p.is_used(["baz"])
    assert not p.is_used(["fooba"])
    assert not p.is_used(["foobarbaz"])
    assert p.mappings == {
        "Identity mapping": {"foobar"},
        "Python env at /some/path": {"foo", "bar", "baz"},
    }
    assert p.import_names == {"foobar", "foo", "bar", "baz"}


@pytest.mark.parametrize(
    "mapping_files_content,custom_mapping,expect",
    [
        pytest.param(
            [
                """\
                apache-airflow = ["airflow"]
                attrs = ["attr", "attrs"]
            """
            ],
            None,
            {"apache_airflow": ["airflow"], "attrs": ["attr", "attrs"]},
            id="well_formated_input_file__parses_correctly",
        ),
        pytest.param(
            [
                """\
                apache-airflow = ["airflow"]
                attrs = ["attr", "attrs"]
                """,
                """\
                apache-airflow = ["baz"]
                foo = ["bar"]
                """,
            ],
            None,
            {
                "apache_airflow": ["airflow", "baz"],
                "attrs": ["attr", "attrs"],
                "foo": ["bar"],
            },
            id="well_formated_input_2files__parses_correctly",
        ),
        pytest.param(
            [
                """\
                apache-airflow = ["airflow"]
                attrs = ["attr", "attrs"]
                """,
                """\
                apache-airflow = ["baz"]
                foo = ["bar"]
                """,
            ],
            {"apache-airflow": ["unicorn"]},
            {
                "apache_airflow": ["airflow", "baz", "unicorn"],
                "attrs": ["attr", "attrs"],
                "foo": ["bar"],
            },
            id="well_formated_input_2files_and_config__parses_correctly",
        ),
    ],
)
def test_user_defined_mapping__well_formated_input_file__parses_correctly(
    mapping_files_content,
    custom_mapping,
    expect,
    tmp_path,
):
    custom_mapping_files = set()
    for i, mapping in enumerate(mapping_files_content):
        custom_mapping_file = tmp_path / f"mapping{i}.toml"
        custom_mapping_file.write_text(dedent(mapping))
        custom_mapping_files.add(custom_mapping_file)

    udm = UserDefinedMapping(
        mapping_paths=custom_mapping_files, custom_mapping=custom_mapping
    )
    mapped_packages = {k: sorted(list(v.import_names)) for k, v in udm.packages.items()}
    assert mapped_packages == expect


def test_user_defined_mapping__input_is_no_file__raises_unparsable_path_exeption():
    with pytest.raises(UnparseablePathException):
        UserDefinedMapping({SAMPLE_PROJECTS_DIR})


def test_user_defined_mapping__no_input__returns_empty_mapping():
    udm = UserDefinedMapping()
    assert len(udm.packages) == 0


@pytest.mark.parametrize(
    "dep_name,expect_import_names",
    [
        pytest.param(
            "NOT_A_PACKAGE",
            None,
            id="missing_package__returns_None",
        ),
        pytest.param(
            "isort",
            {"isort"},
            id="package_exposes_nothing__can_still_infer_import_name",
        ),
        pytest.param(
            "pip",
            {"pip"},
            id="package_exposes_one_entry__returns_entry",
        ),
        pytest.param(
            "setuptools",
            {"_distutils_hack", "pkg_resources", "setuptools"},
            id="package_exposes_many_entries__returns_all_entries",
        ),
        pytest.param(
            "SETUPTOOLS",
            {"_distutils_hack", "pkg_resources", "setuptools"},
            id="package_declared_in_capital_letters__is_successfully_mapped_with_d2i",
        ),
        pytest.param(
            "typing-extensions",
            {"typing_extensions"},
            id="package_with_hyphen__provides_import_name_with_underscore",
        ),
    ],
)
def test_LocalPackageResolver_lookup_packages(
    isolate_default_resolver, dep_name, expect_import_names
):
    isolate_default_resolver(default_sys_path_env_for_tests)
    lpl = LocalPackageResolver()
    actual = lpl.lookup_packages({dep_name})
    if expect_import_names is None:
        assert actual == {}
    else:
        assert len(actual) == 1
        assert actual[dep_name].import_names == expect_import_names


@pytest.mark.parametrize(
    "dep_names,user_mapping,expected",
    [
        pytest.param([], None, {}, id="no_deps__empty_dict"),
        pytest.param(
            ["pandas", "numpy", "other"],
            None,
            {
                "pandas": Package("pandas", {"Identity mapping": {"pandas"}}),
                "numpy": Package("numpy", {"Identity mapping": {"numpy"}}),
                "other": Package("other", {"Identity mapping": {"other"}}),
            },
            id="uninstalled_deps__use_identity_mapping",
        ),
        pytest.param(
            ["setuptools", "pip", "isort"],
            None,
            {
                "setuptools": Package(
                    "setuptools",
                    {
                        "Python env at {site_packages}": {
                            "_distutils_hack",
                            "pkg_resources",
                            "setuptools",
                        }
                    },
                ),
                "pip": Package("pip", {"Python env at {site_packages}": {"pip"}}),
                "isort": Package("isort", {"Python env at {site_packages}": {"isort"}}),
            },
            id="installed_deps__use_local_env_mapping",
        ),
        pytest.param(
            ["pandas", "pip"],
            None,
            {
                "pip": Package("pip", {"Python env at {site_packages}": {"pip"}}),
                "pandas": Package("pandas", {"Identity mapping": {"pandas"}}),
            },
            id="mixed_deps__uses_mixture_of_identity_and_local_env_mapping",
        ),
        pytest.param(
            ["pandas", "pip", "apache_airflow"],
            {
                "file": """\
                apache-airflow = ["airflow"]
                """
            },
            {
                "apache_airflow": Package(
                    "apache_airflow", {"User-defined mapping": {"airflow"}}
                ),
                "pip": Package("pip", {"Python env at {site_packages}": {"pip"}}),
                "pandas": Package("pandas", {"Identity mapping": {"pandas"}}),
            },
            id="mixed_deps__uses_mixture_of_user_defined_from_file_identity_and_local_env_mapping",
        ),
        pytest.param(
            ["pandas", "pip"],
            {"file": dedent("""apache-airflow = ["airflow"]""")},
            {
                "pip": Package("pip", {"Python env at {site_packages}": {"pip"}}),
                "pandas": Package("pandas", {"Identity mapping": {"pandas"}}),
            },
            id="mixed_deps__unaffected_by_nonmatching_user_defined_mapping",
        ),
        pytest.param(
            ["pandas", "pip", "apache_airflow"],
            {"configuration": {"apache-airflow": ["airflow"]}},
            {
                "apache_airflow": Package(
                    "apache_airflow", {"User-defined mapping": {"airflow"}}
                ),
                "pip": Package("pip", {"Python env at {site_packages}": {"pip"}}),
                "pandas": Package("pandas", {"Identity mapping": {"pandas"}}),
            },
            id="mixed_deps__user_defined_from_config_identity_and_local_env_mapping",
        ),
        pytest.param(
            ["pandas", "pip", "apache_airflow"],
            {
                "file": """apache-airflow = ["airflow"]""",
                "configuration": {"apache-airflow": ["foo", "bar"]},
            },
            {
                "apache_airflow": Package(
                    "apache_airflow",
                    {"User-defined mapping": {"airflow", "foo", "bar"}},
                ),
                "pip": Package("pip", {"Python env at {site_packages}": {"pip"}}),
                "pandas": Package("pandas", {"Identity mapping": {"pandas"}}),
            },
            id="mixed_deps__uses_mixture_of_user_defined_identity_and_local_env_mapping",
        ),
    ],
)
def test_resolve_dependencies__focus_on_mappings(
    dep_names, user_mapping, expected, tmp_path, isolate_default_resolver
):
    custom_mapping_files = set()
    custom_mapping = None
    if user_mapping is not None:
        custom_mapping = user_mapping.get("configuration")
        if "file" in user_mapping:
            custom_mapping_file = tmp_path / "mapping.toml"
            custom_mapping_file.write_text(dedent(user_mapping["file"]))
            custom_mapping_files = {custom_mapping_file}

    site_packages = isolate_default_resolver(default_sys_path_env_for_tests)
    expected = expand_package_mappings_placeholders(
        expected,
        site_packages=site_packages,
    )

    assert (
        resolve_dependencies(
            dep_names,
            custom_mapping_files=custom_mapping_files,
            custom_mapping=custom_mapping,
        )
        == expected
    )


@pytest.mark.parametrize("vector", [pytest.param(v, id=v.id) for v in test_vectors])
def test_resolve_dependencies(vector, isolate_default_resolver):
    dep_names = [dd.name for dd in vector.declared_deps]
    site_packages = isolate_default_resolver(default_sys_path_env_for_tests)
    expected = expand_package_mappings_placeholders(
        vector.expect_resolved_deps,
        site_packages=site_packages,
    )
    assert resolve_dependencies(dep_names) == expected


def test_resolve_dependencies__informs_once_when_id_mapping_is_used(
    caplog, isolate_default_resolver
):
    dep_names = ["some-foo", "pip", "some-foo"]
    site_packages = isolate_default_resolver(default_sys_path_env_for_tests)
    expect = expand_package_mappings_placeholders(
        {
            "pip": Package("pip", {"Python env at {site_packages}": {"pip"}}),
            "some-foo": Package("some-foo", {"Identity mapping": {"some_foo"}}),
        },
        site_packages=site_packages,
    )
    expect_log = [
        (
            "fawltydeps.packages",
            logging.INFO,
            "'some-foo' was not resolved. Assuming it can be imported as 'some_foo'.",
        )
    ]
    caplog.set_level(logging.INFO)
    assert resolve_dependencies(dep_names) == expect
    assert caplog.record_tuples == expect_log
