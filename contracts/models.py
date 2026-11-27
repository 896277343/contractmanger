from django.conf import settings
from django.db import models


class Contract(models.Model):
    STATUS = [("draft", "待补录"), ("submitted", "待审核"), ("approved", "已审核"), ("rejected", "退回")]
    oa_number = models.CharField("OA流水号", max_length=40, null=True, blank=True)
    contract_number = models.CharField("合同编号", max_length=100, unique=True)
    customer_contract_number = models.CharField("甲方合同号", max_length=100, blank=True)
    signed_on = models.DateField("合同签订日期", null=True, blank=True)
    business_type = models.CharField("业务类型", max_length=30, blank=True)
    salesperson = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name="业务员", on_delete=models.PROTECT, related_name="contracts")
    oa_signer = models.CharField("OA合同签订人", max_length=100, blank=True)
    customer_name = models.CharField("客户公司名称", max_length=200, blank=True)
    customer_category = models.CharField("客户类别", max_length=100, blank=True)
    inquiry_source = models.CharField("询盘来源", max_length=100, blank=True)
    country = models.CharField("国家/地区", max_length=100, blank=True)
    continent = models.CharField("所属大洲", max_length=20, blank=True, editable=False)
    industry = models.CharField("行业", max_length=100, blank=True)
    currency = models.CharField("合同币种", max_length=20, blank=True)
    freight = models.DecimalField("运费(原币)", max_digits=18, decimal_places=2, null=True, blank=True)
    amount = models.DecimalField("合同金额(原币)", max_digits=18, decimal_places=2, null=True, blank=True)
    agreed_ship_on = models.DateField("约定发货日期", null=True, blank=True)
    invoice_on = models.DateField("开票日期", null=True, blank=True)
    invoice_type = models.CharField("发票类型", max_length=50, blank=True)
    purchase_invoiced = models.BooleanField("购入是否开票", null=True, blank=True)
    sales_invoiced = models.BooleanField("销售是否开票", null=True, blank=True)
    payment_method = models.CharField("收款方式", max_length=50, blank=True)
    exchange_rate = models.DecimalField("结算汇率", max_digits=18, decimal_places=6, null=True, blank=True)
    progress = models.CharField("执行进度", max_length=50, blank=True)
    progress_detail = models.TextField("执行详情", blank=True)
    customs_declared = models.BooleanField("是否报关", null=True, blank=True)
    email = models.EmailField("邮箱", blank=True)
    notes = models.TextField("备注", blank=True)
    status = models.CharField("状态", max_length=20, choices=STATUS, default="draft")
    source_filename = models.CharField("OA源文件名", max_length=255, blank=True)
    source_sha256 = models.CharField("OA源文件摘要", max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        from .geography import continent_for
        self.continent = continent_for(self.country)
        if kwargs.get("update_fields") is not None:
            kwargs["update_fields"] = set(kwargs["update_fields"]) | {"continent"}
        super().save(*args, **kwargs)

    def __str__(self):
        return self.contract_number

    @property
    def oa_numbers_display(self):
        return ", ".join(flow.number for flow in self.oa_flows.all()) or self.oa_number or ""


class OAFlow(models.Model):
    contract = models.ForeignKey(Contract, on_delete=models.CASCADE, related_name="oa_flows")
    number = models.CharField("OA流水号", max_length=40)
    source = models.CharField("来源", max_length=20, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]
        constraints = [models.UniqueConstraint(fields=["contract", "number"], name="unique_oa_flow_per_contract")]

    def __str__(self):
        return self.number


class LineItem(models.Model):
    contract = models.ForeignKey(Contract, on_delete=models.CASCADE, related_name="items")
    position = models.PositiveIntegerField(default=1)
    product_name = models.CharField("商品名称", max_length=200, blank=True)
    product_category = models.CharField("产品类别", max_length=100, blank=True)
    product_subcategory = models.CharField("产品类别二级", max_length=100, blank=True)
    model = models.CharField("产品型号", max_length=200, blank=True)
    supplier = models.CharField("购入厂家", max_length=200, blank=True)
    purchase_price = models.DecimalField("购入价格(人民币)", max_digits=18, decimal_places=2, null=True, blank=True)
    quantity = models.DecimalField("数量", max_digits=18, decimal_places=3, null=True, blank=True)
    unit = models.CharField("单位", max_length=30, blank=True)
    sale_price = models.DecimalField("销售单价(原币)", max_digits=18, decimal_places=2, null=True, blank=True)
    line_amount = models.DecimalField("行金额(原币)", max_digits=18, decimal_places=2, null=True, blank=True)
    notes = models.TextField(blank=True)
    oa_source_sha256 = models.CharField("OA产品来源摘要", max_length=64, blank=True)
    oa_source_position = models.PositiveIntegerField("OA产品行号", null=True, blank=True)

    class Meta:
        ordering = ["position", "id"]


class Material(models.Model):
    contract = models.ForeignKey(Contract, on_delete=models.CASCADE, related_name="materials")
    position = models.PositiveIntegerField(default=1)
    code = models.CharField("物料编码", max_length=100, blank=True)
    name = models.CharField("物料名称", max_length=200, blank=True)
    specification = models.CharField("规格型号", max_length=200, blank=True)
    quantity = models.DecimalField("数量", max_digits=18, decimal_places=3, null=True, blank=True)
    unit = models.CharField("单位", max_length=30, blank=True)
    technical_parameters = models.TextField("技术参数", blank=True)
    supply_requirement = models.TextField("供货需求", blank=True)
    stock_status = models.CharField("库存情况", max_length=100, blank=True)
    production_order = models.CharField("生产订单号", max_length=100, blank=True)
    source_oa_number = models.CharField("来源OA流水号", max_length=40, blank=True)
    source_sha256 = models.CharField("来源OA文件摘要", max_length=64)
    source_position = models.PositiveIntegerField("来源物料行号")

    class Meta:
        ordering = ["position", "id"]
        constraints = [models.UniqueConstraint(fields=["contract", "source_sha256", "source_position"], name="unique_material_oa_row")]


class OutboundItem(models.Model):
    contract = models.ForeignKey(Contract, on_delete=models.CASCADE, related_name="outbound_items")
    position = models.PositiveIntegerField(default=1)
    document_number = models.CharField("出库单据编号", max_length=100, blank=True)
    flow_number = models.CharField("出库流水号", max_length=100, blank=True)
    material_code = models.CharField("物料编码", max_length=100, blank=True)
    material_name = models.CharField("物料名称", max_length=200, blank=True)
    specification = models.CharField("规格型号", max_length=200, blank=True)
    unit = models.CharField("单位", max_length=30, blank=True)
    quantity = models.DecimalField("数量", max_digits=18, decimal_places=3, null=True, blank=True)
    notes = models.TextField("备注", blank=True)
    source_oa_number = models.CharField("来源OA流水号", max_length=40, blank=True)
    source_sha256 = models.CharField("来源OA文件摘要", max_length=64)
    source_position = models.PositiveIntegerField("来源出库行号")

    class Meta:
        ordering = ["position", "id"]
        constraints = [models.UniqueConstraint(fields=["contract", "source_sha256", "source_position"], name="unique_outbound_oa_row")]


class Shipment(models.Model):
    contract = models.ForeignKey(Contract, on_delete=models.CASCADE, related_name="shipments")
    shipped_on = models.DateField("实际发货日期")
    amount = models.DecimalField("本次发货金额(原币)", max_digits=18, decimal_places=2, null=True, blank=True)
    tracking = models.CharField("物流及单号", max_length=255, blank=True)
    notes = models.TextField(blank=True)


class Receipt(models.Model):
    contract = models.ForeignKey(Contract, on_delete=models.CASCADE, related_name="receipts")
    received_on = models.DateField("收款日期", null=True, blank=True)
    currency = models.CharField("收款币种", max_length=20, blank=True)
    amount = models.DecimalField("收款金额(原币)", max_digits=18, decimal_places=2, null=True, blank=True)
    exchange_rate = models.DecimalField("结算汇率", max_digits=18, decimal_places=6, null=True, blank=True)
    notes = models.TextField(blank=True)


class AuditEvent(models.Model):
    contract = models.ForeignKey(Contract, on_delete=models.CASCADE, related_name="events")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    action = models.CharField(max_length=50)
    detail = models.TextField(blank=True)
    at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-at"]
