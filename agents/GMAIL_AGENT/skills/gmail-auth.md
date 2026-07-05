# Description

How to authenticate with Gmail using OAuth2 credentials.

# Gmail Authentication (gmail-auth)

## Environment Variables

The Gmail Agent requires exactly three environment variables to be set at runtime:

| Variable | Description | Required |
|----------|-------------|----------|
| `GMAIL_CLIENT_ID` | OAuth 2.0 Client ID from Google Cloud Console | Yes |
| `GMAIL_CLIENT_SECRET` | OAuth 2.0 Client Secret from Google Cloud Console | Yes |
| `GMAIL_REFRESH_TOKEN` | OAuth 2.0 Refresh Token (offline access) | Yes |

## How to Obtain Credentials

1. Go to the [Google Cloud Console](https://console.cloud.google.com/)
2. Create a project (or select an existing one)
3. Navigate to **APIs & Services → Library** and enable the **Gmail API**
4. Navigate to **APIs & Services → Credentials**
5. Click **Create Credentials → OAuth 2.0 Client ID**
   - Application type: **Desktop app** (or Web application for server deployments)
   - Add `http://localhost` to authorized redirect URIs
6. Note the **Client ID** and **Client Secret**

### Obtaining a Refresh Token (Desktop Flow)

Using the [Google OAuth 2.0 Playground](https://developers.google.com/oauthplayground/):

1. Click the gear icon → check "Use your own OAuth credentials"
2. Enter your Client ID and Client Secret
3. In the scopes field, enter: `https://www.googleapis.com/auth/gmail.modify`
4. Click "Authorize APIs" and complete the OAuth flow
5. Click "Exchange authorization code for tokens"
6. Copy the **Refresh token**

### Scope

The agent uses the `gmail.modify` scope (`https://www.googleapis.com/auth/gmail.modify`), which grants:

- Read messages
- Send messages
- Create drafts
- Add/remove labels
- Trash/untrash messages
- Permanently delete messages

This scope does **not** grant account administration, password changes, or access to other Google services.

## How Credentials Are Used

The `get_gmail_service()` function in `_gmail_base.py`:
1. Reads the three environment variables
2. Constructs a `google.oauth2.credentials.Credentials` object with the refresh token
3. Builds a Gmail API v1 service resource
4. Caches the service globally so subsequent calls reuse the same connection

## Common Failure Modes

| Error | Cause | Solution |
|-------|-------|----------|
| `Missing Gmail credential: GMAIL_CLIENT_ID` | Env var not set | Export the variable before starting the agent |
| `Gmail auth failed: refresh token expired or revoked` | Token expired or user revoked access | Re-authenticate via the OAuth playground |
| `Failed to initialize Gmail service: ...` | Network error, invalid JSON, etc. | Check connectivity and credential format |

## Error Messages

The agent produces specific, actionable error messages for each failure mode so the user knows exactly what to fix.
