"""Read historical contract workbooks without executing formulas or macros."""
from collections import OrderedDict, defaultdict
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from io import BytesIO
import re
from zipfile import ZipFile
from openpyxl import load_workbook


def clean(value):
    return str(value).strip() if value is not None else ""


def decimal(value):
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value).replace(",", ""))
    except InvalidOperation:
        raise ValueError(f"无法识别金额或数量：{value}")


def day(value):
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if not value or clean(value) in ("?", "？"):
        return None
    try:
        date_text = str(value).strip().split()[0].rstrip(".")
        parts = re.fullmatch(r"(\d{4})[./-](\d{1,2})[./-](\d{1,2})", date_text)
        if parts:
            return date(*(int(part) for part in parts.groups())).isoformat()
        return date.fromisoformat(date_text[:10]).isoformat()
    except ValueError:
        raise ValueError(f"无法识别日期：{value}")


def currency(value):
    return {"R": "人民币", "M": "美元", "U": "欧元", "人民币": "人民币", "美元": "美元", "欧元": "欧元"}.get(clean(value).upper(), clean(value))


def boolean(value):
    if value is None or value == "":
        return None
    if clean(value) in ("是", "已开", "已开票", "1", "True"):
        return True
    if clean(value) in ("否", "未开", "未开票", "0", "False"):
        return False
    return None


def flow_numbers(value):
    if value is None:
        return []
    if isinstance(value, (int, float)) and float(value).is_integer():
        return [str(int(value))]
    return [part for part in re.split(r"[,，、;；\s]+", clean(value)) if part]


def parse_workbook(content):
    if len(content) > 10 * 1024 * 1024:
        raise ValueError("Excel 文件超过 10 MB")
    try:
        with ZipFile(BytesIO(content)) as archive:
            if sum(entry.file_size for entry in archive.infolist()) > 100 * 1024 * 1024:
                raise ValueError("Excel 解压后超过 100 MB")
        book = load_workbook(BytesIO(content), read_only=True, data_only=True, keep_links=False)
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("无法读取 xlsx 文件") from exc
    try:
        sheet = None
        header_row = None
        header_index = None
        inferred_header = False
        replaced_header_value = ""
        for candidate in book:
            for row_number, row in enumerate(candidate.iter_rows(min_row=1, max_row=min(candidate.max_row, 5), max_col=80, values_only=True), 1):
                labels = [clean(v) for v in row]
                if "合同编号" in labels and "OA流水号" in labels:
                    sheet, header_row, header_index = candidate, row, row_number
                    break
                # Some legacy sheets accidentally replace E2 (合同编号) with a
                # contract number. Infer it only when the surrounding template
                # headers still match their fixed positions.
                if (len(labels) >= 19 and labels[1] == "OA流水号"
                        and labels[2] == "合同签订日期"
                        and labels[3] == "业务类型"
                        and labels[6] == "客户公司名称"
                        and labels[18] == "合同金额(原币)"
                        and "合同编号" not in labels):
                    replaced_header_value = labels[4]
                    labels[4] = "合同编号"
                    sheet, header_row, header_index = candidate, labels, row_number
                    inferred_header = True
                    break
            if sheet:
                break
        if sheet is None:
            raise ValueError("找不到包含‘合同编号’和‘OA流水号’的合同数据表")
        labels = [clean(v) for v in header_row]
        def value(row, *names):
            for name in names:
                if name in labels:
                    index = labels.index(name)
                    return row[index] if index < len(row) else None
            return None
        contracts = OrderedDict()
        warnings = []
        if inferred_header:
            warnings.append(f"第 5 列表头被‘{replaced_header_value}’覆盖，已根据相邻固定表头识别为合同编号列；该表头单元格不会作为合同导入，请核对源表与预览数据")
        names = set()
        row_count = 0
        for row_number, row in enumerate(sheet.iter_rows(min_row=header_index + 1, max_col=80, values_only=True), header_index + 1):
            number = clean(value(row, "合同编号"))
            if not number:
                continue
            row_count += 1
            name = clean(value(row, "业务员"))
            if name:
                names.add(name)
            oa_numbers = flow_numbers(value(row, "OA流水号"))
            oa = oa_numbers[0] if oa_numbers else ""
            line_currency = currency(value(row, "人民币/美元/欧元（R/M/U）"))
            if number not in contracts:
                contracts[number] = {"number": number, "ambiguous": "," in number or "\n" in number, "oa": oa, "oa_numbers": [], "signed_on": day(value(row, "合同签订日期")), "business_type": clean(value(row, "业务类型")), "salesperson_name": name, "customer_name": clean(value(row, "客户公司名称")), "customer_category": clean(value(row, "客户类别")), "inquiry_source": clean(value(row, "询盘来源")), "country": clean(value(row, "国家/地区", "地区")), "industry": clean(value(row, "行业")), "currency": line_currency, "freight": Decimal(0), "freight_present": False, "amount": Decimal(0), "amount_present": False, "invoice_on": day(value(row, "开票日期")), "invoice_type": clean(value(row, "发票类型")), "purchase_invoiced": boolean(value(row, "购入是否开票")), "sales_invoiced": boolean(value(row, "销售是否开票")), "agreed_ship_on": day(value(row, "合订签订发货日期")), "payment_method": clean(value(row, "收款方式")), "exchange_rate": decimal(value(row, "结算汇率")), "progress": clean(value(row, "执行进度")), "progress_detail": clean(value(row, "执行详情")), "customs_declared": boolean(value(row, "是否报关")), "email": clean(value(row, "邮箱")), "notes": clean(value(row, "备注")), "items": [], "shipments": {}, "receipts": defaultdict(Decimal), "rows": []}
                if "," in number or "\n" in number:
                    warnings.append(f"合同号 {number!r} 含多个编号或换行，将跳过；请在源表拆分后再导入")
            item = contracts[number]
            item["rows"].append(row_number)
            row_rate = decimal(value(row, "结算汇率"))
            if item["exchange_rate"] is None and row_rate is not None:
                item["exchange_rate"] = row_rate
            elif row_rate is not None and item["exchange_rate"] is not None and row_rate != item["exchange_rate"]:
                warning = f"合同 {number} 有不同结算汇率；合同汇率取首个值，收款按各行汇率"
                if warning not in warnings:
                    warnings.append(warning)
            for flow in oa_numbers:
                if flow not in item["oa_numbers"]:
                    item["oa_numbers"].append(flow)
            if not item["oa"] and item["oa_numbers"]:
                item["oa"] = item["oa_numbers"][0]
            if item["currency"] and line_currency and item["currency"] != line_currency:
                raise ValueError(f"合同 {number} 有不同币种，需人工拆分")
            amount = decimal(value(row, "合同金额(原币)"))
            if amount is not None:
                item["amount"] += amount
                item["amount_present"] = True
            freight = decimal(value(row, "运费(原币)"))
            if freight is not None:
                item["freight"] += freight
                item["freight_present"] = True
            item["items"].append({"position": len(item["items"]) + 1, "product_category": clean(value(row, "产品类别")), "product_subcategory": clean(value(row, "产品类别二级", "常规型号大类")), "model": clean(value(row, "产品型号", "产品名称")), "supplier": clean(value(row, "购入厂家")), "purchase_price": decimal(value(row, "购入价格（人民币）")), "quantity": decimal(value(row, "数量")), "sale_price": decimal(value(row, "销售单价(原币)")), "line_amount": amount})
            shipped_on = day(value(row, "实际发货日期"))
            if shipped_on:
                item["shipments"][shipped_on] = clean(value(row, "物流及单号")) or item["shipments"].get(shipped_on, "")
            received_on = day(value(row, "收款日期"))
            received_amount = decimal(value(row, "收款金额(原币)"))
            if received_on and received_amount is not None:
                receipt_currency = currency(value(row, "收款币种（R/M/U）")) or line_currency
                item["receipts"][(received_on, receipt_currency, row_rate)] += received_amount
        if not contracts:
            raise ValueError("未找到有合同编号的数据行")
        return {"sheet": sheet.title, "row_count": row_count, "contracts": list(contracts.values()), "salesperson_names": sorted(names), "warnings": warnings}
    finally:
        book.close()
