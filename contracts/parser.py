"""Treat OA exports strictly as untrusted data; never render their HTML."""
import hashlib
import re
from io import BytesIO
from zipfile import BadZipFile, ZipFile
from bs4 import BeautifulSoup


def extract_oa_html(content: bytes, filename: str) -> bytes:
    if filename.lower().endswith((".html", ".htm")):
        return content
    if not filename.lower().endswith(".zip"):
        raise ValueError("请选择 OA HTML 或 ZIP 文件")
    try:
        with ZipFile(BytesIO(content)) as archive:
            members = archive.infolist()
            if len(members) > 500 or sum(member.file_size for member in members) > 50 * 1024 * 1024:
                raise ValueError("ZIP 文件内容过大")
            candidates = [member for member in members if not member.is_dir() and member.filename.lower().endswith((".html", ".htm")) and "files" not in member.filename.replace("\\", "/").lower().split("/")[:-1]]
            if len(candidates) != 1:
                raise ValueError(f"ZIP 中应包含 1 份 OA HTML，当前找到 {len(candidates)} 份")
            if candidates[0].file_size > 5 * 1024 * 1024:
                raise ValueError("OA HTML 超过 5 MB")
            with archive.open(candidates[0]) as source:
                return source.read(5 * 1024 * 1024 + 1)
    except (BadZipFile, RuntimeError) as exc:
        raise ValueError("无法读取 ZIP 文件") from exc


def parse_oa(content: bytes):
    if len(content) > 5 * 1024 * 1024:
        raise ValueError("OA 文件超过 5 MB")
    if b"<" not in content[:1024]:
        raise ValueError("文件不是 HTML")
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        text = content.decode("gb18030")
    soup = BeautifulSoup(text, "html.parser")
    fields = {}
    for tag in soup.find_all("input", attrs={"title": True}):
        title = tag.get("title", "")
        value = tag.get("value", "").strip()
        if value:
            fields[title] = value
    visible = soup.get_text(" ", strip=True)
    match = re.search(r"流水号\s*[:：]\s*(\d+)", visible)
    oa_number = match.group(1) if match else None
    def find(*names):
        for name in names:
            for title, value in fields.items():
                if title == name or title.endswith("." + name):
                    return value
        return ""
    contract_number = find("日立信合同号", "合同号")
    if not contract_number:
        raise ValueError("没有找到日立信合同号或合同号，请确认 OA 导出格式")
    signed_on = None
    for table in soup.find_all("table"):
        if "流程开始" not in table.get_text(" ", strip=True)[:100]:
            continue
        for row in table.find_all("tr"):
            row_text = row.get_text(" ", strip=True)
            if re.search(r"第\s*1\s*步", row_text) and "发起人" in row_text:
                started = re.search(r"开始于\s*[:：]\s*(\d{4}-\d{2}-\d{2})\s+\d{2}:\d{2}:\d{2}", row_text)
                if started:
                    signed_on = started.group(1)
                    break
        if signed_on:
            break
    items = []
    table = soup.find("table", id="LV_33")
    if table:
        for row in table.find_all("tr")[1:]:
            cells = [c.get_text(" ", strip=True) for c in row.find_all("td", recursive=False)]
            if len(cells) >= 9:
                items.append({"position": len(items) + 1, "product_name": cells[1], "model": cells[2], "quantity": cells[3] or None, "unit": cells[4], "sale_price": cells[5] or None, "line_amount": cells[6] or None, "currency": cells[7], "notes": cells[8]})
    materials = []
    supply_table = soup.find("table", id="LV_28")
    if supply_table:
        for row in supply_table.find_all("tr")[1:]:
            cells = [c.get_text(" ", strip=True) for c in row.find_all("td", recursive=False)]
            if len(cells) >= 6 and any(cells[1:4]):
                materials.append({"position": len(materials) + 1, "code": cells[1], "name": cells[2], "specification": cells[3], "quantity": cells[4] or None, "unit": cells[5], "technical_parameters": cells[6] if len(cells) > 6 else "", "supply_requirement": cells[7] if len(cells) > 7 else "", "stock_status": cells[8] if len(cells) > 8 else "", "production_order": cells[9] if len(cells) > 9 else ""})
    outbound_items = []
    outbound_table = soup.find("table", id="LV_86")
    if outbound_table:
        for row in outbound_table.find_all("tr")[1:]:
            cells = [c.get_text(" ", strip=True) for c in row.find_all("td", recursive=False)]
            if len(cells) >= 8 and any(cells[1:6]):
                outbound_items.append({"position": len(outbound_items) + 1, "document_number": cells[1], "flow_number": cells[2], "material_code": cells[3], "material_name": cells[4], "specification": cells[5], "unit": cells[6], "quantity": cells[7] or None, "notes": cells[8] if len(cells) > 8 else ""})
    return {"oa_number": oa_number, "contract_number": contract_number, "customer_contract_number": find("甲方合同号"), "oa_signer": find("合同签订人") or find("发起人"), "customer_name": find("签订单位", "合同执行任务单智仪V1.0.签订单位"), "oa_product_type": find("产品类型"), "signed_on": signed_on, "items": items, "materials": materials, "outbound_items": outbound_items, "sha256": hashlib.sha256(content).hexdigest()}
