import json
from decimal import Decimal, InvalidOperation
from io import BytesIO
from urllib.parse import urlencode
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError, transaction
from django.core.paginator import Paginator
from django.http import HttpResponse, HttpResponseForbidden
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from .models import AuditEvent, Contract, LineItem, Material, OAFlow, OutboundItem, Receipt, Shipment
from .parser import extract_oa_html, parse_oa
from .excel_import import parse_workbook
from .dashboard_data import build_dashboard, yearly_sales_ranking


def visible_contracts(user):
    qs = Contract.objects.all()
    if user.is_superuser or user.groups.filter(name__in=["领导", "信息处理员"]).exists():
        return qs
    return qs.filter(salesperson=user)


def attach_flows(contract, numbers, source):
    added = 0
    for number in numbers:
        if not number:
            continue
        _, created = OAFlow.objects.get_or_create(contract=contract, number=number, defaults={"source": source})
        if created:
            added += 1
    return added


def oa_decimal(value):
    try:
        return Decimal(str(value).replace(",", "")) if value else None
    except InvalidOperation:
        return None


def add_oa_details(contract, data):
    materials_added = outbound_added = 0
    for item in data["materials"]:
        if Material.objects.filter(contract=contract, source_sha256=data["sha256"], source_position=item["position"]).exists():
            continue
        quantity = oa_decimal(item["quantity"])
        if data["oa_number"] and Material.objects.filter(contract=contract, source_oa_number=data["oa_number"], code=item["code"], name=item["name"], specification=item["specification"], quantity=quantity, unit=item["unit"]).exists():
            continue
        Material.objects.create(contract=contract, position=(contract.materials.order_by("-position").values_list("position", flat=True).first() or 0) + 1, code=item["code"], name=item["name"], specification=item["specification"], quantity=quantity, unit=item["unit"], technical_parameters=item["technical_parameters"], supply_requirement=item["supply_requirement"], stock_status=item["stock_status"], production_order=item["production_order"], source_oa_number=data["oa_number"] or "", source_sha256=data["sha256"], source_position=item["position"])
        materials_added += 1
    for item in data["outbound_items"]:
        if OutboundItem.objects.filter(contract=contract, source_sha256=data["sha256"], source_position=item["position"]).exists():
            continue
        if data["oa_number"] and OutboundItem.objects.filter(contract=contract, source_oa_number=data["oa_number"], document_number=item["document_number"], flow_number=item["flow_number"], material_code=item["material_code"], quantity=oa_decimal(item["quantity"])).exists():
            continue
        OutboundItem.objects.create(contract=contract, position=(contract.outbound_items.order_by("-position").values_list("position", flat=True).first() or 0) + 1, document_number=item["document_number"], flow_number=item["flow_number"], material_code=item["material_code"], material_name=item["material_name"], specification=item["specification"], unit=item["unit"], quantity=oa_decimal(item["quantity"]), notes=item["notes"], source_oa_number=data["oa_number"] or "", source_sha256=data["sha256"], source_position=item["position"])
        outbound_added += 1
    return materials_added, outbound_added


def oa_import_detail(data, materials_added, outbound_added):
    material_lines = [
        f"编码 {item['code'] or '未填'}，名称 {item['name'] or '未填'}，规格 {item['specification'] or '未填'}，数量 {item['quantity'] or '未填'} {item['unit']}，技术参数 {item['technical_parameters'] or '未填'}，供货需求 {item['supply_requirement'] or '未填'}，库存 {item['stock_status'] or '未填'}，生产订单 {item['production_order'] or '未填'}"
        for item in data["materials"]
    ]
    return (f"OA流水号 {data['oa_number'] or '未找到'}；识别供货物料 {len(data['materials'])} 条，本次新增 {materials_added} 条；"
            f"物料内容：{'；'.join(material_lines) or '无'}；识别已发货物料 {len(data['outbound_items'])} 条，本次新增 {outbound_added} 条；Line items 未变更")


@require_http_methods(["GET", "POST"])
def login_view(request):
    if request.method == "POST":
        user = authenticate(request, username=request.POST.get("username"), password=request.POST.get("password"))
        if user:
            login(request, user)
            return redirect("dashboard")
        messages.error(request, "用户名或密码错误")
    return render(request, "contracts/login.html")


@login_required
def logout_view(request):
    logout(request)
    return redirect("login")


@login_required
def dashboard(request):
    context = build_dashboard(request.user, request.GET)
    context["competition_year"] = timezone.localdate().year
    context["sales_ranking"] = yearly_sales_ranking(request.user, context["competition_year"])
    context["export_url"] = reverse("export") + ("?" + request.GET.urlencode() if request.GET else "")
    return render(request, "contracts/dashboard.html", context)


@login_required
def update_log(request):
    events = AuditEvent.objects.filter(contract__in=visible_contracts(request.user)).select_related("contract", "actor").order_by("-at", "-pk")
    query = request.GET.get("q", "").strip()
    if query:
        events = events.filter(contract__contract_number__icontains=query)
    page = Paginator(events, 50).get_page(request.GET.get("page"))
    return render(request, "contracts/update_log.html", {"page": page, "query": query})


@login_required
@require_http_methods(["GET", "POST"])
def upload(request):
    if request.method == "POST":
        file = request.FILES.get("file")
        if not file or not file.name.lower().endswith((".html", ".htm", ".zip")) or file.size > 20 * 1024 * 1024:
            messages.error(request, "请选择不超过 20 MB 的 OA HTML 或 ZIP 文件")
            return redirect("upload")
        try:
            data = parse_oa(extract_oa_html(file.read(), file.name))
            with transaction.atomic():
                contract = Contract.objects.filter(contract_number=data["contract_number"]).first()
                if contract:
                    if not (request.user.is_superuser or contract.salesperson_id == request.user.id or request.user.groups.filter(name="信息处理员").exists()):
                        raise ValueError("该合同归属其他业务员")
                    added = attach_flows(contract, [data["oa_number"]], "OA导入")
                    materials_added, outbound_added = add_oa_details(contract, data)
                    AuditEvent.objects.create(contract=contract, actor=request.user, action="补充OA详情", detail=oa_import_detail(data, materials_added, outbound_added))
                else:
                    contract = Contract.objects.create(oa_number=data["oa_number"], contract_number=data["contract_number"], customer_contract_number=data["customer_contract_number"], oa_signer=data["oa_signer"], salesperson=request.user, customer_name=data["customer_name"], currency=data["items"][0]["currency"] if data["items"] else "", source_filename=file.name[:255], source_sha256=data["sha256"])
                    attach_flows(contract, [data["oa_number"]], "OA导入")
                    materials_added, outbound_added = add_oa_details(contract, data)
                    AuditEvent.objects.create(contract=contract, actor=request.user, action="OA导入", detail=oa_import_detail(data, materials_added, outbound_added))
                    added = 1
            messages.success(request, f"合同 {contract.contract_number} 已保存，新增 {added} 个 OA 流程、{materials_added} 条物料、{outbound_added} 条出库记录；Line items 未变更")
            return redirect(reverse("dashboard") + "?" + urlencode({"q": contract.contract_number}))
        except (ValueError, IntegrityError) as exc:
            messages.error(request, str(exc))
    return render(request, "contracts/upload.html")


@login_required
@require_http_methods(["GET", "POST"])
def import_excel(request):
    if not (request.user.is_superuser or request.user.groups.filter(name="信息处理员").exists()):
        return HttpResponseForbidden()
    users = get_user_model().objects.filter(is_active=True).order_by("username")
    if request.method == "POST" and request.POST.get("stage") == "preview":
        request.session.pop("excel_preview", None)
        file = request.FILES.get("file")
        owner_id = request.POST.get("owner_id", "")
        owner = users.filter(pk=int(owner_id)).first() if owner_id.isdigit() else None
        if not owner or not file or not file.name.lower().endswith(".xlsx") or file.size > 10 * 1024 * 1024:
            messages.error(request, "请选择业务员账号及不超过 10 MB 的 xlsx 文件")
            return redirect("import_excel")
        try:
            parsed = parse_workbook(file.read())
            serializable = []
            for item in parsed["contracts"]:
                item = dict(item)
                item["shipments"] = [{"on": k, "tracking": v} for k, v in item["shipments"].items()]
                item["receipts"] = [{"on": k[0], "currency": k[1], "exchange_rate": str(k[2]) if k[2] is not None else None, "amount": str(v)} for k, v in item["receipts"].items()]
                serializable.append(json.loads(json.dumps(item, default=str)))
            request.session["excel_preview"] = {"owner_id": owner.pk, "owner_name": owner.get_full_name() or owner.username, "source_name": file.name[:255], "sheet": parsed["sheet"], "row_count": parsed["row_count"], "contracts": serializable, "salesperson_names": parsed["salesperson_names"], "warnings": parsed["warnings"]}
        except ValueError as exc:
            messages.error(request, str(exc))
            return redirect("import_excel")
    if request.method == "POST" and request.POST.get("stage") == "confirm":
        if request.POST.get("confirmed") != "yes":
            messages.error(request, "请先确认业务员归属和预览金额")
            return redirect("import_excel")
        preview = request.session.get("excel_preview")
        if not preview:
            messages.error(request, "预览已失效，请重新上传")
            return redirect("import_excel")
        owner = users.filter(pk=preview["owner_id"]).first()
        if not owner:
            messages.error(request, "业务员账号不存在")
            return redirect("import_excel")
        created = skipped = flows_added = 0
        try:
            with transaction.atomic():
                for item in preview["contracts"]:
                    if item["ambiguous"]:
                        skipped += 1
                        continue
                    c = Contract.objects.filter(contract_number=item["number"]).first()
                    if c:
                        flows_added += attach_flows(c, item["oa_numbers"], "历史Excel")
                        skipped += 1
                        continue
                    c = Contract.objects.create(oa_number=item["oa"] or None, contract_number=item["number"], signed_on=item["signed_on"], business_type=item["business_type"], salesperson=owner, customer_name=item["customer_name"], customer_category=item["customer_category"], inquiry_source=item["inquiry_source"], country=item["country"], industry=item["industry"], currency=item["currency"], freight=Decimal(item["freight"]) if item["freight_present"] else None, amount=Decimal(item["amount"]) if item["amount_present"] else None, invoice_on=item["invoice_on"], invoice_type=item["invoice_type"], purchase_invoiced=item["purchase_invoiced"], sales_invoiced=item["sales_invoiced"], agreed_ship_on=item["agreed_ship_on"], payment_method=item["payment_method"], exchange_rate=item["exchange_rate"], progress=item["progress"], progress_detail=item["progress_detail"], customs_declared=item["customs_declared"], email=item["email"], notes=item["notes"], source_filename=preview["source_name"])
                    flows_added += attach_flows(c, item["oa_numbers"], "历史Excel")
                    for line in item["items"]:
                        LineItem.objects.create(contract=c, **line)
                    for shipment in item["shipments"]:
                        Shipment.objects.create(contract=c, shipped_on=shipment["on"], tracking=shipment["tracking"])
                    for receipt in item["receipts"]:
                        Receipt.objects.create(contract=c, received_on=receipt["on"], currency=receipt["currency"], amount=receipt["amount"], exchange_rate=receipt.get("exchange_rate"))
                    AuditEvent.objects.create(contract=c, actor=request.user, action="历史Excel导入", detail=f"源表行：{item['rows']}")
                    created += 1
        except (ValueError, IntegrityError) as exc:
            messages.error(request, str(exc))
            return redirect("import_excel")
        del request.session["excel_preview"]
        messages.success(request, f"成功导入 {created} 份合同、新增 {flows_added} 个 OA 流程；跳过 {skipped} 份已有或待拆分合同")
        return redirect("dashboard")
    preview = request.session.get("excel_preview")
    existing = set(Contract.objects.filter(contract_number__in=[c["number"] for c in preview["contracts"]]).values_list("contract_number", flat=True)) if preview else set()
    return render(request, "contracts/import_excel.html", {"users": users, "preview": preview, "existing": existing})


HEADERS = ["序号", "OA流水号", "合同签订日期", "业务类型", "合同编号", "业务员", "客户公司名称", "客户类别", "询盘来源", "产品类别", "产品类别二级", "产品型号", "购入厂家", "购入价格（人民币）", "数量", "人民币/美元/欧元（R/M/U）", "销售单价(原币)", "运费(原币)", "合同金额(原币)", "国家/地区", "行业", "开票日期", "发票类型", "购入是否开票", "销售是否开票", "合订签订发货日期", "实际发货日期", "物流及单号", "收款方式", "收款日期", "收款币种（R/M/U）", "收款金额(原币)", "结算汇率", "执行进度", "执行详情", "是否报关", "邮箱", "备注"]


@login_required
def export_csv(request):
    if not (request.user.is_superuser or request.user.groups.filter(name__in=["领导", "信息处理员"]).exists()):
        return HttpResponseForbidden()
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    filters = build_dashboard(request.user, request.GET)
    contracts = Contract.objects.filter(pk__in=filters["export_ids"]).prefetch_related("items", "materials", "outbound_items", "shipments", "receipts", "oa_flows").select_related("salesperson").order_by("signed_on", "pk")
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "合同明细"
    sheet.append(HEADERS)
    sheet.freeze_panes = "A2"
    index = 0
    for contract in contracts:
        items = list(contract.items.all()) or [None]
        shipments = list(contract.shipments.order_by("shipped_on"))
        receipts = list(contract.receipts.order_by("received_on"))
        for item in items:
            index += 1
            joined = lambda values: "; ".join(str(v) for v in values if v is not None and v != "")
            values = [index, joined(f.number for f in contract.oa_flows.all()) or contract.oa_number, contract.signed_on, contract.business_type, contract.contract_number, contract.salesperson.get_full_name() or contract.salesperson.username, contract.customer_name, contract.customer_category, contract.inquiry_source, item.product_category if item else "", item.product_subcategory if item else "", item.model if item else "", item.supplier if item else "", item.purchase_price if item else "", item.quantity if item else "", contract.currency, item.sale_price if item else "", contract.freight, contract.amount, contract.country, contract.industry, contract.invoice_on, contract.invoice_type, contract.purchase_invoiced, contract.sales_invoiced, contract.agreed_ship_on, joined(s.shipped_on for s in shipments), joined(s.tracking for s in shipments), contract.payment_method, joined(r.received_on for r in receipts), joined(r.currency for r in receipts), joined(r.amount for r in receipts), joined(r.exchange_rate for r in receipts) or contract.exchange_rate, contract.progress, contract.progress_detail, contract.customs_declared, contract.email, contract.notes]
            for column, value in enumerate(values, 1):
                if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
                    value = "'" + value
                cell = sheet.cell(row=index + 1, column=column, value=value)
                if isinstance(value, (int, float, Decimal)):
                    cell.number_format = "#,##0.00" if column not in (1, 15) else "0.###"
        AuditEvent.objects.create(contract=contract, actor=request.user, action="导出")
    sheet.auto_filter.ref = f"A1:AL{max(index + 1, 1)}"
    for cell in sheet[1]:
        cell.fill = PatternFill("solid", fgColor="173C75")
        cell.font = Font(color="FFFFFF", bold=True)
        cell.alignment = Alignment(vertical="center", wrap_text=True)
    sheet.row_dimensions[1].height = 32
    for column in sheet.columns:
        letter = column[0].column_letter
        sheet.column_dimensions[letter].width = min(30, max(12, len(str(column[0].value)) * 2))
    def safe_row(target, values):
        target.append(["'" + value if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")) else value for value in values])

    material_sheet = workbook.create_sheet("OA供货物料")
    safe_row(material_sheet, ["合同编号", "业务员", "OA流水号", "行号", "物料编码", "物料名称", "规格型号", "数量", "单位", "技术参数", "供货需求", "库存情况", "生产订单号"])
    outbound_sheet = workbook.create_sheet("OA已发货物料")
    safe_row(outbound_sheet, ["合同编号", "业务员", "OA流水号", "行号", "出库单据编号", "出库流水号", "物料编码", "物料名称", "规格型号", "数量", "单位", "备注"])
    shipment_sheet = workbook.create_sheet("发货记录")
    safe_row(shipment_sheet, ["合同编号", "业务员", "实际发货日期", "本次发货金额(原币)", "合同币种", "物流及单号", "备注"])
    for contract in contracts:
        owner = contract.salesperson.get_full_name() or contract.salesperson.username
        for material in contract.materials.all():
            safe_row(material_sheet, [contract.contract_number, owner, material.source_oa_number, material.source_position, material.code, material.name, material.specification, material.quantity, material.unit, material.technical_parameters, material.supply_requirement, material.stock_status, material.production_order])
        for outbound in contract.outbound_items.all():
            safe_row(outbound_sheet, [contract.contract_number, owner, outbound.source_oa_number, outbound.source_position, outbound.document_number, outbound.flow_number, outbound.material_code, outbound.material_name, outbound.specification, outbound.quantity, outbound.unit, outbound.notes])
        for shipment in contract.shipments.all():
            safe_row(shipment_sheet, [contract.contract_number, owner, shipment.shipped_on, shipment.amount, contract.currency, shipment.tracking, shipment.notes])
    for detail_sheet in (material_sheet, outbound_sheet, shipment_sheet):
        detail_sheet.freeze_panes = "A2"
        detail_sheet.auto_filter.ref = detail_sheet.dimensions
        for cell in detail_sheet[1]:
            cell.fill = PatternFill("solid", fgColor="173C75")
            cell.font = Font(color="FFFFFF", bold=True)
        for column in detail_sheet.columns:
            detail_sheet.column_dimensions[column[0].column_letter].width = min(32, max(14, len(str(column[0].value)) * 2))
    notes = workbook.create_sheet("筛选说明")
    for row in [
        ("导出范围", "当前看板筛选结果"),
        ("查看范围", filters["scope"]),
        ("统计年份", filters["year"]),
        ("业绩口径", filters["metric_label"]),
        ("开始日期", filters["start"]),
        ("结束日期", filters["end"]),
        ("下钻维度", filters["dimension"]),
        ("下钻值", filters["focus"]),
        ("下钻月份", filters["month_focus"]),
        ("合同数量", filters["detail_count"]),
        ("口径说明", "筛选决定导出哪些合同；每份合同按产品逐行导出。发货或收款口径按事件日期选合同，明细列保留该合同全部发货与收款记录。"),
        ("明细说明", "OA供货物料、OA已发货物料、发货记录分别独立成表，按合同编号关联；空表表示当前范围没有相应记录。OA已发货物料不等于系统中的发货金额记录。"),
        ("金额说明", "合同金额在同一合同的每个产品行重复显示，请按合同编号去重后汇总。不同币种不得直接相加。"),
    ]:
        notes.append(row)
    notes.column_dimensions["A"].width = 20
    notes.column_dimensions["B"].width = 100
    stream = BytesIO()
    workbook.save(stream)
    response = HttpResponse(stream.getvalue(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    response["Content-Disposition"] = 'attachment; filename="contracts-filtered.xlsx"'
    return response
