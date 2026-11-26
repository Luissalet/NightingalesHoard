"""Exercise the native agent endpoint with only temporary datasets."""
import pytest
from pydantic import BaseModel, ValidationError


def test_agent_query_exposes_only_the_requested_ingest(client, tmp_path):
    headers = {"Authorization": "Bearer " + client.services.token}

    def call(tool, **arguments):
        return client.post("/api/agent/call", headers=headers, json={
            "name": tool, "arguments": arguments, "agent": "fixture-worker", "reason": "Synthetic data"})

    def ingest(name, values):
        source = tmp_path / f"{name}.csv"
        source.write_text(f"status,value\n{values}", encoding="utf-8")
        response = call("data_ingest", kind="file", path=str(source), name=name)
        assert response.status_code == 200, response.text
        return response.json()

    own = ingest("own", "open,1\ndone,2\nopen,3\n")
    foreign = ingest("foreign_data", "held,42\n")
    client.services.transform_apply("own", "filter", {"expr": "status = 'done'"})
    sql = {"dataset_id": own["id"], "name": own["name"],
           "statement": "SELECT status, count(*) AS n FROM dataset GROUP BY status ORDER BY status"}
    response = call("data_query", sql=sql)
    assert response.status_code == 200, response.text
    assert response.json()["rows"] == [{"status": "done", "n": 1}, {"status": "open", "n": 2}]

    response = call("data_query", sql={**sql, "statement":
                    "SELECT current_setting('enable_external_access') AS external_access"})
    assert response.status_code == 200, response.text
    assert response.json()["rows"] == [{"external_access": False}]

    physical_name = f"ds_{foreign['id']}_v0"
    for relation in ("foreign_data", physical_name):
        response = call("data_query", sql={**sql, "statement": f"SELECT * FROM {relation}"})
        assert response.status_code >= 400
        assert "Query only the selected dataset" in response.text and "Consulta solo" in response.text

    response = call("data_query", sql={**sql, "dataset_id": foreign["id"]})
    assert response.status_code >= 400
    assert "ingested dataset is unavailable" in response.text and "conjunto ingerido" in response.text

    response = call("data_query", sql="SELECT status FROM foreign_data")
    assert response.status_code == 200, response.text
    assert response.json()["rows"] == [{"status": "held"}]

    # Deleting and recreating a name must not reactivate a stale receipt.
    assert call("data_ingest", action="delete", name="own").status_code == 200
    replacement = ingest("own", "new,1\n")
    assert replacement["id"] != own["id"]
    assert call("data_query", sql=sql).status_code >= 400


def test_scoped_envelope_cannot_run_against_the_old_string_schema():
    class PreviousQueryArgs(BaseModel):
        sql: str

    with pytest.raises(ValidationError):
        PreviousQueryArgs.model_validate({
            "sql": {"dataset_id": 1, "name": "fixture", "statement": "SELECT * FROM dataset"}})
