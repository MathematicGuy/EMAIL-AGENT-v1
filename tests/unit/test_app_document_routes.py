from cowork_agent.app import create_app


def test_project_document_routes_are_unique_in_openapi() -> None:
    schema = create_app().openapi()
    paths = schema["paths"]
    assert set(paths["/v1/cowork/chat/projects"].keys()) == {"get", "post"}
    assert set(paths["/v1/cowork/chat/projects/{project_id}/documents"].keys()) == {
        "get",
        "post",
    }
    operation_ids = [
        operation["operationId"]
        for path in paths.values()
        for method, operation in path.items()
        if method in {"get", "post", "put", "delete", "patch"}
    ]
    assert len(operation_ids) == len(set(operation_ids))
