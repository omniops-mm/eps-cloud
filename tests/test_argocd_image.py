"""Regression checks for the Argo runtime and inventory trust boundary."""

import copy

import pytest

from scripts.verify_argocd_image import check_files, derived_inventory


def inventory_example():
    pins = {
        "platform_digest": "a" * 64,
        "packages": {
            "libexample": {
                "version": "2",
                "path": "usr/lib/example.so",
                "sha256": "new",
            }
        },
    }
    reference = "pkg:deb/debian/libexample@1?arch=amd64"
    statement = {
        "predicateType": "https://cyclonedx.org/bom/v1.6",
        "subject": [{"digest": {"sha256": "a" * 64}}],
        "predicate": {
            "components": [
                {
                    "name": "libexample",
                    "version": "1",
                    "purl": reference,
                    "bom-ref": reference,
                },
                {
                    "name": "@types/example",
                    "version": "1",
                    "purl": "pkg:npm/%40types/example@1",
                },
                {"name": "unchanged", "version": "7"},
            ],
            "dependencies": [{"ref": "root", "dependsOn": [reference]}],
        },
    }
    return pins, statement


def test_inventory_preserves_components_and_updates_dependency_references():
    pins, statement = inventory_example()
    original = copy.deepcopy(statement)
    result = derived_inventory(statement, pins)
    assert statement == original
    assert len(result["components"]) == 3
    assert result["components"][2] == original["predicate"]["components"][2]
    patched, scoped = result["components"][:2]
    assert patched["version"] == "2"
    assert patched["purl"] == patched["bom-ref"] == "pkg:deb/debian/libexample@2?arch=amd64"
    assert result["dependencies"][0]["dependsOn"] == [patched["purl"]]
    assert scoped["group"] == "@types" and scoped["name"] == "example"


@pytest.mark.parametrize("change", ["subject", "missing", "duplicate", "npm_conflict"])
def test_inventory_rejects_invalid_provenance_or_identity(change):
    pins, statement = inventory_example()
    components = statement["predicate"]["components"]
    if change == "subject":
        statement["subject"][0]["digest"]["sha256"] = "b" * 64
    elif change == "missing":
        del components[0]
    elif change == "duplicate":
        components.append(copy.deepcopy(components[0]))
    else:
        components[1]["version"] = "99"
    with pytest.raises(ValueError):
        derived_inventory(statement, pins)


@pytest.mark.parametrize("change", ["none", "binary", "metadata", "extra", "wrong_patch"])
def test_runtime_changes_are_limited_to_reviewed_library_bytes(change):
    pins, _ = inventory_example()
    original = {
        "usr/lib/example.so": ("0", 0, 0, 420, "", "old"),
        "usr/bin/argocd": ("0", 0, 0, 493, "", "original"),
    }
    candidate = dict(original)
    candidate["usr/lib/example.so"] = ("0", 0, 0, 420, "", "new")
    candidate["var/lib/dpkg/status"] = ("0", 0, 0, 420, "", "updated")
    if change == "binary":
        candidate["usr/bin/argocd"] = ("0", 0, 0, 493, "", "tampered")
    elif change == "metadata":
        candidate["usr/lib/example.so"] = ("0", 999, 0, 420, "", "new")
    elif change == "extra":
        candidate["usr/bin/unexpected"] = ("0", 0, 0, 493, "", "extra")
    elif change == "wrong_patch":
        candidate["usr/lib/example.so"] = ("0", 0, 0, 420, "", "unreviewed")
    if change == "none":
        check_files(original, candidate, pins["packages"])
    else:
        with pytest.raises(ValueError):
            check_files(original, candidate, pins["packages"])
