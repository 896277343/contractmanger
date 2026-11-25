from django.db import migrations, models
import django.db.models.deletion


def backfill(apps, schema_editor):
    Contract = apps.get_model("contracts", "Contract")
    OAFlow = apps.get_model("contracts", "OAFlow")
    for contract in Contract.objects.exclude(oa_number__isnull=True).exclude(oa_number=""):
        OAFlow.objects.get_or_create(number=contract.oa_number, defaults={"contract_id": contract.pk, "source": "原有数据"})


class Migration(migrations.Migration):
    dependencies = [("contracts", "0002_roles")]
    operations = [
        migrations.CreateModel(name="OAFlow", fields=[("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")), ("number", models.CharField(max_length=40, unique=True, verbose_name="OA流水号")), ("source", models.CharField(blank=True, max_length=20, verbose_name="来源")), ("created_at", models.DateTimeField(auto_now_add=True)), ("contract", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="oa_flows", to="contracts.contract"))], options={"ordering": ["created_at", "id"]}),
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
