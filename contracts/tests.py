from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib import admin
from django.core.files.uploadedfile import SimpleUploadedFile
from django.forms.models import inlineformset_factory
from django.test import RequestFactory
from django.test import Client
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from pathlib import Path
import json
from unittest import skipUnless
from unittest.mock import patch
from io import BytesIO
from zipfile import ZipFile
from decimal import Decimal
from datetime import timedelta
from types import SimpleNamespace
from openpyxl import load_workbook
from .models import AuditEvent, Contract, LineItem, Material, OAFlow, OutboundItem, Receipt, Shipment
from .admin import ContractAdmin
from .currency import conversion
from .parser import parse_oa
from .excel_import import flow_numbers, parse_workbook
from .workflow_import import contract_candidates, parse_workflow_xls
from .oa_client import describe_oa_page, fetch_oa_html


SAMPLE = '''<script>var g_run_id = "12345";</script><body>流水号:12345
<input type="hidden" title="合同号" value="ZY-001">
<input type="hidden" title="合同执行任务单智仪V1.0.签订单位" value="Example Ltd">
<table id="LV_33"><tr><td>序号</td></tr><tr><td>1</td><td>Meter</td><td>M-1</td><td>2</td><td>pcs</td><td></td><td></td><td>人民币</td><td></td></tr></table></body>'''.encode("utf-8")


class WorkflowImportTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser("administrator", password="safe-password-123")
        self.owner = get_user_model().objects.create_user("sales-one", password="safe-password-123", first_name="张", last_name="三")
        self.other = get_user_model().objects.create_user("sales-two", password="safe-password-123", first_name="李", last_name="四")
        group = Group.objects.get(name="业务员")
        self.owner.groups.add(group)
        self.other.groups.add(group)
        self.client.force_login(self.admin)

    def test_contract_candidates_reject_multiple_distinct_numbers(self):
        self.assertEqual(contract_candidates("OA 299945 合同 ZY-F04-261113-031 (印度)"), ["ZY-F04-261113-031"])
        self.assertEqual(contract_candidates("甲方F11-260525-01 乙方F11-260525-01"), ["F11-260525-01"])
        self.assertEqual(len(contract_candidates("R26054, R26055")), 2)

    def test_preview_confirm_and_repeat_keep_one_contract_per_number(self):
        rows = [
            {"line": 2, "oa_number": "299945", "title": "合同 F11-260525-01", "initiator": "张三", "contracts": ["F11-260525-01"]},
            {"line": 3, "oa_number": "299946", "title": "加单 F11-260525-01", "initiator": "张三", "contracts": ["F11-260525-01"]},
            {"line": 4, "oa_number": "299947", "title": "R26054, R26055", "initiator": "张三", "contracts": ["R26054", "R26055"]},
        ]
        parsed = {"sheet": "工作流工作查询", "rows": rows, "initiators": ["张三"]}
        upload = {"stage": "preview", "file": SimpleUploadedFile("workflows.xls", b"data")}
        with patch("contracts.views.parse_workflow_xls", return_value=parsed):
            response = self.client.post(reverse("import_workflows"), upload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["initiators"][0]["default_id"], self.owner.pk)
        response = self.client.post(reverse("import_workflows"), {"stage": "confirm", "owner_0": str(self.owner.pk), "confirmed": "yes"})
        self.assertEqual(response.status_code, 302)
        contract = Contract.objects.get(contract_number="F11-260525-01")
        self.assertEqual(contract.salesperson, self.owner)
        self.assertEqual(contract.oa_flows.count(), 2)
        self.assertIsNone(contract.amount)
        self.assertEqual(contract.items.count(), 0)
        with patch("contracts.views.parse_workflow_xls", return_value=parsed):
            self.client.post(reverse("import_workflows"), {"stage": "preview", "file": SimpleUploadedFile("workflows.xls", b"data")})
        self.client.post(reverse("import_workflows"), {"stage": "confirm", "owner_0": str(self.owner.pk), "confirmed": "yes"})
        self.assertEqual(Contract.objects.count(), 1)
        self.assertEqual(OAFlow.objects.count(), 2)

    def test_existing_owner_conflict_is_not_reassigned(self):
        Contract.objects.create(contract_number="F11-260525-01", salesperson=self.other)
        parsed = {"sheet": "工作流", "initiators": ["张三"], "rows": [{"line": 2, "oa_number": "299945", "title": "F11-260525-01", "initiator": "张三", "contracts": ["F11-260525-01"]}]}
        with patch("contracts.views.parse_workflow_xls", return_value=parsed):
            self.client.post(reverse("import_workflows"), {"stage": "preview", "file": SimpleUploadedFile("workflows.xls", b"data")})
        self.client.post(reverse("import_workflows"), {"stage": "confirm", "owner_0": str(self.owner.pk), "confirmed": "yes"})
        self.assertEqual(Contract.objects.get().salesperson, self.other)
        self.assertEqual(OAFlow.objects.count(), 0)

    def test_salesperson_cannot_open_batch_import(self):
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(reverse("import_workflows")).status_code, 403)

    def test_admin_is_not_listed_as_salesperson_filter(self):
        Contract.objects.create(contract_number="ADMIN-1", salesperson=self.admin)
        Contract.objects.create(contract_number="SALES-1", salesperson=self.owner)
        response = self.client.get(reverse("dashboard"))
        self.assertNotIn(f"salesperson:{self.admin.pk}", [person["scope"] for person in response.context["salespeople"]])
        self.assertIn(f"salesperson:{self.owner.pk}", [person["scope"] for person in response.context["salespeople"]])


class ImportTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("sales", password="safe-password-123")
        self.client.force_login(self.user)

    def test_import_and_duplicate(self):
        upload = lambda: self.client.post(reverse("upload"), {"file": SimpleUploadedFile("oa.html", SAMPLE, content_type="text/html")})
        response = upload()
        self.assertEqual(response.status_code, 302)
        self.assertIn("q=ZY-001", response.url)
        contract = Contract.objects.get()
        self.assertEqual(contract.oa_number, "12345")
        self.assertEqual(contract.items.count(), 1)
        self.assertEqual(contract.items.get().product_name, "Meter")
        self.assertIsNone(contract.amount)
        upload()
        self.assertEqual(Contract.objects.count(), 1)
        self.assertEqual(OAFlow.objects.count(), 1)
        self.assertEqual(contract.items.count(), 1)

    def test_zip_import_reads_html_and_ignores_attachments(self):
        buffer = BytesIO()
        with ZipFile(buffer, "w") as archive:
            archive.writestr("oa.html", SAMPLE)
            archive.writestr("files/photo.jpg", b"attachment")
        response = self.client.post(reverse("upload"), {"file": SimpleUploadedFile("oa.zip", buffer.getvalue())})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Contract.objects.get().contract_number, "ZY-001")

    @patch("contracts.views.fetch_oa_html")
    def test_online_oa_import_uses_current_salesperson_and_deduplicates(self, fetch):
        fetch.return_value = SAMPLE
        payload = {"oa_username": "my-oa-account", "oa_password": "temporary-secret", "run_id": "12345"}
        first = self.client.post(reverse("import_oa_online"), payload)
        self.assertEqual(first.status_code, 302)
        self.assertEqual(Contract.objects.get().salesperson, self.user)
        self.assertEqual(OAFlow.objects.count(), 1)
        self.client.post(reverse("import_oa_online"), payload)
        self.assertEqual(Contract.objects.count(), 1)
        self.assertEqual(OAFlow.objects.count(), 1)
        self.assertNotIn("temporary-secret", str(dict(self.client.session)))
        fetch.assert_called_with("my-oa-account", "temporary-secret", "12345", "http://192.168.1.80:8080")

    @patch("contracts.views.fetch_oa_html")
    def test_online_oa_cannot_change_another_salesperson_contract(self, fetch):
        other = get_user_model().objects.create_user("other-owner", password="safe-password-123")
        Contract.objects.create(contract_number="ZY-001", salesperson=other)
        fetch.return_value = SAMPLE
        response = self.client.post(reverse("import_oa_online"), {"oa_username": "user", "oa_password": "secret", "run_id": "12345"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Contract.objects.get().salesperson, other)
        self.assertEqual(OAFlow.objects.count(), 0)

    @patch("contracts.oa_client._get")
    @patch("contracts.oa_client._encrypt_password", return_value="encrypted")
    def test_online_oa_reports_login_failure_instead_of_contract_format(self, encrypt, get_page):
        get_page.side_effect = [b"login page", "错误 用户名或密码错误".encode(), "用户未登录，请重新登录".encode()]
        with self.assertRaisesMessage(ValueError, "OA 登录未成功"):
            fetch_oa_html("account", "secret", "298175")
        self.assertEqual(get_page.call_count, 3)

    def test_print_page_hidden_contract_number_is_recognized(self):
        page = b'<html><head><title>Print</title></head><body><script>var g_run_id = "298175";</script><input type="hidden" title="\xe5\x90\x88\xe5\x90\x8c\xe5\x8f\xb7" value="R26063"></body></html>'
        self.assertEqual(parse_oa(page)["contract_number"], "R26063")
        self.assertIn("页面流水号 298175", describe_oa_page(page))

    def test_placeholder_internal_number_uses_customer_number_and_merges_lines(self):
        existing = Contract.objects.create(contract_number="F09-261012-01", salesperson=self.user)
        first = LineItem.objects.create(contract=existing, position=1, product_name="旧商品", model="OLD", quantity=1, product_category="保留分类")
        LineItem.objects.create(contract=existing, position=5, product_name="额外行")
        html = SAMPLE.replace(b'ZY-001', b'/').replace(b'</body>', '<input type="hidden" title="甲方合同号" value="F09-261012-01"></body>'.encode())
        parsed = parse_oa(html)
        self.assertEqual(parsed["contract_number"], "F09-261012-01")
        self.assertEqual(parsed["customer_contract_number"], "F09-261012-01")
        self.client.post(reverse("upload"), {"file": SimpleUploadedFile("oa.html", html)})
        first.refresh_from_db()
        self.assertEqual(first.product_name, "Meter")
        self.assertEqual(first.model, "M-1")
        self.assertEqual(first.product_category, "保留分类")
        self.assertEqual(existing.items.get(position=5).product_name, "额外行")
        self.assertFalse(Contract.objects.filter(contract_number="/").exists())

    def test_placeholder_contract_number_without_fallback_is_rejected(self):
        html = SAMPLE.replace(b'ZY-001', b'/')
        with self.assertRaisesMessage(ValueError, "没有找到有效的"):
            parse_oa(html)

    def test_desktop_api_rejects_placeholder_contract_number(self):
        data = parse_oa(SAMPLE)
        data["contract_number"] = "/"
        data["currency"] = "人民币"
        response = self.client.post(reverse("desktop_sync"), data=json.dumps(data), content_type="application/json")
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Contract.objects.exists())

    def test_oa_contract_number_alias_start_date_and_line_merge(self):
        contract = Contract.objects.create(contract_number="ZY-001", salesperson=self.user)
        first = LineItem.objects.create(contract=contract, position=1, product_name="旧商品", model="OLD", quantity=1, notes="业务备注", product_category="分析仪", supplier="厂家A", purchase_price=Decimal("8"))
        LineItem.objects.create(contract=contract, position=3, product_name="保留的额外行")
        html = SAMPLE.replace('title="合同号"'.encode(), 'title="日立信合同号"'.encode()).replace(b'</body>', '<table class="TableList"><tr><td>流程开始（流水号：12345）</td></tr><tr><td>第1步</td><td>序号1：发起人</td><td>开始于：2026-11-05 15:34:20</td></tr></table></body>'.encode())
        parsed = parse_oa(html)
        self.assertEqual(parsed["contract_number"], "ZY-001")
        self.assertEqual(parsed["signed_on"], "2026-11-05")
        self.client.post(reverse("upload"), {"file": SimpleUploadedFile("oa.html", html)})
        contract.refresh_from_db()
        first.refresh_from_db()
        self.assertEqual(str(contract.signed_on), "2026-11-05")
        self.assertEqual(first.product_name, "Meter")
        self.assertEqual(first.model, "M-1")
        self.assertEqual(first.quantity, Decimal("2"))
        self.assertEqual(first.notes, "业务备注")
        self.assertEqual(first.product_category, "分析仪")
        self.assertEqual(first.supplier, "厂家A")
        self.assertEqual(first.purchase_price, Decimal("8"))
        self.assertEqual(contract.items.count(), 2)
        self.assertEqual(contract.items.get(position=3).product_name, "保留的额外行")
        self.client.post(reverse("upload"), {"file": SimpleUploadedFile("oa.html", html)})
        self.assertEqual(contract.items.count(), 2)

    def test_desktop_sync_uses_logged_in_owner_and_is_idempotent(self):
        client = Client(enforce_csrf_checks=True)
        token = client.get(reverse("desktop_csrf")).json()["csrf_token"]
        login_response = client.post(reverse("desktop_login"), {"username": "sales", "password": "safe-password-123"}, HTTP_X_CSRFTOKEN=token)
        self.assertEqual(login_response.status_code, 200)
        token = login_response.json()["csrf_token"]
        data = parse_oa(SAMPLE)
        data["currency"] = "人民币"
        data["materials"] = [{"position": 1, "code": "15100153", "name": "仪表", "specification": "M-1", "quantity": "1", "unit": "台", "technical_parameters": "", "supply_requirement": "", "stock_status": "", "production_order": ""}]
        payload = json.dumps(data)
        self.assertEqual(client.post(reverse("desktop_sync"), data=payload, content_type="application/json").status_code, 403)
        first = client.post(reverse("desktop_sync"), data=payload, content_type="application/json", HTTP_X_CSRFTOKEN=token)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()["materials_added"], 1)
        self.assertEqual(first.json()["lines_created"], 1)
        self.assertEqual(Contract.objects.get().salesperson, self.user)
        second = client.post(reverse("desktop_sync"), data=payload, content_type="application/json", HTTP_X_CSRFTOKEN=token)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json()["materials_added"], 0)
        self.assertEqual(second.json()["lines_updated"], 0)
        self.assertEqual(Material.objects.count(), 1)
        self.assertEqual(LineItem.objects.count(), 1)
        self.assertEqual(AuditEvent.objects.filter(action="OA桌面同步").count(), 1)

    def test_desktop_login_over_https_requires_matching_origin(self):
        from django.test import override_settings
        with override_settings(ALLOWED_HOSTS=["contracts.68988008.xyz"]):
            client = Client(enforce_csrf_checks=True)
            token = client.get(reverse("desktop_csrf"), secure=True, HTTP_HOST="contracts.68988008.xyz").json()["csrf_token"]
            response = client.post(reverse("desktop_login"), {"username": "sales", "password": "safe-password-123"}, secure=True, HTTP_HOST="contracts.68988008.xyz", HTTP_X_CSRFTOKEN=token)
            self.assertEqual(response.status_code, 403)
            response = client.post(reverse("desktop_login"), {"username": "sales", "password": "safe-password-123"}, secure=True, HTTP_HOST="contracts.68988008.xyz", HTTP_X_CSRFTOKEN=token, HTTP_ORIGIN="https://contracts.68988008.xyz")
            self.assertEqual(response.status_code, 200)

    def test_desktop_sync_does_not_take_over_another_owner_contract(self):
        other = get_user_model().objects.create_user("other", password="safe-password-123")
        Contract.objects.create(contract_number="ZY-001", salesperson=other)
        data = parse_oa(SAMPLE)
        data["currency"] = "人民币"
        response = self.client.post(reverse("desktop_sync"), data=json.dumps(data), content_type="application/json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Contract.objects.get().salesperson, other)
        self.assertEqual(Material.objects.count(), 0)

    def test_admin_desktop_sync_assigns_new_contract_by_oa_signer(self):
        admin = get_user_model().objects.create_superuser("administrator", password="safe-password-123")
        self.user.first_name = "程"
        self.user.last_name = "耐红"
        self.user.save()
        self.user.groups.add(Group.objects.get(name="业务员"))
        self.client.force_login(admin)
        data = parse_oa(SAMPLE)
        data["oa_signer"] = "程耐红"
        data["currency"] = "人民币"
        preview = self.client.post(reverse("desktop_resolve_owner"), data=json.dumps(data), content_type="application/json")
        self.assertEqual(preview.json()["owner"], self.user.get_full_name())
        response = self.client.post(reverse("desktop_sync"), data=json.dumps(data), content_type="application/json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Contract.objects.get().salesperson, self.user)
        self.assertEqual(AuditEvent.objects.get(action="OA桌面同步").actor, admin)

    def test_admin_desktop_sync_keeps_existing_owner_and_rejects_unknown_signer(self):
        admin = get_user_model().objects.create_superuser("administrator", password="safe-password-123")
        self.client.force_login(admin)
        data = parse_oa(SAMPLE)
        data["oa_signer"] = "没有对应账号"
        data["currency"] = "人民币"
        rejected = self.client.post(reverse("desktop_sync"), data=json.dumps(data), content_type="application/json")
        self.assertEqual(rejected.status_code, 400)
        self.assertEqual(Contract.objects.count(), 0)
        existing = Contract.objects.create(contract_number="ZY-001", salesperson=self.user)
        preview = self.client.post(reverse("desktop_resolve_owner"), data=json.dumps(data), content_type="application/json")
        self.assertTrue(preview.json()["existing"])
        accepted = self.client.post(reverse("desktop_sync"), data=json.dumps(data), content_type="application/json")
        self.assertEqual(accepted.status_code, 200)
        existing.refresh_from_db()
        self.assertEqual(existing.salesperson, self.user)

    @patch("contracts.oa_client._get")
    @patch("contracts.oa_client._encrypt_password", return_value="encrypted")
    def test_online_oa_follows_original_form_iframe(self, encrypt, get_page):
        wrapper = b'<iframe id="print_frm" src="../print.php?RUN_ID=298175&amp;FLOW_ID=199"></iframe>'
        form = '<script>var g_run_id = "298175";</script><input title="合同号" value="R26063">'.encode()
        get_page.side_effect = [b"login page", b"logged in", wrapper, form]
        result = fetch_oa_html("account", "secret", "298175")
        self.assertEqual(parse_oa(result)["contract_number"], "R26063")
        self.assertIn("/general/approve_center/list/print.php?RUN_ID=298175", get_page.call_args.args[1])

    @patch("contracts.oa_client._get")
    @patch("contracts.oa_client._encrypt_password", return_value="encrypted")
    def test_online_oa_rejects_mismatched_iframe_run_id(self, encrypt, get_page):
        wrapper = b'<iframe id="print_frm" src="../print.php?RUN_ID=999"></iframe>'
        get_page.side_effect = [b"login page", b"logged in", wrapper]
        with self.assertRaisesMessage(ValueError, "流水号与输入不一致"):
            fetch_oa_html("account", "secret", "298175")

    def test_zip_with_multiple_html_files_is_rejected(self):
        buffer = BytesIO()
        with ZipFile(buffer, "w") as archive:
            archive.writestr("first.html", SAMPLE)
            archive.writestr("second.html", SAMPLE)
        response = self.client.post(reverse("upload"), {"file": SimpleUploadedFile("oa.zip", buffer.getvalue())})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Contract.objects.count(), 0)

    def test_one_contract_can_have_multiple_oa_flows(self):
        self.client.post(reverse("upload"), {"file": SimpleUploadedFile("first.html", SAMPLE)})
        second = SAMPLE.replace(b"12345", b"67890")
        self.client.post(reverse("upload"), {"file": SimpleUploadedFile("second.html", second)})
        self.assertEqual(Contract.objects.count(), 1)
        self.assertEqual(Contract.objects.get().items.count(), 1)
        self.assertEqual(set(OAFlow.objects.values_list("number", flat=True)), {"12345", "67890"})

    def test_one_oa_flow_can_cover_multiple_contracts(self):
        self.client.post(reverse("upload"), {"file": SimpleUploadedFile("first.html", SAMPLE)})
        second = SAMPLE.replace(b"ZY-001", b"ZY-002")
        self.client.post(reverse("upload"), {"file": SimpleUploadedFile("second.html", second)})
        self.assertEqual(Contract.objects.count(), 2)
        self.assertEqual(OAFlow.objects.filter(number="12345").count(), 2)

    def test_split_multiple_oa_numbers_in_one_cell(self):
        self.assertEqual(flow_numbers("271112, 289881"), ["271112", "289881"])

    def test_salesperson_cannot_export(self):
        self.assertEqual(self.client.get(reverse("export")).status_code, 403)

    def test_sample_parser(self):
        self.assertEqual(parse_oa(SAMPLE)["contract_number"], "ZY-001")

    def test_oa_outbound_rows_are_imported_once(self):
        oa = SAMPLE.replace(b"</body>", b'<table id="LV_86"><tr><td>header</td></tr><tr><td>1</td><td>OUT-1</td><td>FLOW-1</td><td>MAT-1</td><td>Sensor</td><td>S-1</td><td>pcs</td><td>2</td><td>note</td></tr></table></body>')
        def upload():
            return self.client.post(reverse("upload"), {"file": SimpleUploadedFile("oa.html", oa)})
        upload()
        self.assertEqual(OutboundItem.objects.get().material_code, "MAT-1")
        upload()
        self.assertEqual(OutboundItem.objects.count(), 1)

    def test_dashboard_renders_after_import(self):
        self.client.post(reverse("upload"), {"file": SimpleUploadedFile("oa.html", SAMPLE, content_type="text/html")})
        self.assertEqual(self.client.get(reverse("dashboard")).status_code, 200)
        self.assertIn("?v=", self.client.get(reverse("dashboard")).content.decode())
        result = self.client.get(reverse("dashboard"), {"q": "ZY-001", "year": "2026"})
        self.assertEqual(result.context["count"], 0)
        self.assertContains(result, "ZY-001")
        self.assertContains(result, "未计入签订业绩")

    def test_salesperson_cannot_see_other_salesperson_data(self):
        other = get_user_model().objects.create_user("other", password="safe-password-123")
        today = timezone.localdate()
        Contract.objects.create(contract_number="OWN-2026", salesperson=self.user, signed_on=today, amount=Decimal("100"), currency="美元", country="美国")
        Contract.objects.create(contract_number="OTHER-2026", salesperson=other, signed_on=today, amount=Decimal("200"), currency="美元", country="法国")
        response = self.client.get(reverse("dashboard"))
        self.assertContains(response, "结构分布")
        self.assertContains(response, 'class="contract-row" data-href="/admin/contracts/contract/')
        self.assertContains(response, 'class="customer-clip"')
        self.assertNotContains(response, "业务员业绩榜")
        self.assertNotIn("sales_ranking", response.context)
        self.assertEqual(response.context["count"], 1)
        self.assertNotContains(response, "OTHER-2026")
        self.assertNotContains(response, "法国")

    def test_oa_supplements_materials_and_existing_product_fields(self):
        contract = Contract.objects.create(contract_number="ZY-001", salesperson=self.user, customer_name="Excel customer")
        LineItem.objects.create(contract=contract, position=1, model="OLD-1", quantity=1)
        oa = SAMPLE.replace(b"</body>", b'<table id="LV_28"><tr><td>header</td></tr><tr><td>1</td><td>MAT-01</td><td>Sensor</td><td>S-1</td><td>2</td><td>pcs</td><td>standard</td><td>buy</td><td>none</td><td>PO-1</td></tr></table></body>')
        def upload():
            return self.client.post(reverse("upload"), {"file": SimpleUploadedFile("oa.html", oa)})
        upload()
        contract.refresh_from_db()
        self.assertEqual(contract.customer_name, "Excel customer")
        self.assertEqual(contract.items.count(), 1)
        self.assertEqual(contract.items.get().model, "M-1")
        self.assertEqual(contract.items.get().quantity, Decimal("2"))
        self.assertEqual(contract.materials.count(), 1)
        self.assertEqual(Material.objects.get().code, "MAT-01")
        self.assertIn("MAT-01", AuditEvent.objects.filter(contract=contract, action="补充OA详情").first().detail)
        upload()
        self.assertEqual(contract.items.count(), 1)
        self.assertEqual(contract.materials.count(), 1)
        repeat_log = AuditEvent.objects.filter(contract=contract, action="补充OA详情").first().detail
        self.assertIn("识别供货物料 1 条，本次新增 0 条", repeat_log)
        self.assertIn("编码 MAT-01", repeat_log)
        self.assertIn("规格 S-1", repeat_log)


class UpdateLogTests(TestCase):
    def test_salesperson_can_view_oa_material_in_contract_admin(self):
        user = get_user_model().objects.create_user("material_sales", password="safe-password-123", is_staff=True)
        user.groups.add(Group.objects.get(name="业务员"))
        contract = Contract.objects.create(contract_number="MAT-VIEW-1", salesperson=user)
        Material.objects.create(contract=contract, code="1102023570", name="Connector", source_sha256="source", source_position=1)
        self.client.force_login(user)
        response = self.client.get(f"/admin/contracts/contract/{contract.pk}/change/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "1102023570")

    def test_manual_field_change_and_visibility(self):
        user = get_user_model().objects.create_user("sales_log", password="safe-password-123", is_staff=True)
        other = get_user_model().objects.create_user("other_log", password="safe-password-123", is_staff=True)
        contract = Contract.objects.create(contract_number="LOG-1", salesperson=user, customer_name="Before")
        another = Contract.objects.create(contract_number="LOG-2", salesperson=other)
        AuditEvent.objects.create(contract=another, actor=other, action="合同修改", detail="其他人的记录")
        contract.customer_name = "After"
        request = RequestFactory().post("/admin/contracts/contract/1/change/")
        request.user = user
        ContractAdmin(Contract, admin.site).save_model(request, contract, SimpleNamespace(changed_data=["customer_name"]), True)
        self.assertIn("Before → After", AuditEvent.objects.get(contract=contract).detail)
        self.client.force_login(user)
        response = self.client.get(reverse("update_log"))
        self.assertContains(response, "LOG-1")
        self.assertNotContains(response, "LOG-2")

    def test_inline_change_is_recorded_with_before_and_after(self):
        user = get_user_model().objects.create_superuser("audit_admin", "audit@example.com", "safe-password-123")
        contract = Contract.objects.create(contract_number="LOG-3", salesperson=user)
        line = LineItem.objects.create(contract=contract, position=1, model="OLD", quantity=1)
        FormSet = inlineformset_factory(Contract, LineItem, fields=("model", "quantity"), extra=0)
        formset = FormSet({"items-TOTAL_FORMS": "1", "items-INITIAL_FORMS": "1", "items-MIN_NUM_FORMS": "0", "items-MAX_NUM_FORMS": "1000", "items-0-id": str(line.pk), "items-0-model": "NEW", "items-0-quantity": "1"}, instance=contract, prefix="items")
        self.assertTrue(formset.is_valid(), formset.errors)
        request = RequestFactory().post("/admin/contracts/contract/1/change/")
        request.user = user
        ContractAdmin(Contract, admin.site).save_formset(request, SimpleNamespace(instance=contract), formset, True)
        self.assertIn("OLD → NEW", AuditEvent.objects.get(contract=contract).detail)


class LoginTests(TestCase):
    def test_anonymous_home_redirects_to_existing_login_page(self):
        response = self.client.get(reverse("dashboard"))
        self.assertRedirects(response, "/login/?next=/")


class DashboardFilterTests(TestCase):
    def test_missing_progress_is_excluded_from_due_total(self):
        user = get_user_model().objects.create_superuser("due_admin", "due@example.com", "safe-password-123")
        Contract.objects.create(contract_number="DUE-1", salesperson=user, signed_on=timezone.localdate(), currency="美元", amount=100, progress="")
        self.client.force_login(user)
        page = self.client.get(reverse("dashboard"))
        self.assertEqual(page.context["due_rmb"], "0.00")
        self.assertEqual(page.context["due_unrated"], 1)
        self.assertEqual(self.client.get(reverse("dashboard"), {"detail": "due"}).context["detail_count"], 0)
        unrated = self.client.get(reverse("dashboard"), {"detail": "due_unrated"})
        self.assertEqual(unrated.context["detail_count"], 1)
        self.assertContains(unrated, "待收未计入合同")

    def test_operational_kpis_drill_down_to_matching_contracts(self):
        admin = get_user_model().objects.create_superuser("ops", "ops@example.com", "safe-password-123")
        today = timezone.localdate()
        first = Contract.objects.create(contract_number="OPS-1", salesperson=admin, signed_on=today, currency="美元", amount=100, progress="待执行", agreed_ship_on=today - timedelta(days=1), sales_invoiced=True, invoice_on=today)
        Contract.objects.create(contract_number="OPS-2", salesperson=admin, signed_on=today, currency="欧元", amount=50, progress="执行中")
        Contract.objects.create(contract_number="OPS-3", salesperson=admin, signed_on=today, currency="人民币", amount=20, progress="待执行")
        Receipt.objects.create(contract=first, received_on=today, currency="美元", amount=30)
        Receipt.objects.create(contract=first, received_on=today, currency="欧元", amount=5)
        self.client.force_login(admin)
        for mode, expected in {"all": 3, "paid": 1, "due": 0, "cross": 1, "overdue": 1, "invoiced": 1}.items():
            response = self.client.get(reverse("dashboard"), {"detail": mode})
            self.assertEqual(response.context["detail_count"], expected, mode)
            self.assertTrue(response.context["detail_title"])
        progress = self.client.get(reverse("dashboard"), {"detail": "progress", "detail_value": "待执行"})
        self.assertEqual(progress.context["detail_count"], 2)
        self.assertContains(progress, "执行进度：待执行")
        self.assertEqual(progress.context["contracts"][0].detail_value_display, "未填写")
        self.assertTrue(any("detail=progress" in row["url"] for row in progress.context["progress_rows"]))

    def test_rmb_total_drilldown_and_all_currency_usd_trend(self):
        User = get_user_model()
        admin = User.objects.create_superuser("chief", "chief@example.com", "safe-password-123")
        today = timezone.localdate()
        for number, currency, amount, rate in [
            ("USD-1", "美元", 100, "7.2"),
            ("EUR-1", "欧元", 100, None),
            ("CNY-1", "人民币", 100, None),
            ("EMPTY-1", "美元", None, None),
        ]:
            Contract.objects.create(contract_number=number, salesperson=admin, signed_on=today, currency=currency, amount=amount, exchange_rate=rate)
        self.client.force_login(admin)
        default_page = self.client.get(reverse("dashboard"))
        self.assertEqual(default_page.context["chart_currency"], "usd_all")
        self.assertTrue(any("detail=currency" in card["url"] for card in default_page.context["currency_cards"]))
        page = self.client.get(reverse("dashboard"), {"currency": "usd_all"})
        self.assertEqual(page.context["rmb_total"], "1,570.00")
        self.assertContains(page, "全部折美元")
        current_month = today.strftime("%Y-%m")
        month = next(bar for bar in page.context["chart"] if bar["label"] == today.strftime("%y/%m"))
        self.assertEqual(month["amount"], "224.29")
        self.assertEqual(month["count"], 3)
        total_detail = self.client.get(reverse("dashboard"), {"detail": "rmb"})
        self.assertEqual(total_detail.context["detail_count"], 3)
        self.assertEqual(sum(Decimal(c.metric_rmb_display.replace(",", "")) for c in total_detail.context["contracts"]), Decimal("1570.00"))
        month_detail = self.client.get(reverse("dashboard"), {"currency": "usd_all", "month": current_month})
        self.assertEqual(month_detail.context["detail_count"], 3)
        usd_detail = self.client.get(reverse("dashboard"), {"detail": "currency", "detail_value": "美元"})
        self.assertEqual(usd_detail.context["detail_count"], 2)
        self.assertContains(usd_detail, "美元金额组成")
        self.assertTrue(any("金额未填" in c.detail_value_display for c in usd_detail.context["contracts"]))
        actual_detail = self.client.get(reverse("dashboard"), {"detail": "rate", "detail_value": "actual"})
        self.assertEqual(actual_detail.context["detail_count"], 1)
        estimate_detail = self.client.get(reverse("dashboard"), {"detail": "rate", "detail_value": "estimated"})
        self.assertEqual(estimate_detail.context["detail_count"], 1)
        missing_detail = self.client.get(reverse("dashboard"), {"detail": "rate", "detail_value": "missing_amount"})
        self.assertEqual(missing_detail.context["detail_count"], 1)

    def test_conversion_priority_and_cross_currency_fallback(self):
        self.assertEqual(conversion(100, "美元", contract_rate="7.2"), (720, "actual", Decimal("7.2")))
        self.assertEqual(conversion(100, "欧元"), (750, "estimated", Decimal("7.5")))
        self.assertEqual(conversion(10, "美元", contract_rate="7.2", receipt_rate="7.1"), (71, "actual", Decimal("7.1")))
        self.assertEqual(conversion(10, "欧元", contract_rate="7.2", same_contract_currency=False), (75, "estimated", Decimal("7.5")))

    def test_defaults_to_current_year_and_filters_each_salesperson(self):
        User = get_user_model()
        admin = User.objects.create_superuser("admin", "admin@example.com", "safe-password-123")
        alice = User.objects.create_user("alice", password="safe-password-123")
        bob = User.objects.create_user("bob", password="safe-password-123")
        today = timezone.localdate()
        first = Contract.objects.create(contract_number="A-1", salesperson=alice, signed_on=today, currency="美元", amount=100, exchange_rate="7.2", customer_name="Alpha", progress="0.5")
        Material.objects.create(contract=first, code="MAT-1", name="Sensor", quantity=2, source_sha256="source", source_position=1, source_oa_number="123")
        OutboundItem.objects.create(contract=first, document_number="OUT-1", material_code="MAT-1", quantity=1, source_sha256="source", source_position=1, source_oa_number="123")
        Shipment.objects.create(contract=first, shipped_on=today, tracking="TRACK-1")
        second = Contract.objects.create(contract_number="B-1", salesperson=bob, signed_on=today, currency="美元", amount=200, customer_name="Beta", progress="1")
        Receipt.objects.create(contract=first, received_on=today, currency="美元", amount=10, exchange_rate="7.1")
        Receipt.objects.create(contract=second, received_on=today, currency="美元", amount=20)
        self.client.force_login(admin)
        home = self.client.get(reverse("dashboard"))
        self.assertEqual(home.context["year"], str(today.year))
        self.assertEqual(home.context["count"], 2)
        self.assertEqual(home.context["rmb_total"], "2,120.00")
        self.assertEqual(home.context["received_same_rmb"], "211.00")
        self.assertEqual(home.context["due_rmb"], "360.00")
        self.assertEqual(home.context["due_unrated"], 0)
        due_detail = self.client.get(reverse("dashboard"), {"detail": "due"})
        self.assertEqual(due_detail.context["detail_count"], 1)
        self.assertIn("执行进度 0.5", due_detail.context["contracts"][0].detail_value_display)
        self.assertEqual(home.context["rate_counts"]["actual"], 1)
        self.assertEqual(home.context["rate_counts"]["estimated"], 1)
        self.assertContains(home, "alice")
        self.assertContains(home, "bob")
        picked = self.client.get(reverse("dashboard"), {"scope": f"salesperson:{alice.pk}"})
        self.assertEqual(picked.context["count"], 1)
        self.assertEqual(picked.context["contracts"][0].contract_number, "A-1")
        self.assertEqual(picked.context["rmb_total"], "720.00")
        search = self.client.get(reverse("dashboard"), {"q": "Beta"})
        self.assertEqual(search.context["count"], 1)
        received = self.client.get(reverse("dashboard"), {"metric": "received"})
        self.assertEqual(received.context["rmb_total"], "211.00")
        self.assertEqual(received.context["rate_counts"]["actual"], 1)
        self.assertEqual(received.context["rate_counts"]["estimated"], 1)
        exported = self.client.get(reverse("export"), {"scope": f"salesperson:{alice.pk}"})
        self.assertEqual(exported.status_code, 200)
        self.assertIn(".xlsx", exported["Content-Disposition"])
        book = load_workbook(BytesIO(exported.content), read_only=True, data_only=True)
        self.assertTrue({"OA供货物料", "OA已发货物料", "发货记录"}.issubset(book.sheetnames))
        self.assertEqual(book["OA供货物料"]["E2"].value, "MAT-1")
        self.assertEqual(book["OA已发货物料"]["E2"].value, "OUT-1")
        self.assertEqual(book["发货记录"]["F2"].value, "TRACK-1")
        rows = list(book["合同明细"].values)
        self.assertEqual(len(rows[0]), 38)
        self.assertEqual([row[4] for row in rows[1:]], ["A-1"])
        self.assertEqual(book["筛选说明"]["B10"].value, 1)
        book.close()
        self.client.force_login(alice)
        forbidden_scope = self.client.get(reverse("dashboard"), {"scope": f"salesperson:{bob.pk}"})
        self.assertEqual(forbidden_scope.context["count"], 1)
        self.assertEqual(forbidden_scope.context["scope"], "mine")


HISTORICAL_SAMPLE = Path(r"C:\Users\Administrator\Documents\tongda\ispirit\file_receive\299609智仪-合同执行任务单V1.0流程甲方F11-261023-01乙方F11-261023-01（ISTS, 分析仪1台）[1]\2026合同管理表_许沛翔.xlsx")
SONG_SAMPLE = Path(r"C:\Users\Administrator\Desktop\2024年智仪销售合同统计\数据源\2026合同管理表_宋振峰.xlsx")


@skipUnless(SONG_SAMPLE.exists(), "宋振峰历史样例不在此环境")
class DamagedHeaderImportTests(TestCase):
    def test_preview_recovers_overwritten_contract_header(self):
        parsed = parse_workbook(SONG_SAMPLE.read_bytes())
        self.assertEqual((parsed["row_count"], len(parsed["contracts"])), (30, 24))
        self.assertTrue(any("第 5 列表头" in warning for warning in parsed["warnings"]))
        self.assertEqual(parsed["contracts"][0]["signed_on"], "2025-11-28")



@skipUnless(HISTORICAL_SAMPLE.exists(), "历史样例不在此环境")
class HistoricalImportTests(TestCase):
    def test_preview_confirm_and_repeat(self):
        admin = get_user_model().objects.create_superuser("admin", "admin@example.com", "safe-password-123")
        owner = get_user_model().objects.create_user("xu", password="safe-password-123", first_name="沛翔", last_name="许")
        self.client.force_login(admin)
        data = HISTORICAL_SAMPLE.read_bytes()
        parsed = parse_workbook(data)
        self.assertEqual((parsed["row_count"], len(parsed["contracts"])), (56, 26))
        self.assertEqual(next(c for c in parsed["contracts"] if c["number"] == "F08-251013-01")["amount"], 28857)
        def preview():
            return self.client.post(reverse("import_excel"), {"stage": "preview", "owner_id": owner.pk, "file": SimpleUploadedFile("history.xlsx", data)})
        self.assertEqual(preview().status_code, 200)
        self.assertEqual(self.client.post(reverse("import_excel"), {"stage": "confirm", "confirmed": "yes"}).status_code, 302)
        self.assertEqual(Contract.objects.count(), 25)
        self.assertEqual(Contract.objects.get(contract_number="F08-251013-01").items.count(), 7)
        self.assertEqual(Contract.objects.get(contract_number="F08-251013-01").receipts.first().amount, 28857)
        page = self.client.get(reverse("dashboard"), {"year": "2026", "metric": "signed", "dimension": "country"})
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "结构分布")
        self.assertContains(page, "客户金额排行")
        detail = self.client.get(reverse("dashboard"), {"year": "2026", "metric": "signed", "dimension": "customer", "focus": "ARAB TECH"})
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.context["count"], 2)
        self.assertEqual(self.client.get(reverse("dashboard"), {"year": "2026", "metric": "received"}).status_code, 200)
        preview()
        self.client.post(reverse("import_excel"), {"stage": "confirm", "confirmed": "yes"})
        self.assertEqual(Contract.objects.count(), 25)
