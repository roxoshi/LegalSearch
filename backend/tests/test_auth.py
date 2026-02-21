"""Tests for the authentication system (OTP + Google OAuth + cookie-based JWT)."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth import hash_otp
from app.database import Base, get_database
from app.main import app
from app.models import OTPCode, User, UserIdentity


@pytest.fixture(scope="function")
def auth_db():
    """In-memory SQLite DB with only auth tables (no pgvector/ARRAY columns)."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    # Only create auth-related tables
    User.__table__.create(bind=engine, checkfirst=True)
    UserIdentity.__table__.create(bind=engine, checkfirst=True)
    OTPCode.__table__.create(bind=engine, checkfirst=True)

    TestingSession = sessionmaker(bind=engine)
    db = TestingSession()
    try:
        yield db
    finally:
        db.close()
        User.__table__.drop(bind=engine)
        UserIdentity.__table__.drop(bind=engine)
        OTPCode.__table__.drop(bind=engine)


@pytest.fixture
def client(auth_db):
    """FastAPI test client with DB override."""

    def override_get_database():
        try:
            yield auth_db
        finally:
            pass

    app.dependency_overrides[get_database] = override_get_database
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def existing_user(auth_db):
    """Create an existing verified user with email identity."""
    user = User(id=uuid4(), first_name="Jane", last_name="Doe")
    auth_db.add(user)
    auth_db.flush()
    identity = UserIdentity(
        id=uuid4(),
        user_id=user.id,
        provider="email",
        provider_id="jane@example.com",
        is_verified=True,
    )
    auth_db.add(identity)
    auth_db.commit()
    return user


class TestRequestOTP:
    @patch("app.main.send_otp_email")
    def test_request_otp_creates_identity(self, mock_send, client):
        res = client.post(
            "/auth/request-otp",
            json={"identifier": "new@example.com"},
        )
        assert res.status_code == 200
        data = res.json()
        assert data["message"] == "OTP sent"
        assert data["identifier"] == "new@example.com"
        mock_send.assert_called_once()

    @patch("app.main.send_otp_email")
    def test_request_otp_existing_user(self, mock_send, client, existing_user):
        res = client.post(
            "/auth/request-otp",
            json={"identifier": "jane@example.com"},
        )
        assert res.status_code == 200
        mock_send.assert_called_once()

    def test_request_otp_invalid_email(self, client):
        res = client.post(
            "/auth/request-otp",
            json={"identifier": "not-an-email"},
        )
        assert res.status_code == 400


class TestVerifyOTP:
    def _seed_otp(self, auth_db, identifier="test@example.com", otp="123456", expired=False, attempts=0):
        """Helper to seed an OTP record."""
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)
        if expired:
            expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)

        # Ensure identity exists
        identity = (
            auth_db.query(UserIdentity)
            .filter(UserIdentity.provider_id == identifier)
            .first()
        )
        if not identity:
            user = User(id=uuid4(), first_name="", last_name="")
            auth_db.add(user)
            auth_db.flush()
            identity = UserIdentity(
                id=uuid4(),
                user_id=user.id,
                provider="email",
                provider_id=identifier,
                is_verified=False,
            )
            auth_db.add(identity)

        otp_record = OTPCode(
            identifier=identifier,
            otp_hash=hash_otp(otp),
            expires_at=expires_at,
            attempts=attempts,
        )
        auth_db.add(otp_record)
        auth_db.commit()

    def test_verify_otp_success(self, client, auth_db):
        self._seed_otp(auth_db)
        res = client.post(
            "/auth/verify-otp",
            json={
                "identifier": "test@example.com",
                "otp": "123456",
                "first_name": "Test",
                "last_name": "User",
            },
        )
        assert res.status_code == 200
        data = res.json()
        assert data["needs_profile"] is False
        assert data["user"]["first_name"] == "Test"
        # Check cookie is set
        assert "access_token" in res.cookies

    def test_verify_otp_needs_profile(self, client, auth_db):
        self._seed_otp(auth_db)
        res = client.post(
            "/auth/verify-otp",
            json={"identifier": "test@example.com", "otp": "123456"},
        )
        assert res.status_code == 200
        data = res.json()
        assert data["needs_profile"] is True

    def test_verify_otp_expired(self, client, auth_db):
        self._seed_otp(auth_db, expired=True)
        res = client.post(
            "/auth/verify-otp",
            json={"identifier": "test@example.com", "otp": "123456"},
        )
        assert res.status_code == 400
        assert "expired" in res.json()["detail"].lower()

    def test_verify_otp_wrong_code(self, client, auth_db):
        self._seed_otp(auth_db)
        res = client.post(
            "/auth/verify-otp",
            json={"identifier": "test@example.com", "otp": "999999"},
        )
        assert res.status_code == 400
        assert "Invalid OTP" in res.json()["detail"]

    def test_verify_otp_max_attempts(self, client, auth_db):
        self._seed_otp(auth_db, attempts=5)
        res = client.post(
            "/auth/verify-otp",
            json={"identifier": "test@example.com", "otp": "123456"},
        )
        assert res.status_code == 429
        assert "Too many attempts" in res.json()["detail"]


class TestGoogleAuth:
    @patch("app.main.verify_google_id_token")
    def test_google_auth_new_user(self, mock_verify, client):
        mock_verify.return_value = {
            "email": "google@example.com",
            "given_name": "Google",
            "family_name": "User",
            "sub": "google-sub-123",
        }
        res = client.post(
            "/auth/google",
            json={"credential": "fake-id-token"},
        )
        assert res.status_code == 200
        data = res.json()
        assert data["user"]["first_name"] == "Google"
        assert "access_token" in res.cookies

    @patch("app.main.verify_google_id_token")
    def test_google_auth_existing_email_user(self, mock_verify, client, existing_user):
        mock_verify.return_value = {
            "email": "jane@example.com",
            "given_name": "Jane",
            "family_name": "Doe",
            "sub": "google-sub-jane",
        }
        res = client.post(
            "/auth/google",
            json={"credential": "fake-id-token"},
        )
        assert res.status_code == 200
        data = res.json()
        # Should link to existing user
        assert data["user"]["first_name"] == "Jane"


class TestAuthMe:
    def test_auth_me_with_cookie(self, client, auth_db):
        """Authenticated /auth/me returns user."""
        # Create user, get cookie via OTP
        self._login_user(client, auth_db)

        res = client.get("/auth/me")
        assert res.status_code == 200
        data = res.json()
        assert data["first_name"] == "Auth"

    def test_auth_me_no_cookie(self, client):
        res = client.get("/auth/me")
        assert res.status_code == 401

    def _login_user(self, client, auth_db):
        """Helper: seed OTP and verify to get auth cookie on client."""
        user = User(id=uuid4(), first_name="Auth", last_name="Test")
        auth_db.add(user)
        auth_db.flush()
        identity = UserIdentity(
            id=uuid4(),
            user_id=user.id,
            provider="email",
            provider_id="auth@example.com",
            is_verified=False,
        )
        auth_db.add(identity)
        otp_record = OTPCode(
            identifier="auth@example.com",
            otp_hash=hash_otp("111111"),
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
            attempts=0,
        )
        auth_db.add(otp_record)
        auth_db.commit()

        client.post(
            "/auth/verify-otp",
            json={
                "identifier": "auth@example.com",
                "otp": "111111",
                "first_name": "Auth",
                "last_name": "Test",
            },
        )


class TestLogout:
    def test_logout_clears_cookie(self, client, auth_db):
        # First login
        TestAuthMe()._login_user(client, auth_db)

        # Verify we're logged in
        res = client.get("/auth/me")
        assert res.status_code == 200

        # Logout
        res = client.post("/auth/logout")
        assert res.status_code == 200
        assert res.json()["message"] == "Logged out"

        # Verify we're logged out
        res = client.get("/auth/me")
        assert res.status_code == 401


class TestProfileUpdate:
    def test_profile_update(self, client, auth_db):
        TestAuthMe()._login_user(client, auth_db)

        res = client.post(
            "/auth/profile",
            json={
                "first_name": "Updated",
                "last_name": "Name",
                "year_of_birth": 1990,
            },
        )
        assert res.status_code == 200
        data = res.json()
        assert data["first_name"] == "Updated"
        assert data["last_name"] == "Name"
        assert data["year_of_birth"] == 1990

    def test_profile_update_unauthenticated(self, client):
        res = client.post(
            "/auth/profile",
            json={"first_name": "X", "last_name": "Y"},
        )
        assert res.status_code == 401
