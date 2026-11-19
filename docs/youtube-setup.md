# Set up the ViceScope YouTube connection on Windows

## Google Cloud Console

1. Open [Google Cloud Console](https://console.cloud.google.com/) and create or select a project for ViceScope. In **APIs & Services → Library**, find **YouTube Data API v3** and click **Enable**.
2. In **Google Auth Platform → Branding**, set the app name to **ViceScope Clip Studio**, select a user support email and add your developer contact email.
3. Under **Audience**, choose **External** for a personal Google account. While the app is in **Testing**, add the Google account that controls the ViceScope channel under **Test users**. **Internal** is available for eligible Google Workspace organizations and limits access to that organization.
4. In **Data Access**, add `https://www.googleapis.com/auth/youtube.force-ssl`. The app requests this scope to read the authenticated channel and video status, upload, and change video privacy.
5. In **Clients → Create client**, select **Desktop app**, name it **ViceScope Clip Studio**, create it and download its JSON. Google Desktop OAuth supports the loopback redirect on an ephemeral `127.0.0.1` port. A Web application client, client secret embedded in source code, API key, or service account cannot substitute for this JSON.
6. On Windows, create `%LOCALAPPDATA%\ViceScopeClipStudio` if needed. Save the downloaded file as `youtube-client.json` in that directory. Keep the file outside the repository.
7. While signed in to the intended ViceScope identity, open [YouTube account advanced settings](https://www.youtube.com/account_advanced) and copy the **Channel ID**, which starts with `UC`. In PowerShell, set it for the current launcher session and start the app.

   ```powershell
   $env:VICESCOPE_YOUTUBE_CHANNEL_ID = 'UC_REPLACE_WITH_YOUR_ACTUAL_CHANNEL_ID'
   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
   .\.venv\Scripts\python.exe -m app.main
   ```

   To keep the channel ID for future PowerShell sessions, set the user environment variable with `[Environment]::SetEnvironmentVariable('VICESCOPE_YOUTUBE_CHANNEL_ID', $env:VICESCOPE_YOUTUBE_CHANNEL_ID, 'User')`. Restart any launcher that was open before the change.
8. In **Ready for Review**, choose **Connect YouTube**. In the system browser, sign in to Google and select the intended channel if Google offers a channel chooser. Check the channel name and exact ID shown in ViceScope. Use **Verify channel** after restarting the app. ViceScope never collects a Google password.

For external apps in **Testing**, Google generally expires refresh tokens after seven days when YouTube scopes are requested. Use **Connect YouTube** again when prompted. Moving the OAuth app to production and any required OAuth verification is a separate Google process from the YouTube API compliance audit. YouTube restricts uploads from unverified API projects created after July 28, 2020 to private visibility. An affected project needs a [YouTube API Services audit](https://developers.google.com/youtube/v3/guides/quota_and_compliance_audits) before you can rely on changing visibility through the API. [Google OAuth desktop documentation](https://developers.google.com/identity/protocols/oauth2/native-app) · [YouTube upload restrictions](https://developers.google.com/youtube/v3/docs/videos/insert)

## Review, upload, and publish

- Generate clips as usual. In **Ready for Review**, select a clip, check the preview, save the metadata, and choose **Approve private upload**. Approval is tied to the exact rendered MP4 and canonical reviewed metadata. An edit, rejection, superseding render, or changed source authorization can invalidate it.
- Select the approved item and choose **Upload Privately**. The app verifies the channel and checks source permission and hashes before reserving the upload. YouTube receives the reviewed title and description, with reviewed hashtags appended to the description. The app uses category **People & Blogs** (22). Check audience and other Studio settings before making the video public.
- After an interruption, choose **Upload Privately** again on the same item. ViceScope queries YouTube for the server-confirmed offset or completed video ID. If the session expired or is missing after data transfer may have begun, the reservation remains blocked. Check YouTube Studio. ViceScope has no in-app recovery override for a lost session or an independently completed upload.
- Use **Open on YouTube** to view the uploaded video while signed into its owner account. **Refresh video details** reads the title, description, and current privacy from YouTube. Choose **Publish Publicly** and confirm in the dialog. ViceScope rechecks channel identity, approval, and remote video details before asking YouTube to change privacy, then reads privacy back. If the update returns an uncertain result, refresh details or check Studio before trying again.
- **Disconnect YouTube** revokes the Google refresh token and removes it from Windows Credential Manager after confirmed revocation. If revocation cannot reach Google, retry the action. You can also remove access from your [Google Account connections](https://myaccount.google.com/connections).

The Windows Credential Manager stores refresh tokens and active resumable-session URLs. SQLite records the reservation, confirmed progress, video ID, and last observed privacy. The duplicate check applies to matching MP4 content within this app’s database and destination channel. Keep the database and Windows credential entries intact while an upload is pending. Uploading uses a temporary local snapshot of the approved MP4 so media edited during transfer cannot change the bytes sent to YouTube.

## Local verification

Run the automated suite in PowerShell.

```powershell
$env:QT_QPA_PLATFORM = 'offscreen'
.\.venv\Scripts\python.exe -m pytest -q
```

The tests mock Internet requests and Windows Credential Manager. The OAuth callback test uses an actual local loopback listener with a mocked system browser and token exchange. The offscreen Qt test exercises the review buttons and publication confirmation with a playback stub. A live Windows connection requires the Cloud configuration and the channel owner's Google authorization. An actual upload requires explicit authorization for the selected video.
