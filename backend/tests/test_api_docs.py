import subprocess
import sys
import textwrap


def test_docs_and_openapi_open_in_dev(client):
    assert client.get("/api/openapi.json").status_code == 200
    assert client.get("/api/docs").status_code == 200
    assert client.get("/api/health").status_code == 200


def test_docs_and_openapi_closed_in_prod_settings():
    """운영 설정(config.settings.prod)으로 새 프로세스를 띄워 라우팅을 직접 확인한다."""
    code = textwrap.dedent(
        """
        import django
        from django.test import Client
        django.setup()
        c = Client(HTTP_HOST="localhost")
        print([c.get(p).status_code for p in ("/api/docs", "/api/openapi.json", "/api/health")])
        """
    )
    env = {"DJANGO_SETTINGS_MODULE": "config.settings.prod"}
    import os
    out = subprocess.run([sys.executable, "-c", code], env={**os.environ, **env},
                         capture_output=True, text=True, timeout=60)
    assert out.stdout.strip().endswith("[404, 404, 200]"), out.stdout + out.stderr
