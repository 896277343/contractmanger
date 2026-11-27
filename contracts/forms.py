from django import forms
from django.utils.html import format_html, format_html_join
from django.templatetags.static import static
from calendar import monthrange
from datetime import date

from .models import Contract, LineItem, Material, Receipt
from .geography import COUNTRIES, find_country


OPTIONS = {
    "business_type": ["内销", "外销"],
    "customer_category": ["最终用户", "经销商", "授权代理商", "EPC/系统集成商", "同行/OEM", "科研院所/高校", "其他"],
    "inquiry_source": ["展会", "老客户转介绍", "客户主动询价", "自行开发", "其他", "百度", "谷歌", "站群"],
    "product_category": ["泄漏仪表", "分析仪表", "回收车", "传感器", "回收管与接头", "氢气设备", "其他"],
    "product_subcategory": ["便携仪表", "固定仪表", "便携分析仪", "固定分析仪", "小型回收车", "中型回收车", "大型回收车", "真空泵", "新型绝缘气体设备", "气体再生回收车", "小型充气抽真空推车"],
    "currency": ["人民币", "美元", "欧元", "美金"],
    "industry": ["电力与电气", "石油化工", "冶金钢铁", "环保水务", "电子制造", "科研院所及高校", "新能源", "其他"],
    "payment_method": ["阿里", "公账", "私账"],
    "progress": ["生产中", "生产完成", "已完成"],
}


def choice_field(model_field, values, current=""):
    choices = [("", "---------")] + [(value, value) for value in values]
    if current and current not in values:
        choices.append((current, f"{current}（已有值）"))
    return forms.ChoiceField(label=model_field.verbose_name, choices=choices, required=False)


def three_months_after(value: date) -> date:
    month_index = value.year * 12 + value.month - 1 + 3
    year, month_zero = divmod(month_index, 12)
    month = month_zero + 1
    return date(year, month, min(value.day, monthrange(year, month)[1]))


class CountryCodeInput(forms.TextInput):
    def render(self, name, value, attrs=None, renderer=None):
        attrs = {**(attrs or {}), "autocomplete": "off", "placeholder": "输入国家拼音、英文名或 US 等代码", "data-country-source": static("contracts/geography.json")}
        widget = super().render(name, value, attrs, renderer)
        return format_html('{}<div id="country-suggestions" role="listbox"></div><span id="country-continent-preview" class="help"></span>', widget)


class ContractAdminForm(forms.ModelForm):
    class Meta:
        model = Contract
        fields = "__all__"
        exclude = ("status",)
        widgets = {"country": CountryCodeInput}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ("business_type", "customer_category", "inquiry_source", "currency", "industry", "payment_method", "progress"):
            current = getattr(self.instance, name, "")
            if name == "customer_category" and current == "自行开发":
                current = ""
            self.fields[name] = choice_field(Contract._meta.get_field(name), OPTIONS[name], current)
        for name in ("oa_number", "signed_on", "contract_number", "customer_category", "inquiry_source", "industry", "payment_method", "progress", "country", "agreed_ship_on", "amount"):
            self.fields[name].required = True
        self.fields["customs_declared"].required = True
        self.fields["country"].help_text = "输入国家拼音、英文名称或两位代码（如 taiguo、Thailand、TH），选中后填写英文国名。"
        self.fields["business_type"].help_text = "默认外销（正式报关）；内销业务请自行改选。"
        self.fields["payment_method"].help_text = "默认公账；通过阿里或私账收款时请改选对应方式。"
        self.fields["customer_category"].help_text = "默认最终用户；经销商、代理商等请按实际情况改选。"
        self.fields["amount"].help_text = "必填。按合同填写原币金额，不要根据 OA 供货物料数量推算。"
        self.fields["progress"].help_text = "默认生产中；生产完成或合同完成后请更新。"
        self.fields["agreed_ship_on"].help_text = "默认合同签订日期后三个自然月；可按合同约定修改。"
        self.fields["customs_declared"].help_text = "外销默认是；内销默认否，可按实际报关情况修改。"
        if not self.is_bound:
            for name, default in (("business_type", "外销"), ("payment_method", "公账"), ("customer_category", "最终用户"), ("progress", "生产中"), ("industry", "电力与电气"), ("inquiry_source", "谷歌")):
                if not getattr(self.instance, name, None):
                    self.initial[name] = default
            if not self.instance.agreed_ship_on and self.instance.signed_on:
                self.initial["agreed_ship_on"] = three_months_after(self.instance.signed_on)
            if self.instance.customs_declared is None:
                self.initial["customs_declared"] = (self.instance.business_type or "外销") == "外销"
        self.fields["agreed_ship_on"].widget.attrs["data-saved-value"] = "1" if self.instance.agreed_ship_on else "0"
        self.fields["customs_declared"].widget.attrs["data-saved-value"] = "1" if self.instance.customs_declared is not None else "0"
        self.fields["industry"].help_text = "默认电力与电气；其他行业请按客户实际情况改选。"
        self.fields["inquiry_source"].help_text = "默认谷歌；自行开发、展会、介绍或其他渠道请改选。"

    def clean_customs_declared(self):
        value = self.cleaned_data["customs_declared"]
        if value is None:
            raise forms.ValidationError("请选择是否报关。")
        return value

    def clean_country(self):
        value = self.cleaned_data["country"].strip()
        if not value:
            return value
        if value == self.instance.country:
            return value
        code = find_country(value)
        if not code:
            raise forms.ValidationError("请选择有效国家；可输入拼音、英文名称或两位代码。")
        return COUNTRIES[code]["name"]


class LineItemAdminForm(forms.ModelForm):
    class Meta:
        model = LineItem
        fields = "__all__"
        widgets = {"notes": forms.Textarea(attrs={"rows": 2, "cols": 20, "style": "width:160px;max-width:160px;box-sizing:border-box"})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ("product_category", "product_subcategory"):
            self.fields[name] = choice_field(LineItem._meta.get_field(name), OPTIONS[name], getattr(self.instance, name, ""))
            self.fields[name].required = True
        self.fields["model"].required = True


class MaterialAdminForm(forms.ModelForm):
    class Meta:
        model = Material
        fields = "__all__"
        widgets = {
            "technical_parameters": forms.Textarea(attrs={"rows": 2, "cols": 20, "style": "width:160px;max-width:160px;box-sizing:border-box"}),
            "supply_requirement": forms.Textarea(attrs={"rows": 2, "cols": 20, "style": "width:160px;max-width:160px;box-sizing:border-box"}),
        }


class ReceiptAdminForm(forms.ModelForm):
    class Meta:
        model = Receipt
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["currency"] = choice_field(Receipt._meta.get_field("currency"), OPTIONS["currency"], getattr(self.instance, "currency", ""))
        self.fields["currency"].required = False
