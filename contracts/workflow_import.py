"""Parse the OA workflow search export; its rows contain no product details."""
from io import StringIO
import re



CONTRACT_PATTERN = re.compile(
    r"(?<![A-Z0-9-])(?:ZY-F\d{2}-\d{6}-\d{2,3}|ZY-F\d{2}-\d{11}|F\d{2}-\d{6}-\d{1,3}|R\d{5})(?![A-Z0-9-])",
    re.IGNORECASE,
)
REQUIRED = ("流水号", "工作名称/文号", "发起人")


def contract_candidates(title):
    return list(dict.fromkeys(match.group().upper() for match in CONTRACT_PATTERN.finditer(title)))


def parse_workflow_xls(content):
    try:
        import xlrd
    except ImportError as exc:
        raise ValueError("服务器缺少 xlrd 依赖，请管理员重新运行更新脚本") from exc
    if len(content) > 10 * 1024 * 1024 or not content.startswith(b"\xd0\xcf\x11\xe0"):
        raise ValueError("请选择不超过 10 MB 的 OA 工作流 XLS 文件")
    try:
        book = xlrd.open_workbook(file_contents=content, ignore_workbook_corruption=True, logfile=StringIO())
    except (xlrd.XLRDError, xlrd.compdoc.CompDocError, ValueError, IndexError, AssertionError) as exc:
        raise ValueError("无法读取 XLS 文件") from exc
    for sheet in book.sheets():
        if sheet.nrows < 2 or sheet.nrows > 2001:
            continue
        headers = [str(sheet.cell_value(0, col)).strip() for col in range(sheet.ncols)]
        if not all(name in headers for name in REQUIRED):
            continue
        positions = {name: headers.index(name) for name in REQUIRED}
        rows = []
        for index in range(1, sheet.nrows):
            def cell(name):
                value = sheet.cell_value(index, positions[name])
                return str(int(value)) if isinstance(value, float) and value.is_integer() else str(value).strip()
            number = cell("流水号")
            title = cell("工作名称/文号")[:500]
            initiator = cell("发起人")[:100]
            if not any((number, title, initiator)):
                continue
            candidates = contract_candidates(title)
            rows.append({"line": index + 1, "oa_number": number, "title": title, "initiator": initiator, "contracts": candidates})
        return {"sheet": sheet.name, "rows": rows, "initiators": sorted({row["initiator"] for row in rows if row["initiator"]})}
    raise ValueError("找不到包含“流水号、工作名称/文号、发起人”的工作流数据表，或数据超过 2000 行")
