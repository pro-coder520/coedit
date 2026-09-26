from fastapi.testclient import TestClient

from app.main import create_app


def test_demo_page_and_client_script_are_served(tmp_path) -> None:
    app = create_app(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'demo.sqlite'}",
        initialize_schema=True,
    )

    with TestClient(app) as client:
        page = client.get("/")
        script = client.get("/app.js")

    assert page.status_code == 200
    assert "Coedit" in page.text
    assert script.status_code == 200
    assert "function applyOperation" in script.text