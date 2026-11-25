from django.db import migrations


def create_roles(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")
    for name, codes in {
        "业务员": ["view_contract", "change_contract", "add_contract", "view_lineitem", "change_lineitem", "add_lineitem", "delete_lineitem", "view_shipment", "change_shipment", "add_shipment", "delete_shipment", "view_receipt", "change_receipt", "add_receipt", "delete_receipt"],
        "信息处理员": ["view_contract", "change_contract", "add_contract", "view_lineitem", "change_lineitem", "add_lineitem", "delete_lineitem", "view_shipment", "change_shipment", "add_shipment", "delete_shipment", "view_receipt", "change_receipt", "add_receipt", "delete_receipt"],
        "领导": ["view_contract", "view_lineitem", "view_shipment", "view_receipt"],
    }.items():
        group, _ = Group.objects.get_or_create(name=name)
        group.permissions.set(Permission.objects.filter(content_type__app_label="contracts", codename__in=codes))


class Migration(migrations.Migration):
    dependencies = [("contracts", "0001_initial"), ("auth", "0012_alter_user_first_name_max_length")]
    operations = [migrations.RunPython(create_roles, migrations.RunPython.noop)]
