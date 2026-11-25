from django.db import migrations


def grant_detail_permissions(apps, schema_editor):
    ContentType = apps.get_model("contenttypes", "ContentType")
    Permission = apps.get_model("auth", "Permission")
    Group = apps.get_model("auth", "Group")
    roles = {
        "业务员": {"material": ("view", "add", "change"), "outbounditem": ("view", "add", "change"), "auditevent": ("view",)},
        "信息处理员": {"material": ("view", "add", "change", "delete"), "outbounditem": ("view", "add", "change", "delete"), "auditevent": ("view",)},
        "领导": {"material": ("view",), "outbounditem": ("view",), "auditevent": ("view",)},
    }
    for role, models in roles.items():
        group = Group.objects.filter(name=role).first()
        if not group:
            continue
        for model, actions in models.items():
            content_type, _ = ContentType.objects.get_or_create(app_label="contracts", model=model)
            for action in actions:
                permission, _ = Permission.objects.get_or_create(
                    content_type=content_type,
                    codename=f"{action}_{model}",
                    defaults={"name": f"Can {action} {model}"},
                )
                group.permissions.add(permission)


class Migration(migrations.Migration):
    dependencies = [("contracts", "0007_outbounditem"), ("auth", "0012_alter_user_first_name_max_length")]
    operations = [migrations.RunPython(grant_detail_permissions, migrations.RunPython.noop)]
