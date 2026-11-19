# Set up the ViceScope YouTube connection on Windows or macOS

## Google Cloud Console

1. Open [Google Cloud Console](https://console.cloud.google.com/) and create or select a project for ViceScope. In **APIs & Services → Library**, find **YouTube Data API v3** and click **Enable**.
2. In **Google Auth Platform → Branding**, set the app name to **ViceScope Clip Studio**, select a user support email and add your developer contact email.
3. Under **Audience**, choose **External** for a personal Google account. While the app is in **Testing**, add the Google account that controls the ViceScope channel under **Test users**. **Internal** is available for eligible Google Workspace organizations and limits access to that organization.
4. In **Data Access**, add `https://www.googleapis.com/auth/youtube.force-ssl`. The app requests this scope to read the authenticated channel and video status, upload, and change video privacy.
5. In **Clients → Create client**, select **Desktop app**, name it **ViceScope Clip Studio**, create it and download its JSON. Google Desktop OAuth supports the loopback redirect on an ephemeral `127.0.0.1` port. A Web application client, client secret embedded in source code, API key, or service account cannot substitute for this JSON.
6. Save the downloaded file as `youtube-client.json` in the app data directory. Create the directory if needed, and keep the file outside the repository.

   - Windows — `%LOCALAPPDATA%\ViceScopeClipStudio`
   - macOS — `~/Library/Application Support/ViceScopeClipStudio`
7. While signed in to the intended ViceScope identity, open [YouTube account advanced settings](https://www.youtube.com/account_advanced) and copy the **Channel ID**, which starts with `UC`. In the same app data directory, create or update the UTF-8 file `settings.json` with the channel ID. This works for terminal, Explorer, and Finder launches.

   ```json
   {
     "youtube_channel_id": "UC_REPLACE_WITH_YOUR_ACTUAL_CHANNEL_ID"
   }
   ```

   An existing `VICESCOPE_YOUTUBE_CHANNEL_ID` environment variable takes precedence. Restart the app after editing the setting.
8. In **Ready for Review**, choose **Connect YouTube**. In the system browser, sign in to Google and select the intended channel if Google offers a channel chooser. Check the channel name and exact ID shown in ViceScope. Use **Verify channel** after restarting the app. ViceScope never collects a Google password.

For external apps in **Testing**, Google generally expires refresh tokens after seven days when YouTube scopes are requested. Use **Connect YouTube** again when prompted. Google also documents a separate Personal mode with different token rules; check its availability and requirements in your console. Moving an OAuth app to production and any required OAuth verification are separate from the YouTube API compliance audit. Google's current video-resource documentation and historical restriction notice differ on whether unverified projects' uploads remain private, so do not assume either outcome for your project; check the current policy and the video's actual status before publication. [Google OAuth desktop documentation](https://developers.google.com/identity/protocols/oauth2/native-app) · [Google app audience guidance](https://support.google.com/cloud/answer/15549945) · [YouTube video resource](https://developers.google.com/youtube/data/reference/rest/v3/videos) · [YouTube historical restriction notice](https://developers.google.com/youtube/data/revision_history) · [YouTube API audit](https://developers.google.com/youtube/v3/guides/quota_and_compliance_audits)

## Review, upload, and publish

- Generate clips as usual. In **Ready for Review**, select a clip, check the preview, save the metadata, and choose **Approve private upload**. Approval is tied to the exact rendered MP4 and canonical reviewed metadata. An edit, rejection, superseding render, or changed source authorization can invalidate it.
- Select the approved item and choose **Upload Privately**. The app verifies the channel and checks source permission and hashes before reserving the upload. YouTube receives the reviewed title and description, with reviewed hashtags appended to the description. The app uses category **People & Blogs** (22). Check audience and other Studio settings before making the video public.
- After an interruption, choose **Upload Privately** again on the same item. ViceScope queries YouTube for the server-confirmed offset or completed video ID. If the session expired or is missing after data transfer may have begun, the reservation remains blocked. Check YouTube Studio. ViceScope has no in-app recovery override for a lost session or an independently completed upload.
- Use **Open on YouTube** to view the uploaded video while signed into its owner account. **Refresh video details** reads the title, description, and current privacy from YouTube. Choose **Publish Publicly** and confirm in the dialog. ViceScope rechecks channel identity, approval, and remote video details before asking YouTube to change privacy, then reads privacy back. If the update returns an uncertain result, refresh details or check Studio before trying again.
- **Disconnect YouTube** revokes the Google refresh token and removes it from Windows Credential Manager or macOS Keychain after confirmed revocation. If revocation cannot reach Google, retry the action. You can also remove access from your [Google Account connections](https://myaccount.google.com/connections).

Windows Credential Manager and macOS Keychain store refresh tokens and active resumable-session URLs. SQLite records the reservation, confirmed progress, video ID, and last observed privacy. The duplicate check applies to matching MP4 content within this app’s database and destination channel. Keep the database and native credential entries intact while an upload is pending. Uploading uses a temporary local snapshot of the approved MP4 so media edited during transfer cannot change the bytes sent to YouTube.

## Local verification

With the virtual environment active, run the automated suite.

```text
python -m pytest -q
```

The tests mock Internet requests and the credential store. The OAuth callback test uses an actual local loopback listener with a mocked system browser and token exchange. The offscreen Qt test exercises the review buttons and publication confirmation with a playback stub. A live connection requires the Cloud configuration and the channel owner's Google authorization. An actual upload requires explicit authorization for the selected video.
