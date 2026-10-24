from django.db import migrations, models


# #55: 탈퇴 웹훅(user.deleted)의 처리한 이벤트 id(W4). 새 표 하나뿐이라 되돌리기도 깨끗하다.
class Migration(migrations.Migration):

    dependencies = [
        ("tenants", "0007_membership_invites"),
    ]

    operations = [
        migrations.CreateModel(
            name="AuthWebhookEvent",
            fields=[
                ("event_id", models.CharField(max_length=64, primary_key=True, serialize=False)),
                ("type", models.CharField(max_length=64)),
                ("received_at", models.DateTimeField(auto_now_add=True, db_index=True)),
            ],
            options={"db_table": "auth_webhook_events"},
        ),
    ]
