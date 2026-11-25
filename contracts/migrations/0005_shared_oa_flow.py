from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("contracts", "0004_oaflow_permissions")]
    operations = [
        migrations.AlterField(model_name="contract", name="oa_number", field=models.CharField(blank=True, max_length=40, null=True, verbose_name="OA流水号")),
        migrations.AlterField(model_name="oaflow", name="number", field=models.CharField(max_length=40, verbose_name="OA流水号")),
        migrations.AddConstraint(model_name="oaflow", constraint=models.UniqueConstraint(fields=("contract", "number"), name="unique_oa_flow_per_contract")),
    ]
