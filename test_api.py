import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Ensure the API test uses an isolated SQLite database and a known admin key.
_fd, _db_path = tempfile.mkstemp(prefix="fourth_scientific_test_", suffix=".db")
os.close(_fd)
os.environ["DATABASE_URL"] = f"sqlite:///{_db_path}"
os.environ["ADMIN_API_KEY"] = "test-admin-key-123456789"
os.environ.pop("BOT_TOKEN", None)

from fastapi.testclient import TestClient
from app.main import app


def test_public_routes_and_shared_file_crud():
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        subjects_resp = client.get("/api/subjects")
        assert subjects_resp.status_code == 200
        subjects = subjects_resp.json()
        assert any(s["name"] == "رياضيات" for s in subjects)
        assert any(s["name"] == "الكردي" for s in subjects)
        assert client.get("/api/files").json() == []  # no fabricated educational files

        headers = {"X-Admin-Key": "test-admin-key-123456789"}
        subject_id = next(s["id"] for s in subjects if s["name"] == "فيزياء")
        payload = {
            "subject_id": subject_id,
            "kind": "note",
            "title": "ملزمة اختبارية",
            "teacher": "مدرس تجريبي",
            "description": "وصف للاختبار",
            "year": "2026",
            "telegram_file_id": "TEST_TELEGRAM_FILE_ID_12345",
            "file_type": "document",
            "visible": True,
        }
        created = client.post("/api/admin/files", json=payload, headers=headers)
        assert created.status_code == 201, created.text
        file_data = created.json()
        assert file_data["title"] == "ملزمة اختبارية"
        assert "telegram_file_id" not in client.get("/api/files").json()[0]
        assert client.get("/api/files", params={"category": "notes", "q": "اختبارية"}).json()[0]["id"] == file_data["id"]

        changed = client.patch(f"/api/admin/files/{file_data['id']}", json={"visible": False}, headers=headers)
        assert changed.status_code == 200, changed.text
        assert client.get("/api/files").json() == []
        assert client.get("/api/admin/dashboard", headers=headers).json()["stats"]["visible_files"] == 0
        deleted = client.delete(f"/api/admin/files/{file_data['id']}", headers=headers)
        assert deleted.status_code == 200


def test_admin_requires_key():
    with TestClient(app) as client:
        assert client.get("/api/admin/dashboard").status_code == 401
        assert client.get("/api/admin/dashboard", headers={"X-Admin-Key": "wrong-key"}).status_code == 401
