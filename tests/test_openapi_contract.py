from yowayowa.api.app import app


def test_fred_series_schema_keeps_rich_date_filtered_contract() -> None:
    schema = app.openapi()
    operation = schema["paths"]["/v1/macro/fred/{series_id}"]["get"]
    parameter_names = {parameter["name"] for parameter in operation["parameters"]}

    assert {"observation_start", "observation_end"} <= parameter_names
    assert {"units", "frequency", "aggregation_method", "limit"} <= parameter_names


def test_openapi_operation_ids_are_unique() -> None:
    schema = app.openapi()
    operation_ids: list[str] = []
    for path_item in schema["paths"].values():
        for operation in path_item.values():
            if isinstance(operation, dict) and "operationId" in operation:
                operation_ids.append(operation["operationId"])

    assert len(operation_ids) == len(set(operation_ids))



def test_agent_facing_openapi_contract_remains_machine_discoverable() -> None:
    schema = app.openapi()
    paths = schema["paths"]

    required_paths = {
        "/v1/ai/chat",
        "/v1/ai/status",
        "/v1/ai/prompt-packet",
        "/v1/ai/codex/status",
        "/v1/ai/codex/device-auth",
        "/v1/ai/codex/session-status",
        "/v1/strategy-research/snapshots",
        "/v1/strategy-research/outcomes",
        "/v1/strategy-presets/{strategy_id}/evaluate",
        "/v1/operations/plan",
    }
    assert required_paths <= set(paths)
    assert paths["/v1/ai/chat"]["post"]["operationId"] == "ai_chat"
