import re
from zipfile import ZipFile

from openpyxl import Workbook, load_workbook

from sheetforge.formulas import apply_fixes, scan


def with_cached_error(path, cell, error):
    """openpyxl cannot store a cached result, so write the error Excel would have saved."""
    with ZipFile(path) as source:
        files = {name: source.read(name) for name in source.namelist()}
    sheet = "xl/worksheets/sheet1.xml"
    xml = files[sheet].decode()
    xml = re.sub(rf'<c r="{cell}"([^>]*)><f>([^<]*)</f><v\s*/?>(?:</v>)?</c>',
                 rf'<c r="{cell}"\1 t="e"><f>\2</f><v>{error}</v></c>', xml)  # fmt: skip
    files[sheet] = xml.encode()
    with ZipFile(path, "w") as target:
        for name, content in files.items():
            target.writestr(name, content)


def workbook(tmp_path):
    path = tmp_path / "budget.xlsx"
    book = Workbook()
    sheet = book.active
    sheet.append(["Item", "Price", "Qty", "Amount", "Tax", "Per unit"])
    for item, price, qty in [("A", 2, 3), ("B", 4, 5), ("C", 10, 2), ("D", 1.5, 4)]:
        row = sheet.max_row + 1
        sheet.append([item, price, qty, price * qty, f"=D{row}*0.17", f"=D{row}/C{row}"])
    sheet.append(["Total", None, 14, 52, "=ROUND(SUM(E2:E5),2)", "=D6/#REF!"])
    book.save(path)
    with_cached_error(path, "F6", "#DIV/0!")
    return path


def test_scan_finds_each_kind_and_fix_writes_formulas(tmp_path):
    path = workbook(tmp_path)
    result = scan(path)
    kinds = {fix.kind for fix in result.fixes}
    assert kinds == {"typed_total", "broken", "hardcoded"}
    total = next(f for f in result.fixes if f.title.startswith("Row 6"))
    assert {c.cell: c.new for c in total.changes} == {"C6": "=SUM(C2:C5)", "D6": "=SUM(D2:D5)"}
    product = next(f for f in result.fixes if "always" in f.title)
    assert product.changes[0].new == "=B2*C2" and len(product.changes) == 4
    ref = next(f for f in result.fixes if "#REF!" in f.title)
    assert ref.ready and ref.changes[0].new == "=D6/C6"  # rebuilt from the formula above
    rate = next(f for f in result.fixes if f.kind == "hardcoded")
    assert rate.value == 0.17 and len(rate.changes) == 4  # ROUND's 2 digits are not an input
    target = tmp_path / "fixed.xlsx"
    apply_fixes(path, result, {f.id: None for f in result.fixes} | {rate.id: "Tax_Rate"}, target)
    fixed = load_workbook(target)
    sheet = fixed["Sheet"]
    assert sheet["D6"].value == "=SUM(D2:D5)" and sheet["D3"].value == "=B3*C3"
    assert sheet["E2"].value == "=D2*Tax_Rate" and sheet["F6"].value == "=D6/C6"
    assert fixed["Inputs"]["B2"].value == 0.17
    assert fixed.defined_names["Tax_Rate"].attr_text == "'Inputs'!$B$2"
    assert load_workbook(path)["Sheet"]["D6"].value == 52  # the original is untouched


def test_division_error_is_wrapped_and_percent_constants_fold(tmp_path):
    path = tmp_path / "ratio.xlsx"
    book = Workbook()
    book.active.append(["A", "B", "Ratio", "Bonus"])
    book.active.append([1, 0, "=A2/B2", "=A2*15%"])
    book.save(path)
    with_cached_error(path, "C2", "#DIV/0!")
    result = scan(path)
    broken = next(f for f in result.fixes if f.kind == "broken")
    assert broken.changes[0].new == "=IFERROR(A2/B2,0)"
    bonus = next(f for f in result.fixes if f.kind == "hardcoded")
    assert bonus.value == 0.15 and bonus.changes[0].new == f"=A2*{bonus.name}"


def test_formula_endpoints_scan_apply_and_download(tmp_path):
    from tests.test_api_storage import client_for, upload

    _, client = client_for(tmp_path / "home")
    ids, _ = upload(client, workbook(tmp_path))
    scanned = client.post("/formulas/scan", json={"project_id": ids["project_id"]})
    assert scanned.status_code == 200, scanned.text
    fixes = scanned.json()["fixes"]
    applied = client.post(
        "/formulas/apply",
        json={"project_id": ids["project_id"], "accepted": {f["id"]: None for f in fixes}},
    )
    assert applied.status_code == 200, applied.text
    download = client.get(applied.json()["download"])
    assert download.status_code == 200
    assert "workbook-formulas-fixed.xlsx" in download.headers["content-disposition"]
    assert client.get("/visuals").json()["visuals"]
