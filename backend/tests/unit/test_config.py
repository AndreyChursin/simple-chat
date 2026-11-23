from app.core.config import Settings


def test_allowed_origins_parsing() -> None:
    s = Settings(app_origin="http://localhost:8080/, http://localhost:5173")
    assert s.allowed_origins == {"http://localhost:8080", "http://localhost:5173"}
    assert s.public_origin == "http://localhost:8080"
