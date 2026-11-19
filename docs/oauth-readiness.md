# ViceScope YouTube connection checklist

This checklist prepares the existing desktop OAuth integration. It does not connect an account, upload a video, or publish anything. See [YouTube setup](youtube-setup.md) for the full procedure.

## Before connecting

- [ ] In Google Cloud Console, create or select a project and enable YouTube Data API v3.
- [ ] Configure the OAuth consent screen and audience. For an External app in Testing, add the Google account that controls the channel as a test user. Check whether Google's Personal mode is available and appropriate for your account.
- [ ] Add the scope `https://www.googleapis.com/auth/youtube.force-ssl`, which the application requests for channel verification, uploads, and privacy changes. This is broader than upload-only access; review the permissions before granting consent.
- [ ] Create an **OAuth Desktop app** client and download its JSON. Place it at `%LOCALAPPDATA%\ViceScopeClipStudio\youtube-client.json`; do not put it in Git or share it. If a file is already there, preserve it until you have deliberately reviewed it.
- [ ] Confirm the channel ID in YouTube's advanced account settings. The ID previously supplied for ViceScope was `UCzsYzQcaMb2d9uP3KS23HLg`; independently confirm it before use. Add `youtube_channel_id` to `%LOCALAPPDATA%\ViceScopeClipStudio\settings.json`, preserving any existing settings. `VICESCOPE_YOUTUBE_CHANNEL_ID`, if set, takes precedence.
- [ ] Review [Google's desktop OAuth guidance](https://developers.google.com/identity/protocols/oauth2/native-app) and the current [Google audience guidance](https://support.google.com/cloud/answer/15549945). An External Testing app's refresh token normally expires after seven days for YouTube scopes; Google's Personal mode has different rules.

## Safeguards in the implementation

- The app uses the system browser and a loopback listener with OAuth state and PKCE. It verifies `channels.list(mine=true)` against the configured channel ID before saving the refresh token.
- Windows uses Credential Manager for refresh tokens and resumable upload session URLs. A disposable write/read/delete check passed on this machine. No real credential was read or written during this preparation.
- Google credentials are refreshed by the Google auth client when needed; a revoked or expired refresh token requires reconnection. Disconnect attempts Google token revocation before deleting the local token.
- A private upload requires the reviewed source permission, output hash and metadata approval to remain valid. Public publication is a separate action with a confirmation dialog; the implementation rechecks the channel, approval and remote video details.
- The automated suite exercises OAuth callback handling, mismatch, upload and publication safeguards with mocks. No live OAuth, upload or publication was performed for this checklist. The connected channel and the project's actual upload/privacy behavior remain unverified.

Google's current video-resource page and historical private-upload restriction notice differ; verify the current policy and actual video's status instead of assuming whether an unverified project can change visibility. See the links in [YouTube setup](youtube-setup.md).
