import io
import json

import pandas as pd
from openpyxl import load_workbook

from sheetforge.llm import LLMConfig, configure
from sheetforge.planner import heuristic_plan, llm_planner
from sheetforge.presentation import kpis
from sheetforge.profiler import profile_workbook
from tests.test_api_storage import FIXTURE, check_quality, client_for


def test_kpis_compute_real_prior_week_comparisons_and_do_not_invent_fields():
    frame = pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-11-02", "2026-11-09"]),
            "cost": [10, 20],
            "usage": [5, 10],
            "utilization": [0.5, 0.7],
        }
    )
    cards = kpis({"Data": frame})
    assert [item["value"] for item in cards] == [2, 30, 15, 60]
    assert cards[1]["delta"] == 100
    assert abs(cards[3]["delta"] - 40) < 1e-10
    absent = kpis({"Data": pd.DataFrame({"Region": ["North"]})})
    assert absent[1]["value"] is None
    assert absent[1]["delta"] is None


def test_csv_to_pbip_and_clean_excel_charts_are_real_outputs(tmp_path):
    _, client = client_for(tmp_path)
    response = client.post(
        "/upload?filename=spend.csv",
        content=b"Region,cost,usage,utilization,date\n=1+1,10,5,50,2026-11-02\nSouth,20,10,70,2026-11-09\n",
        headers={"Content-Type": "application/octet-stream"},
    )
    assert response.status_code == 200, response.text
    data = response.json()
    ids = {"project_id": data["project"]["id"], "run_id": data["run"]["id"]}
    for endpoint in ["/plan", "/apply"]:
        response = client.post(endpoint, json=ids)
        assert response.status_code == 200, response.text
    check_quality(client, ids)
    for endpoint in ["/model", "/generate"]:
        response = client.post(endpoint, json=ids)
        assert response.status_code == 200, response.text
    exported = client.get("/download/excel", params={"run_id": ids["run_id"]})
    assert exported.status_code == 200, exported.text
    workbook = load_workbook(io.BytesIO(exported.content))
    assert workbook.sheetnames[0] == "Summary"
    assert workbook.sheetnames[-2:] == ["Charts", "Transformation audit"]
    data = workbook.worksheets[1]
    assert data.max_row == 3
    assert data.tables and not data.auto_filter.ref  # one Excel Table, no clashing filter
    assert workbook["Charts"]._charts
    assert data["A1"].fill.fgColor.rgb.endswith("15130F")
    assert data["A2"].value == "=1+1"
    assert data["A2"].data_type == "s"
    assert data["B2"].number_format == "#,##0.00"  # cost is money
    assert "Quality fixes" in [cell.value for cell in workbook["Summary"]["A"]]
    workbook.close()
    preview = client.get("/preview", params={"run_id": ids["run_id"]}).json()
    assert preview["kpis"][1]["value"] == 30
    assert client.get("/download", params={"run_id": ids["run_id"]}).content.startswith(b"PK")
    client.close()


def test_masked_samples_hide_values_without_mutating_profile(monkeypatch):
    profile = profile_workbook(FIXTURE)
    original = json.dumps(profile.to_dict())
    captured = []
    configure(LLMConfig(provider="openai", model="mock"))

    def complete(system, messages, schema):
        captured.append(json.dumps(messages))
        return heuristic_plan(profile).model_dump()

    monkeypatch.setattr(llm_planner, "complete", complete)
    try:
        llm_planner.plan_cleaning(profile, privacy="sample")
        assert "North" in captured[0]
        llm_planner.plan_cleaning(profile, privacy="masked")
        assert "North" not in captured[-1]
        assert "South" not in captured[-1]
        assert "[masked]" in captured[-1]
        assert json.dumps(profile.to_dict()) == original
    finally:
        configure(LLMConfig())


def test_local_privacy_never_invokes_cloud_planner(monkeypatch):
    configure(LLMConfig(provider="openai", model="mock"))

    def fail(*args, **kwargs):
        raise AssertionError("Cloud completion must stay off")

    monkeypatch.setattr(llm_planner, "complete", fail)
    try:
        plan, _ = llm_planner.plan_cleaning(profile_workbook(FIXTURE), use_ai=False)
        assert plan.table_name
    finally:
        configure(LLMConfig())


def test_all_four_sample_chips_have_downloadable_workbooks(tmp_path):
    _, client = client_for(tmp_path)
    for name in ["transposed", "monthly", "multiple", "merged"]:
        response = client.get("/samples/" + name)
        assert response.status_code == 200
        assert response.content.startswith(b"PK")
    assert client.get("/samples/unknown").status_code == 404
    client.close()


def test_invalid_csv_leaves_no_project_or_working_directory(tmp_path):
    _, client = client_for(tmp_path)
    response = client.post(
        "/upload?filename=invalid.csv",
        content=b"\xff\x00\xfa",
        headers={"Content-Type": "application/octet-stream"},
    )
    assert response.status_code == 400
    assert client.get("/projects").json()["projects"] == []
    assert list((tmp_path / "projects").iterdir()) == []
    client.close()
