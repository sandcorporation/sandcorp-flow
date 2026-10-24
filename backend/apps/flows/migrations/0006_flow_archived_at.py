from django.db import migrations, models


class Migration(migrations.Migration):
    """흐름 보관(soft delete, #126). 되돌릴 수 있다: 열을 지우고 이름 유일 제약을 원래대로."""

    dependencies = [
        ('flows', '0005_flow_deploy_approval'),
    ]

    operations = [
        migrations.AddField(
            model_name='flow',
            name='archived_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RemoveConstraint(
            model_name='flow',
            name='flow_unique_name_per_tenant',
        ),
        migrations.AddConstraint(
            model_name='flow',
            constraint=models.UniqueConstraint(
                condition=models.Q(('archived_at__isnull', True)),
                fields=('tenant', 'name'),
                name='flow_unique_name_per_tenant',
            ),
        ),
    ]
