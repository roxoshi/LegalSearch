# Authentication System

LegalSearch uses an Identity-Provider pattern with OTP-based email login and Google OAuth. There are no passwords. JWTs are stored in HttpOnly cookies.

## How It Works

```
User enters email ──> POST /auth/request-otp ──> OTP sent via SMTP
User enters OTP   ──> POST /auth/verify-otp  ──> JWT set in HttpOnly cookie
   (or)
User clicks Google ──> POST /auth/google      ──> JWT set in HttpOnly cookie
```

New users who haven't completed their profile get `needs_profile: true` in the response and are prompted to fill in their name before proceeding.

### Database Schema

- **`users`** — UUID PK, `first_name`, `last_name`, `year_of_birth`
- **`user_identities`** — Links providers (`email`, `google`) to users. Unique on `(provider, provider_id)`. One user can have multiple identities.
- **`otp_codes`** — Temporary store for hashed OTPs with 5-minute expiry and max 5 attempts.

### Endpoints

| Endpoint | Method | Auth | Description |
|---|---|---|---|
| `/auth/request-otp` | POST | No | Send OTP to email |
| `/auth/verify-otp` | POST | No | Verify OTP, set JWT cookie |
| `/auth/google` | POST | No | Verify Google id_token, set JWT cookie |
| `/auth/me` | GET | Yes | Get current user profile |
| `/auth/profile` | POST | Yes | Update user profile |
| `/auth/logout` | POST | No | Clear JWT cookie |
| `/auth/dev-login` | POST | No | **Staging only.** Bypass OTP/Google, log in directly. |

---

## Environment Variables

### Auth-specific variables

| Variable | Used by | Description |
|---|---|---|
| `SECRET_KEY` | Backend | Signs JWT tokens. **Must be unique per environment.** |
| `GOOGLE_CLIENT_ID` | Backend + Frontend | Google OAuth client ID |
| `GOOGLE_CLIENT_SECRET` | Backend | Google OAuth client secret (kept for future use) |
| `SMTP_HOST` | Backend | SMTP server hostname. Empty = OTPs logged to console. |
| `SMTP_PORT` | Backend | SMTP server port (default: `587`) |
| `SMTP_USER` | Backend | SMTP login username. Empty = skip TLS/auth (for MailPit). |
| `SMTP_PASSWORD` | Backend | SMTP login password |
| `SMTP_FROM_EMAIL` | Backend | "From" address on OTP emails |
| `ENVIRONMENT` | Backend | `staging`, `dev`, or `prod`. Controls cookie `Secure` flag and enables `/auth/dev-login`. |
| `FRONTEND_URL` | Backend | Added to CORS allowed origins (for non-localhost frontends). |
| `NEXT_PUBLIC_GOOGLE_CLIENT_ID` | Frontend | Google OAuth client ID (passed to browser) |
| `NEXT_PUBLIC_API_URL` | Frontend | Backend API base URL |

---

## How to Get Each Key

### 1. SECRET_KEY

A random 256-bit hex string used to sign JWTs. Generate one per environment:

```bash
openssl rand -hex 32
```

Each environment **must** have its own unique `SECRET_KEY`. Rotating it invalidates all active sessions (users must re-login).

### 2. Google OAuth Credentials

Only needed for **dev** and **prod** (staging can skip this entirely).

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a project (or select existing)
3. Navigate to **APIs & Services > Credentials**
4. Click **Create Credentials > OAuth 2.0 Client ID**
5. Application type: **Web application**
6. Add **Authorized JavaScript Origins**:
   - Dev: `http://localhost:3000`
   - Prod: `https://yourdomain.com`
7. Copy the **Client ID** and **Client Secret**

You can use a single OAuth client for both dev and prod by listing all origins in step 6, or create separate clients for isolation.

```bash
GOOGLE_CLIENT_ID=123456789-xxxx.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=GOCSPX-xxxxxxxxxxxxxxxx
NEXT_PUBLIC_GOOGLE_CLIENT_ID=${GOOGLE_CLIENT_ID}
```

### 3. SMTP Credentials

Only needed for **dev** and **prod** (staging uses MailPit locally).

**Gmail (dev only):**
- Enable 2FA on a Google account
- Generate an [App Password](https://myaccount.google.com/apppasswords)
- Host: `smtp.gmail.com`, Port: `587`

**Amazon SES (prod):**
1. Go to [AWS SES Console](https://console.aws.amazon.com/ses/)
2. Verify your sending domain or email address
3. Create SMTP credentials under **SMTP Settings**
4. Endpoint: e.g. `email-smtp.us-east-1.amazonaws.com`

**Resend, Postmark, SendGrid, Mailgun** also work — any provider that offers standard SMTP.

---

## Per-Environment Setup

### Staging — Fully Local, No External Services

Staging is your local development environment. It uses **zero external services** for auth testing:

- **OTP emails**: Caught by [MailPit](https://mailpit.axllent.org/) running in Docker. View them at `http://localhost:8025`.
- **Google OAuth**: Skipped entirely. Use the `/auth/dev-login` endpoint instead.
- **No API keys needed**: No Google credentials, no SMTP provider accounts.

#### Setup

1. **Start MailPit** alongside your other services:

```bash
docker compose --env-file envs/.env.staging --profile mail up -d
```

This starts the `mailpit` container alongside `db`, `backend`, and `frontend`. MailPit listens on:
- **SMTP**: port `1025` (receives emails from backend)
- **Web UI**: port `8025` (view caught emails in browser)

2. **Env file** (`envs/.env.staging`):

```bash
ENVIRONMENT=staging

SECRET_KEY=<openssl rand -hex 32>

# Google — not needed for staging, leave empty
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=

# SMTP — points to MailPit container
SMTP_HOST=mailpit
SMTP_PORT=1025
SMTP_USER=
SMTP_PASSWORD=
SMTP_FROM_EMAIL=noreply@legalsearch.local
```

When `SMTP_USER` is empty, the backend skips TLS and authentication — exactly what MailPit expects.

#### Testing the OTP flow (staging)

```bash
# 1. Request OTP
curl -X POST http://localhost:8000/auth/request-otp \
  -H 'Content-Type: application/json' \
  -d '{"identifier": "test@example.com"}'

# 2. Open http://localhost:8025 in your browser
#    You'll see the email with the 6-digit code

# 3. Verify OTP (replace 123456 with the code from MailPit)
curl -X POST http://localhost:8000/auth/verify-otp \
  -H 'Content-Type: application/json' \
  -d '{"identifier": "test@example.com", "otp": "123456", "first_name": "Test", "last_name": "User"}' \
  -c cookies.txt

# 4. Check auth
curl http://localhost:8000/auth/me -b cookies.txt
```

#### Testing with dev-login (staging — skip OTP entirely)

When `ENVIRONMENT=staging`, a `/auth/dev-login` endpoint is available that bypasses OTP and Google completely:

```bash
# Instant login — no OTP, no Google, no email
curl -X POST http://localhost:8000/auth/dev-login \
  -H 'Content-Type: application/json' \
  -d '{"email": "test@example.com", "first_name": "Test", "last_name": "User"}' \
  -c cookies.txt

# Verify it worked
curl http://localhost:8000/auth/me -b cookies.txt
```

This endpoint does **not** exist in dev or prod — only when `ENVIRONMENT=staging`.

#### Frontend testing (staging)

The frontend login page works as normal in staging — enter an email, it sends an OTP, check MailPit for the code. The Google login button won't work without credentials, but OTP covers the full flow.

---

### Dev — Real Google OAuth + Real SMTP

Dev is for testing with actual third-party services before going to prod.

#### Setup

You need:
- A Google OAuth Client ID (see [How to Get Each Key > Google OAuth](#2-google-oauth-credentials))
- An SMTP provider account (see [How to Get Each Key > SMTP](#3-smtp-credentials))

`envs/.env.dev`:
```bash
ENVIRONMENT=dev

SECRET_KEY=<openssl rand -hex 32>

# Real Google OAuth
GOOGLE_CLIENT_ID=<your-client-id>
GOOGLE_CLIENT_SECRET=<your-client-secret>

# Real SMTP (e.g., Gmail App Password)
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=yourproject@gmail.com
SMTP_PASSWORD=<gmail-app-password>
SMTP_FROM_EMAIL=yourproject@gmail.com
```

`frontend/.env.local`:
```bash
NEXT_PUBLIC_API_URL=http://localhost:8000
NEXT_PUBLIC_GOOGLE_CLIENT_ID=<your-client-id>
```

**Dev behavior:**
- Cookie `Secure` flag is `false` (works over plain HTTP on localhost)
- CORS allows `http://localhost:3000`
- OTP emails sent to real inboxes via SMTP
- Google login uses real Google OAuth flow
- `/auth/dev-login` is **not** available

---

### Prod — Real Everything, Hardened

`envs/.env.prod`:
```bash
ENVIRONMENT=prod

SECRET_KEY=<unique-prod-key>

# Real Google OAuth
GOOGLE_CLIENT_ID=<your-client-id>
GOOGLE_CLIENT_SECRET=<your-client-secret>

# Production SMTP (e.g., AWS SES)
SMTP_HOST=email-smtp.us-east-1.amazonaws.com
SMTP_PORT=587
SMTP_USER=<ses-smtp-user>
SMTP_PASSWORD=<ses-smtp-password>
SMTP_FROM_EMAIL=noreply@yourdomain.com

FRONTEND_URL=https://yourdomain.com
```

**Prod behavior:**
- Cookie `Secure` flag is `true` (requires HTTPS)
- CORS allows `http://localhost:3000` + `FRONTEND_URL`
- OTP emails sent to real inboxes
- Google login uses real Google OAuth flow
- `/auth/dev-login` is **not** available

---

## What Must Be Different Per Environment

| Variable | Staging | Dev | Prod |
|---|---|---|---|
| `SECRET_KEY` | unique | unique | unique |
| `ENVIRONMENT` | `staging` | `dev` | `prod` |
| `GOOGLE_CLIENT_ID` | empty | real | real |
| `SMTP_HOST` | `mailpit` (local) | real provider | real provider |
| `SMTP_USER` | empty | real credentials | real credentials |
| `FRONTEND_URL` | not needed | not needed | `https://yourdomain.com` |
| `DATABASE_URL` | local postgres | local postgres | prod postgres |
| `/auth/dev-login` | available | **not** available | **not** available |

---

## Database Migration

After deploying with the new auth schema, run the migration:

```bash
psql -U $POSTGRES_USER -d $POSTGRES_DB < scripts/migrations.sql
```

This is idempotent — safe to run multiple times. It will:
- Drop the old `users` table (if it has the old `email` column schema)
- Create `users`, `user_identities`, and `otp_codes` tables
- All existing user sessions will be invalidated (users must re-login)

---

## Troubleshooting

**OTPs not arriving in MailPit (staging):**
- Make sure MailPit is running: `docker compose --profile mail ps`
- Check that `SMTP_HOST=mailpit` (the Docker service name, not `localhost`)
- Open `http://localhost:8025` — if the UI loads, MailPit is running
- Check backend logs: `docker compose logs backend`

**OTPs not arriving via real SMTP (dev/prod):**
- Check `SMTP_HOST` is set. If empty, OTPs are logged to backend stdout instead.
- Verify credentials: `python -c "import smtplib; s = smtplib.SMTP('smtp.gmail.com', 587); s.starttls(); s.login('user', 'pass'); print('OK')"`
- For SES: verify the sender email/domain is verified and you're out of sandbox mode.

**Google login fails with "Invalid Google token":**
- Verify `GOOGLE_CLIENT_ID` on backend matches `NEXT_PUBLIC_GOOGLE_CLIENT_ID` on frontend.
- Check that your frontend's origin is listed in Google Cloud Console under Authorized JavaScript Origins.
- This is expected in staging when `GOOGLE_CLIENT_ID` is empty — use `/auth/dev-login` or OTP instead.

**Cookies not being sent (401 on /auth/me):**
- Frontend must use `credentials: 'include'` on all fetch calls (already configured in AuthContext).
- `FRONTEND_URL` must be set in backend env so CORS allows the origin.
- In prod, the app must be served over HTTPS (cookie `Secure` flag is `true`).

**CORS errors:**
- The backend allows `http://localhost:3000` by default, plus whatever `FRONTEND_URL` is set to.
- Wildcard `*` origins do not work with `credentials: true`. The origin must be explicitly listed.

**`/auth/dev-login` returns 404:**
- This endpoint only exists when `ENVIRONMENT=staging`. Check your env file.
