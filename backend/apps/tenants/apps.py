from django.apps import AppConfig


class TenantsConfig(AppConfig):
    name = "apps.tenants"
    label = "tenants"

    def ready(self) -> None:
        # 테넌트 비밀 가림(#33): 모든 로그 기록의 메시지·예외 본문에서 이 프로세스가 본 비밀 값을 가린다.
        from apps.tenants.tenant_secrets import install_log_redaction

        install_log_redaction()
