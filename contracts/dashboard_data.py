from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from urllib.parse import urlencode
from django.contrib.auth import get_user_model
from django.db.models import Q
from django.utils import timezone

from .models import Contract
from .currency import conversion, normalized_currency

DIMENSIONS = {
    "country": "国家/地区", "customer_category": "客户类别", "inquiry_source": "询盘来源",
    "product_category": "产品类别", "product_subcategory": "产品类别二级", "model": "产品型号",
    "industry": "行业", "salesperson": "业务员", "customer": "客户",
}
METRICS = {"signed": "签订", "shipped": "发货", "received": "收款"}


def label(contract, dimension):
    if dimension == "salesperson":
        return contract.salesperson.get_full_name() or contract.salesperson.username
    if dimension == "customer":
        return contract.customer_name.strip() or "未填写"
    return (getattr(contract, dimension, "") or "").strip() or "未填写"


def item_labels(contract, dimension):
    if dimension in ("product_category", "product_subcategory", "model"):
        return {((getattr(item, dimension) or "").strip() or "未填写") for item in contract.items.all()}
    return {label(contract, dimension)}


def money(value):
    return f"{value:,.2f}" if value is not None else "—"


def paid_fraction(value):
    try:
        fraction = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None
    return fraction if fraction.is_finite() and Decimal("0") <= fraction <= Decimal("1") else None


def link(params, **changes):
    values = {key: value for key, value in params.items() if value not in (None, "")}
    values.update(changes)
    return "?" + urlencode(values)


def build_dashboard(user, params):
    privileged = user.is_superuser or user.groups.filter(name__in=["领导", "信息处理员"]).exists()
    base = Contract.objects.all() if privileged else Contract.objects.filter(salesperson=user)
    salesperson_ids = list(base.order_by().values_list("salesperson_id", flat=True).distinct()) if privileged else []
    people = get_user_model().objects.filter(Q(groups__name="业务员") | Q(pk__in=salesperson_ids, is_staff=False), is_active=True, is_superuser=False).distinct().order_by("last_name", "first_name", "username") if privileged else []
    salespeople = [{"scope": f"salesperson:{person.pk}", "name": person.get_full_name() or person.username} for person in people]
    allowed_salesperson_ids = {int(person["scope"].partition(":")[2]) for person in salespeople}
    base = base.select_related("salesperson").prefetch_related("items", "receipts", "shipments", "oa_flows")
    scope = params.get("scope", "all") if privileged else "mine"
    if scope == "mine":
        base = base.filter(salesperson=user)
    elif scope.startswith("salesperson:") and privileged:
        selected_id = scope.partition(":")[2]
        if selected_id.isdigit() and int(selected_id) in allowed_salesperson_ids:
            base = base.filter(salesperson_id=int(selected_id))
        else:
            scope = "all"
    elif scope != "all":
        scope = "all" if privileged else "mine"
    query = params.get("q", "").strip()[:100]
    if query:
        base = base.filter(
            Q(contract_number__icontains=query) | Q(customer_name__icontains=query)
            | Q(oa_number__icontains=query) | Q(oa_flows__number__icontains=query)
            | Q(items__model__icontains=query)
        ).distinct()
    search_matches = list(base.order_by("-pk")[:50]) if query else []
    metric = params.get("metric", "signed")
    if metric not in METRICS:
        metric = "signed"
    current_year = timezone.localdate().year
    year = params.get("year", str(current_year))
    if year != "all" and not (year.isdigit() and 2000 <= int(year) <= 2100):
        year = str(current_year)
    dimension = params.get("dimension", "country")
    if dimension not in DIMENSIONS:
        dimension = "country"
    focus = params.get("focus", "").strip()[:200]
    start, end = params.get("start", ""), params.get("end", "")
    for value in (start, end):
        if value:
            try: date.fromisoformat(value)
            except ValueError: start, end = "", ""
    contracts = list(base)
    years = sorted({c.signed_on.year for c in contracts if c.signed_on} | {current_year, current_year - 1}, reverse=True)
    if focus:
        contracts = [c for c in contracts if focus in item_labels(c, dimension)]
    entries = []
    for c in contracts:
        if metric == "signed":
            if c.signed_on:
                entries.append((c, c.signed_on, normalized_currency(c.currency) or "未填币种", c.amount, None))
        elif metric == "shipped":
            entries.extend((c, s.shipped_on, normalized_currency(c.currency) or "未填币种", s.amount, None) for s in c.shipments.all())
        else:
            entries.extend((c, r.received_on, normalized_currency(r.currency) or "未填币种", r.amount, r) for r in c.receipts.all())
    if year != "all":
        entries = [e for e in entries if e[1].year == int(year)]
    if start:
        entries = [e for e in entries if e[1].isoformat() >= start]
    if end:
        entries = [e for e in entries if e[1].isoformat() <= end]
    selected_ids = {e[0].pk for e in entries}
    selected = [c for c in contracts if c.pk in selected_ids]
    totals = defaultdict(Decimal)
    counts = defaultdict(set)
    months = defaultdict(Decimal)
    month_counts = defaultdict(set)
    usd_month_rmb = defaultdict(Decimal)
    usd_month_ids = defaultdict(set)
    contract_detail = defaultdict(lambda: {"amounts": defaultdict(Decimal), "rmb": Decimal(), "sources": defaultdict(int), "source_amounts": defaultdict(Decimal), "currencies": defaultdict(lambda: {"amount": Decimal(), "amount_count": 0, "rmb": Decimal(), "converted_count": 0, "count": 0}), "rates": set(), "converted": False})
    contributing_ids = set()
    rmb_total = Decimal()
    rate_counts = {"actual": 0, "estimated": 0, "native": 0, "missing_amount": 0, "unknown_currency": 0}
    rate_amounts = {"actual": Decimal(), "estimated": Decimal(), "native": Decimal()}
    for c, when, currency, amount, receipt in entries:
        counts[currency].add(c.pk)
        currency_info = contract_detail[c.pk]["currencies"][currency]
        currency_info["count"] += 1
        month_counts[(currency, when.strftime("%Y-%m"))].add(c.pk)
        if amount is not None:
            totals[currency] += amount
            months[(currency, when.strftime("%Y-%m"))] += amount
            contract_detail[c.pk]["amounts"][currency] += amount
            currency_info["amount"] += amount
            currency_info["amount_count"] += 1
        rmb, source, applied_rate = conversion(
            amount, currency, contract_rate=c.exchange_rate,
            receipt_rate=receipt.exchange_rate if receipt else None,
            same_contract_currency=receipt is None or normalized_currency(receipt.currency) == normalized_currency(c.currency),
        )
        rate_counts[source] += 1
        contract_detail[c.pk]["sources"][source] += 1
        if rmb is not None:
            rmb_total += rmb
            rate_amounts[source] += rmb
            month_key = when.strftime("%Y-%m")
            usd_month_rmb[month_key] += rmb
            usd_month_ids[month_key].add(c.pk)
            contract_detail[c.pk]["rmb"] += rmb
            contract_detail[c.pk]["source_amounts"][source] += rmb
            currency_info["rmb"] += rmb
            currency_info["converted_count"] += 1
            contract_detail[c.pk]["rates"].add(f"{currency} × {applied_rate}")
            contract_detail[c.pk]["converted"] = True
            contributing_ids.add(c.pk)
    for c in selected:
        info = contract_detail[c.pk]
        c.metric_amount_display = "; ".join(f"{cur} {money(value)}" for cur, value in info["amounts"].items()) or "未填金额"
        c.metric_rmb_display = money(info["rmb"]) if info["converted"] else "未计入"
        source_text = " · ".join(f"{name}{info['sources'][source]}笔" for source, name in (("actual", "录入汇率"), ("estimated", "预估汇率"), ("native", "无需换算")) if info["sources"][source])
        c.metric_rate_display = (source_text + "｜" + "; ".join(sorted(info["rates"]))) if source_text else "缺少金额或币种"
    currency_cards = []
    for cur in sorted(counts, key=lambda x: totals[x], reverse=True):
        cur_contracts = [c for c in selected if normalized_currency(c.currency) == cur]
        received = sum((r.amount for c in cur_contracts for r in c.receipts.all() if normalized_currency(r.currency) == cur), Decimal())
        signed_total = sum((c.amount for c in cur_contracts if c.amount is not None), Decimal())
        rated_contracts = [(c, paid_fraction(c.progress)) for c in cur_contracts if c.amount is not None]
        rated_total = sum((c.amount for c, fraction in rated_contracts if fraction is not None), Decimal())
        due = sum((c.amount * (Decimal("1") - fraction) for c, fraction in rated_contracts if fraction is not None), Decimal())
        currency_cards.append({"name": cur, "total": money(totals[cur]), "count": len(counts[cur]), "received": money(received), "due": money(due), "due_unrated": sum(1 for _, fraction in rated_contracts if fraction is None), "rate": round(float((rated_total - due) / rated_total * 100)) if rated_total > 0 else None, "raw_total": totals[cur], "url": link(params, detail="currency", detail_value=cur, month="") + "#contract-list", "trend_url": link(params, currency=cur, month="", detail="", detail_value="")})
    received_same_rmb = Decimal()
    due_rmb = Decimal()
    cross_currency_receipts = 0
    paid_details = {}
    due_details = {}
    due_unrated_details = {}
    due_unrated = 0
    cross_details = {}
    if metric == "signed":
        for c in selected:
            contract_currency = normalized_currency(c.currency)
            paid_original = Decimal()
            paid_converted = Decimal()
            same_count = 0
            cross_lines = []
            for receipt in c.receipts.all():
                receipt_currency = normalized_currency(receipt.currency)
                if receipt_currency != contract_currency:
                    cross_currency_receipts += 1
                    cross_lines.append(f"{receipt.received_on:%Y-%m-%d} {receipt_currency} {money(receipt.amount)}")
                    continue
                same_count += 1
                paid_original += receipt.amount
                paid_value, _, _ = conversion(receipt.amount, receipt_currency, contract_rate=c.exchange_rate, receipt_rate=receipt.exchange_rate)
                if paid_value is not None:
                    received_same_rmb += paid_value
                    paid_converted += paid_value
            if same_count:
                paid_details[c.pk] = f"{contract_currency} {money(paid_original)} → ¥{money(paid_converted)}；{same_count} 笔收款"
            if cross_lines:
                cross_details[c.pk] = f"{len(cross_lines)} 笔：" + "; ".join(cross_lines)
            if c.amount is not None:
                fraction = paid_fraction(c.progress)
                if fraction is None:
                    due_unrated += 1
                    due_unrated_details[c.pk] = f"执行进度 {c.progress or '未填写'}；合同金额 {contract_currency} {money(c.amount)}，未计入待收"
                else:
                    remaining = c.amount * (Decimal("1") - fraction)
                    due_value, _, _ = conversion(remaining, contract_currency, contract_rate=c.exchange_rate)
                    if due_value is not None:
                        due_rmb += due_value
                        if remaining > 0:
                            due_details[c.pk] = f"执行进度 {c.progress}（已收 {money(fraction * 100)}%）；{contract_currency} {money(remaining)} → ¥{money(due_value)}"
    chart_currency = params.get("currency", "usd_all")
    if chart_currency not in counts and chart_currency != "usd_all":
        chart_currency = "usd_all"
    chart_currency_label = "全部折美元" if chart_currency == "usd_all" else chart_currency
    month_keys = sorted({when.strftime("%Y-%m") for _, when, _, _, _ in entries})
    if year != "all":
        month_keys = [f"{year}-{month:02d}" for month in range(1, 13)]
    elif len(month_keys) > 12:
        month_keys = month_keys[-12:]
    monthly_values = {key: usd_month_rmb[key] if chart_currency == "usd_all" else months[(chart_currency, key)] for key in month_keys}
    peak = max(monthly_values.values(), default=Decimal())
    chart = [{"label": key[2:].replace("-", "/"), "amount": money((monthly_values[key] / Decimal("7")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) if chart_currency == "usd_all" else monthly_values[key]), "count": len(usd_month_ids[key] if chart_currency == "usd_all" else month_counts[(chart_currency, key)]), "width": max(3, round(float(monthly_values[key] / peak * 100))) if peak else 3, "url": link(params, month=key, currency=chart_currency, detail="", detail_value="")} for key in month_keys]
    month_focus = params.get("month", "")
    detail = selected
    if month_focus in month_keys:
        if chart_currency == "usd_all":
            detail = [c for c in selected if c.pk in usd_month_ids[month_focus]]
        else:
            detail = [c for c in selected if any(e[0].pk == c.pk and e[1].strftime("%Y-%m") == month_focus and e[2] == chart_currency for e in entries)]
    else:
        month_focus = ""
    detail_mode = params.get("detail", "")
    detail_title = "合同明细"
    detail_extra_label = ""
    if detail_mode == "rmb":
        detail = [c for c in detail if c.pk in contributing_ids]
        detail_title = "人民币总额组成"
    elif detail_mode == "currency":
        selected_currency = normalized_currency(params.get("detail_value", ""))[:20]
        detail = [c for c in detail if c.pk in counts.get(selected_currency, set())]
        detail_title = f"{selected_currency}金额组成"
        detail_extra_label = "所选币种贡献"
        for c in detail:
            info = contract_detail[c.pk]["currencies"][selected_currency]
            original = money(info["amount"]) if info["amount_count"] else "金额未填"
            converted = f"折人民币 ¥{money(info['rmb'])}" if info["converted_count"] else "未计入折人民币总计"
            c.detail_value_display = f"{selected_currency} {original}；{converted}；{info['count']} 笔记录"
    elif detail_mode == "rate":
        source = params.get("detail_value", "")
        names = {"actual": "录入汇率", "estimated": "标准预估汇率", "native": "人民币无需换算", "missing_amount": "缺少金额", "unknown_currency": "币种未识别"}
        if source in names:
            detail = [c for c in detail if contract_detail[c.pk]["sources"][source]]
            detail_title = f"{names[source]}组成"
            detail_extra_label = "对应记录"
            for c in detail:
                info = contract_detail[c.pk]
                c.detail_value_display = f"{info['sources'][source]} 笔；折人民币 ¥{money(info['source_amounts'][source])}"
        else:
            detail_mode = ""
    elif detail_mode == "all":
        detail_title = "合同笔数明细"
    elif metric == "signed" and detail_mode in ("paid", "due", "cross", "due_unrated"):
        detail_map = {"paid": paid_details, "due": due_details, "cross": cross_details, "due_unrated": due_unrated_details}[detail_mode]
        detail = [c for c in detail if c.pk in detail_map]
        detail_title = {"paid": "同币种已收组成", "due": "待收款组成", "cross": "跨币种收款明细", "due_unrated": "待收未计入合同"}[detail_mode]
        detail_extra_label = {"paid": "已收金额", "due": "待收金额", "cross": "跨币种收款", "due_unrated": "未计入原因"}[detail_mode]
        for c in detail:
            c.detail_value_display = detail_map[c.pk]
    elif detail_mode == "overdue":
        detail = [c for c in detail if c.agreed_ship_on and c.agreed_ship_on < timezone.localdate() and not c.shipments.exists()]
        detail_title = "逾期未发货合同"
        detail_extra_label = "约定发货日"
        for c in detail:
            c.detail_value_display = c.agreed_ship_on.strftime("%Y-%m-%d")
    elif detail_mode == "invoiced":
        detail = [c for c in detail if c.sales_invoiced is True]
        detail_title = "销售已开票合同"
        detail_extra_label = "开票情况"
        for c in detail:
            c.detail_value_display = c.invoice_on.strftime("%Y-%m-%d") if c.invoice_on else "已开票，日期未填"
    elif detail_mode == "progress":
        progress_value = params.get("detail_value", "")[:100]
        detail = [c for c in detail if (c.progress or "未填写") == progress_value]
        detail_title = f"执行进度：{progress_value}"
        detail_extra_label = "执行详情"
        for c in detail:
            c.detail_value_display = c.progress_detail or "未填写"
    else:
        detail_mode = ""
    structures = defaultdict(lambda: {"ids": set(), "amounts": defaultdict(Decimal), "rmb": Decimal()})
    for c, when, cur, amount, receipt in entries:
        if dimension in ("product_category", "product_subcategory", "model"):
            for item in c.items.all():
                value = ((getattr(item, dimension) or "").strip() or "未填写")
                structures[value]["ids"].add(c.pk)
                if metric == "signed" and item.line_amount is not None:
                    structures[value]["amounts"][cur] += item.line_amount
        else:
            value = label(c, dimension)
            structures[value]["ids"].add(c.pk)
            if amount is not None:
                structures[value]["amounts"][cur] += amount
                converted, _, _ = conversion(amount, cur, contract_rate=c.exchange_rate, receipt_rate=receipt.exchange_rate if receipt else None, same_contract_currency=receipt is None or normalized_currency(receipt.currency) == normalized_currency(c.currency))
                if converted is not None:
                    structures[value]["rmb"] += converted
    def structure_score(info):
        if dimension in ("product_category", "product_subcategory", "model"):
            return len(info["ids"])
        return info["rmb"] if chart_currency == "usd_all" else info["amounts"].get(chart_currency, Decimal())
    ranked = sorted(structures.items(), key=lambda pair: structure_score(pair[1]), reverse=True)
    peak_rank = max((structure_score(info) for _, info in ranked), default=Decimal())
    structure_rows = []
    for name, info in ranked[:12]:
        score = structure_score(info)
        structure_rows.append({"name": name, "count": len(info["ids"]), "amounts": [{"currency": cur, "value": money(amount)} for cur, amount in info["amounts"].items()], "width": max(3, round(float(score / peak_rank * 100))) if peak_rank else 3, "url": link(params, dimension=dimension, focus=name, month="", detail="", detail_value="")})
    customers = defaultdict(lambda: {"ids": set(), "amounts": defaultdict(Decimal), "rmb": Decimal()})
    for c, _, cur, amount, receipt in entries:
        name = c.customer_name.strip() or "未填写"
        customers[name]["ids"].add(c.pk)
        if amount is not None:
            customers[name]["amounts"][cur] += amount
            converted, _, _ = conversion(amount, cur, contract_rate=c.exchange_rate, receipt_rate=receipt.exchange_rate if receipt else None, same_contract_currency=receipt is None or normalized_currency(receipt.currency) == normalized_currency(c.currency))
            if converted is not None:
                customers[name]["rmb"] += converted
    customer_rows = []
    for name, info in sorted(customers.items(), key=lambda pair: pair[1]["rmb"] if chart_currency == "usd_all" else pair[1]["amounts"].get(chart_currency, Decimal()), reverse=True)[:8]:
        customer_rows.append({"name": name, "count": len(info["ids"]), "amounts": [{"currency": cur, "value": money(amount)} for cur, amount in info["amounts"].items()], "url": link(params, dimension="customer", focus=name, month="", detail="", detail_value="")})
    progress = defaultdict(set)
    for c in selected:
        progress[c.progress or "未填写"].add(c.pk)
    progress_rows = [{"name": name, "count": len(ids), "width": round(len(ids) / len(selected) * 100) if selected else 0, "url": link(params, detail="progress", detail_value=name, month="") + "#contract-list"} for name, ids in sorted(progress.items(), key=lambda pair: len(pair[1]), reverse=True)]
    overdue = sum(1 for c in selected if c.agreed_ship_on and c.agreed_ship_on < timezone.localdate() and not c.shipments.exists())
    invoiced = sum(1 for c in selected if c.sales_invoiced is True)
    trend_tabs = [{"key": "usd_all", "name": "全部折美元", "url": link(params, currency="usd_all", month="", detail="", detail_value="")}] + [{"key": card["name"], "name": card["name"], "url": card["trend_url"]} for card in currency_cards]
    dimension_tabs = [{"key": key, "name": name, "url": link(params, dimension=key, focus="", month="", detail="", detail_value="")} for key, name in DIMENSIONS.items()]
    rate_urls = {source: link(params, detail="rate", detail_value=source, month="") + "#contract-list" for source in rate_counts}
    return {"search_matches": search_matches, "metric": metric, "metric_label": METRICS[metric], "metrics": METRICS, "scope": scope, "privileged": privileged, "salespeople": salespeople, "year": year, "years": years, "dimension": dimension, "dimensions": DIMENSIONS, "focus": focus, "query": query, "start": start, "end": end, "count": len(selected), "detail_count": len(detail), "export_ids": [c.pk for c in detail], "rmb_total": money(rmb_total), "rmb_detail_url": link(params, detail="rmb", month="") + "#contract-list", "all_detail_url": link(params, detail="all", month="") + "#contract-list", "overdue_detail_url": link(params, detail="overdue", month="") + "#contract-list", "invoiced_detail_url": link(params, detail="invoiced", month="") + "#contract-list", "paid_detail_url": link(params, detail="paid", month="") + "#contract-list", "due_detail_url": link(params, detail="due", month="") + "#contract-list", "due_unrated_url": link(params, detail="due_unrated", month="") + "#contract-list", "cross_detail_url": link(params, detail="cross", month="") + "#contract-list", "detail_mode": detail_mode, "detail_title": detail_title, "detail_extra_label": detail_extra_label, "received_same_rmb": money(received_same_rmb), "due_rmb": money(due_rmb), "due_unrated": due_unrated, "cross_currency_receipts": cross_currency_receipts, "rate_counts": rate_counts, "rate_urls": rate_urls, "rate_amounts": {key: money(value) for key, value in rate_amounts.items()}, "currency_cards": currency_cards, "trend_tabs": trend_tabs, "dimension_tabs": dimension_tabs, "chart_currency": chart_currency, "chart_currency_label": chart_currency_label, "chart": chart, "structure_rows": structure_rows, "customer_rows": customer_rows, "progress_rows": progress_rows, "overdue": overdue, "invoiced": invoiced, "contracts": detail if detail_mode else detail[:100], "month_focus": month_focus, "clear_url": link(params, focus="", month="", detail="", detail_value="")}
