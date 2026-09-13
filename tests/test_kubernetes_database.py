"""The rehearsal may deploy only its three database resources."""

import json
from copy import deepcopy

import pytest

from scripts.rehearse_kubernetes_db import database_objects, decode_objects


def test_database_rehearsal_scope():
    objects: list[dict] = [
        {"kind": kind, "metadata": {"name": name}}
        for kind, name in (("Service", "db"), ("StatefulSet", "db"), ("ConfigMap", "db-init"))
    ]
    assert all(
        o["metadata"]["namespace"] == "eps-db-check-test"
        for o in database_objects(objects, "eps-db-check-test")
    )
    bad = deepcopy(objects)
    bad[0]["metadata"]["namespace"] = "eps"
    with pytest.raises(ValueError, match="escaped"):
        database_objects(bad, "eps-db-check-test")
    with pytest.raises(ValueError, match="only"):
        database_objects(
            objects + [{"kind": "Deployment", "metadata": {"name": "web"}}], "eps-db-check-test"
        )


def test_kubectl_json_documents():
    objects = [{"kind": "Service"}, {"kind": "StatefulSet"}]
    stream = "\n".join(json.dumps(obj) for obj in objects).encode()
    assert decode_objects(stream) == objects
    assert decode_objects(json.dumps({"kind": "List", "items": objects}).encode()) == objects
    with pytest.raises(ValueError):
        decode_objects(stream + b"not-json")
