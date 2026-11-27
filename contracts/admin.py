from django.contrib import admin
from .forms import ContractAdminForm, LineItemAdminForm, MaterialAdminForm, OutboundItemAdminForm, ReceiptAdminForm
from .models import AuditEvent, Contract, LineItem, Material, OAFlow, OutboundItem, Receipt, Shipment


def field_changes(instance, fields, old=None):
    changes = []
    for name in fields:
        field = instance._meta.get_field(name)
        before = getattr(old, name) if old is not None else None
        after = getattr(instance, name)
        if old is None or before != after:
            changes.append(f"{field.verbose_name}：{before if before not in (None, '') else '（空）'} → {after if after not in (None, '') else '（空）'}")
    return "；".join(changes)


class LineInline(admin.TabularInline):
    model = LineItem
    form = LineItemAdminForm
    extra = 0


class MaterialInline(admin.TabularInline):
    model = Material
    form = MaterialAdminForm
    extra = 0
    readonly_fields = ("source_oa_number", "source_sha256", "source_position")


class OutboundInline(admin.TabularInline):
    model = OutboundItem
    form = OutboundItemAdminForm
    extra = 0
    readonly_fields = ("source_oa_number", "source_sha256", "source_position")


class ShipmentInline(admin.TabularInline):
    model = Shipment
    extra = 0


class ReceiptInline(admin.TabularInline):
    model = Receipt
    form = ReceiptAdminForm
    extra = 0


class OAFlowInline(admin.TabularInline):
    model = OAFlow
    extra = 0
    readonly_fields = ("source", "created_at")


class AuditEventInline(admin.TabularInline):
    model = AuditEvent
    extra = 0
    fields = ("at", "actor", "action", "detail")
    readonly_fields = fields
    can_delete = False
    show_change_link = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Contract)
class ContractAdmin(admin.ModelAdmin):
    form = ContractAdminForm
    class Media:
        js = ("contracts/country.js", "contracts/contract_defaults.js")
        css = {"all": ("contracts/admin_fields.css",)}
    readonly_fields = ("continent",)
    list_display = ("contract_number", "oa_numbers_display", "customer_name", "salesperson", "signed_on", "country", "continent", "currency", "amount")
    list_filter = ("business_type", "currency", "country", "signed_on")
    search_fields = ("contract_number", "oa_number", "oa_flows__number", "customer_name", "salesperson__username")
    inlines = [OAFlowInline, LineInline, MaterialInline, OutboundInline, ShipmentInline, ReceiptInline, AuditEventInline]
    actions = None

    def get_fields(self, request, obj=None):
        fields = list(super().get_fields(request, obj))
        if "continent" in fields:
            fields.remove("continent")
            fields.insert(fields.index("country") + 1, "continent")
        return fields

    def get_readonly_fields(self, request, obj=None):
        fields = list(super().get_readonly_fields(request, obj))
        if obj and not (request.user.is_superuser or request.user.groups.filter(name="信息处理员").exists()):
            fields.append("salesperson")
        return fields

    def save_formset(self, request, form, formset, change):
        model = formset.model
        changes = []
        for row_form in formset.forms:
            if not row_form.is_valid() or not row_form.has_changed():
                continue
            instance = row_form.instance
            old = model.objects.filter(pk=instance.pk).first() if instance.pk else None
            if row_form.cleaned_data.get("DELETE"):
                if old:
                    fields = [f.name for f in model._meta.fields if f.name not in ("id", "contract")]
                    changes.append(("删除", f"{model._meta.verbose_name} #{old.pk}：" + field_changes(old, fields)))
            else:
                fields = [name for name in row_form.changed_data if name != "DELETE"]
                detail = field_changes(instance, fields, old)
                if detail:
                    changes.append(("修改" if old else "新增", f"{model._meta.verbose_name} #{old.pk if old else '新'}：{detail}"))
        super().save_formset(request, form, formset, change)
        for action, detail in changes:
            AuditEvent.objects.create(contract=form.instance, actor=request.user, action=f"{model._meta.verbose_name}{action}", detail=detail)

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        return queryset if request.user.is_superuser or request.user.groups.filter(name__in=["信息处理员", "领导"]).exists() else queryset.filter(salesperson=request.user)

    def has_change_permission(self, request, obj=None):
        if not super().has_change_permission(request, obj):
            return False
        return obj is None or request.user.is_superuser or obj.salesperson_id == request.user.id or request.user.groups.filter(name="信息处理员").exists()

    def has_view_permission(self, request, obj=None):
        if not super().has_view_permission(request, obj):
            return False
        return obj is None or request.user.is_superuser or obj.salesperson_id == request.user.id or request.user.groups.filter(name__in=["信息处理员", "领导"]).exists()

    def save_model(self, request, obj, form, change):
        old = Contract.objects.filter(pk=obj.pk).first() if change else None
        if not change and not (request.user.is_superuser or request.user.groups.filter(name="信息处理员").exists()):
            obj.salesperson = request.user
        super().save_model(request, obj, form, change)
        if obj.oa_number:
            OAFlow.objects.get_or_create(contract=obj, number=obj.oa_number, defaults={"source": "后台录入"})
        fields = [name for name in form.changed_data if name != "salesperson" or not change]
        detail = field_changes(obj, fields, old)
        if not change or detail:
            AuditEvent.objects.create(contract=obj, actor=request.user, action="合同修改" if change else "合同新建", detail=detail)

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(AuditEvent)
class AuditEventAdmin(admin.ModelAdmin):
    list_display = ("contract", "actor", "action", "detail", "at")
    readonly_fields = ("contract", "actor", "action", "detail", "at")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        return queryset if request.user.is_superuser or request.user.groups.filter(name__in=["信息处理员", "领导"]).exists() else queryset.filter(contract__salesperson=request.user)
