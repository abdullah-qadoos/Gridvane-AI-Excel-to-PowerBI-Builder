import json
from pathlib import Path

import pytest
from openpyxl import Workbook

from sheetforge.modeler import heuristic_model, validate_model
from sheetforge.modeler.visuals import VISUALS, catalogue
from sheetforge.pbip import build_semantic_project
from sheetforge.planner import heuristic_plan
from sheetforge.profiler import profile_workbook
from sheetforge.transformer import apply_plan


def sales(tmp_path):
    source = tmp_path / "sales.xlsx"
    book = Workbook()
    book.active.append(["Region", "Revenue", "Units"])
    for region, revenue, units in [("North", 10, 1), ("South", 20, 2), ("East", 30, 3)]:
        book.active.append([region, revenue, units])
    book.save(source)
    result = apply_plan(source, heuristic_plan(profile_workbook(source)))
    return result, next(iter(result.tables))


def every_visual_plan(result, table):
    plan = heuristic_model(result.tables).model_dump()
    measures = [m["name"] for m in plan["measures"]]
    visuals = []
    for index, key in enumerate(VISUALS):
        x, y = (index % 8) * 160, 64 + (index // 8) * 108
        visuals.append(
            {"kind": key, "table": table, "title": key, "measure": measures[1], "measure2": measures[0],
             "category": "Region", "x": x, "y": y, "w": 150, "h": 100, "color": "#123456"}
        )  # fmt: skip
    plan["pages"] = [
        {"name": "A", "title": "All visuals", "visuals": visuals[:24]},
        {"name": "B", "title": "More", "visuals": visuals[24:]},
    ]
    plan["theme"] = "midnight"
    return plan


def test_every_designer_visual_generates_with_power_bi_role_names(tmp_path):
    result, table = sales(tmp_path)
    plan = validate_model(every_visual_plan(result, table), result.tables)
    project, _ = build_semantic_project(result, tmp_path / "out", plan)
    pages = project.parent / "sales.Report" / "definition" / "pages"
    seen = {}
    for path in pages.rglob("visual.json"):
        content = json.loads(path.read_text(encoding="utf-8"))
        if content["name"] != "visual_000":
            seen[content["visual"]["visualType"], content["position"]["x"]] = content["visual"]
    for index, (key, (kind, _, roles, _, _)) in enumerate(VISUALS.items()):
        found = seen[kind, (index % 8) * 160]
        state = found.get("query", {}).get("queryState", {})
        expected = {role for field, role in roles.items()}
        assert set(state) == expected, key
    gauge = next(v for (k, _), v in seen.items() if k == "gauge")
    assert gauge["objects"]["dataPoint"][0]["properties"]["fill"]["solid"]["color"]["expr"]
    theme = json.loads(next(project.parent.rglob("Pivotra.json")).read_text(encoding="utf-8"))
    assert theme["dataColors"][0] == "#F28C4B"


def test_designer_rejects_missing_fields_and_off_page_boxes(tmp_path):
    result, table = sales(tmp_path)
    plan = heuristic_model(result.tables).model_dump()
    page = plan["pages"][0]["visuals"]
    page[0] = page[0] | {"kind": "donut", "category": None}
    with pytest.raises(ValueError, match="needs a Group by"):
        validate_model(plan, result.tables)
    page[0] = page[0] | {"kind": "card", "x": 1200, "w": 200}
    with pytest.raises(ValueError, match="does not fit"):
        validate_model(plan, result.tables)


def test_auto_layout_fills_positions_and_catalogue_lists_everything(tmp_path):
    result, _ = sales(tmp_path)
    plan = heuristic_model(result.tables)
    assert all(None not in (v.x, v.y, v.w, v.h) for p in plan.pages for v in p.visuals)
    data = catalogue()
    assert len(data["visuals"]) == len(VISUALS) and "midnight" in data["themes"]
    assert Path(__file__).exists()


PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360f8cf0000000301010018dd8db00000000049454e44ae426082"
)


def test_page_background_color_picture_and_transparency(tmp_path):
    result, _ = sales(tmp_path)
    images = tmp_path / "images"
    images.mkdir()
    (images / "0123456789abcdef.png").write_bytes(PNG)
    plan = heuristic_model(result.tables).model_dump()
    plan["pages"][0] |= {"background": "#102030", "image": "0123456789abcdef.png",
                         "image_fit": "Fill", "transparency": 40}  # fmt: skip
    project, _ = build_semantic_project(result, tmp_path / "out", plan, images=images)
    report = project.parent / "sales.Report"
    page = json.loads(next(report.rglob("page_001/page.json")).read_text(encoding="utf-8"))
    backdrop = page["objects"]["background"][0]["properties"]
    assert backdrop["image"]["image"]["url"]["expr"]["ResourcePackageItem"]["ItemName"].endswith(
        ".png"
    )
    assert backdrop["color"]["solid"]["color"]["expr"]["Literal"]["Value"] == "'#102030'"
    items = json.loads((report / "definition" / "report.json").read_text())["resourcePackages"][0]
    assert {
        "name": "0123456789abcdef.png",
        "path": "0123456789abcdef.png",
        "type": "Image",
    } in items["items"]
    assert (
        report / "StaticResources" / "RegisteredResources" / "0123456789abcdef.png"
    ).read_bytes() == PNG
    card = json.loads(next(report.rglob("page_001/visuals/visual_001/visual.json")).read_text())
    assert (
        card["visual"]["visualContainerObjects"]["background"][0]["properties"]["transparency"][
            "expr"
        ]["Literal"]["Value"]
        == "40D"
    )
    with pytest.raises(ValueError, match="picture"):
        build_semantic_project(result, tmp_path / "out2", plan, images=tmp_path / "missing")


def test_rule_design_colors_charts_and_keeps_pictures(tmp_path):
    from sheetforge.modeler.design import suggest_design

    result, _ = sales(tmp_path)
    plan = heuristic_model(result.tables).model_dump()
    plan["pages"][0] |= {"image": "0123456789abcdef.png", "transparency": 30}
    designed, notes = suggest_design(plan, result.tables, use_ai=False)
    charts = [v for v in designed.pages[0].visuals if v.kind not in {"slicer", "table", "donut"}]
    assert all(v.color for v in charts) and "rules" in notes[0]
    assert designed.pages[0].image == "0123456789abcdef.png"


def test_picture_upload_rejects_non_images_and_design_endpoint(tmp_path):
    from tests.test_api_storage import check_quality, client_for, upload

    _, client = client_for(tmp_path / "home")
    ids, _ = upload(client)
    bad = client.post(f"/design/image?project_id={ids['project_id']}", content=b"<svg/>")
    assert bad.status_code == 400
    good = client.post(f"/design/image?project_id={ids['project_id']}", content=PNG)
    name = good.json()["name"]
    assert client.get(f"/design/image?project_id={ids['project_id']}&name={name}").content == PNG
    assert (
        client.get(f"/design/image?project_id={ids['project_id']}&name=..%2Fx.png").status_code
        == 404
    )
    ids["run_id"] = client.post("/plan", json=ids).json()["run"]["id"]
    client.post("/apply", json=ids)
    check_quality(client, ids)
    assert client.post("/model", json=ids).status_code == 200
    designed = client.post("/design/suggest", json=ids)
    assert designed.status_code == 200, designed.text
    plan = designed.json()["model_plan"]
    plan["pages"][0]["image"] = name
    assert client.post("/model", json=ids | {"model_plan": plan}).status_code == 200
    assert client.post("/generate", json=ids).status_code == 200, "picture copied into the project"
