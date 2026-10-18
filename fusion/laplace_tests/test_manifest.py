from pathlib import Path

from faustus_manifest import check_repo

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_manifest_is_valid_and_referenced_files_exist():
    data = check_repo(REPO_ROOT)
    assert data["id"] == "laplace"
    assert data["mcp"]["transport"] == "stdio"
    assert data["x-family"]["copilot"]["contract_version"] == "1.0"
    assert data["x-family"]["copilot"]["object_types"] == ["cell", "computation", "dataset"]
