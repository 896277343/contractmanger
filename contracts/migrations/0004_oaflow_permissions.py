from django.db import migrations


def grant(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")
    ContentType = apps.get_model("contenttypes", "ContentType")
    models = ["contract", "lineitem", "shipment", "receipt", "oaflow"]
    for model in models:
        content_type, _ = ContentType.objects.get_or_create(app_label="contracts", model=model)
        for action in ("view", "add", "change", "delete"):
            code = f"{action}_{model}"
            Permission.objects.get_or_create(content_type=content_type, codename=code, defaults={"name": f"Can {action} {model}"})
    all_codes = [f"{action}_{model}" for model in models for action in ("view", "add", "change", "delete")]
    for name, codes in {
        "业务员": [code for code in all_codes if code.startswith(("view_", "add_", "change_")) or code in ("delete_lineitem", "delete_shipment", "delete_receipt")],
        "信息处理员": all_codes,
        "领导": [code for code in all_codes if code.startswith("view_")],
    }.items():
        group = Group.objects.filter(name=name).first()
        if group:
            group.permissions.set(Permission.objects.filter(content_type__app_label="contracts", codename__in=codes))


class Migration(migrations.Migration):
    dependencies = [("contracts", "0003_oaflow")]
    operations = [migrations.RunPython(grant, migrations.RunPython.noop)]
