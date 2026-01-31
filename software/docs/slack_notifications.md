# Slack Notifications for Squid Microscope

Send real-time notifications to Slack during acquisitions, including mosaic screenshots at each timepoint.

## Features

- **Acquisition start** - Notifies when acquisition begins with parameters summary
- **Timepoint complete** - Sends mosaic screenshot with stats (elapsed time, remaining time, images, FOVs, laser AF results)
- **Acquisition finished** - Summary with total images, duration, and error count
- **Error notifications** - Alerts when job failures occur during acquisition

## Setup

### 1. Create a Slack App

1. Go to https://api.slack.com/apps
2. Click **"Create New App"** → **"From scratch"**
3. Name your app (e.g., "Squid Microscope") and select your workspace
4. Click **"Create App"**

### 2. Configure Bot Permissions

1. In the left sidebar, click **"OAuth & Permissions"**
2. Scroll to **"Scopes"** → **"Bot Token Scopes"**
3. Add these scopes:
   - `chat:write` - Send messages
   - `files:write` - Upload mosaic images

### 3. Install App to Workspace

1. Scroll up to **"OAuth Tokens for Your Workspace"**
2. Click **"Install to Workspace"**
3. Review and click **"Allow"**
4. Copy the **Bot User OAuth Token** (starts with `xoxb-`)

### 4. Get Channel ID

1. In Slack, right-click the channel for notifications
2. Select **"View channel details"**
3. Copy the **Channel ID** at the bottom (starts with `C`)

> **Note:** You must use a channel ID (starts with `C`, `G`, `D`, or `Z`), not a user ID. Direct messages to yourself won't work for image uploads.

### 5. Invite Bot to Channel

In the channel, type:
```
/invite @YourBotName
```

### 6. Configure in Squid

1. Open **Settings → Slack Notifications**
2. Check **"Enable Slack Notifications"**
3. Enter the **Bot Token** (`xoxb-...`)
4. Enter the **Channel ID** (`C...`)
5. Click **"Test Connection"** to verify
6. Configure which notifications to receive
7. Click **"Save"**

## Configuration Options

| Option | Description |
|--------|-------------|
| Enable Slack Notifications | Master switch for all notifications |
| Notify on errors | Send alert when job fails during acquisition |
| Notify on timepoint completion | Send message + mosaic after each timepoint |
| Include mosaic snapshots | Attach screenshot with timepoint notifications |
| Notify on acquisition start | Send message when acquisition begins |
| Notify on acquisition finished | Send summary when acquisition completes |

## Notification Examples

### Timepoint Complete
```
Timepoint 5/10 Complete

Elapsed: 2h 15m
Remaining: ~2h 30m
Images: 1,234
FOVs: 412
Laser AF: 408/412 FOVs succeeded (4 failed)

[Mosaic screenshot attached]
```

### Acquisition Complete
```
Acquisition Complete

Experiment: 2024-01-22_14-30-00
Status: Finished successfully
Total Images: 12,340
Timepoints: 10
Duration: 4h 45m
```

## Troubleshooting

| Error | Solution |
|-------|----------|
| `not_in_channel` | Invite the bot to the channel with `/invite @BotName` |
| `invalid_auth` | Check that the bot token is correct |
| `channel_not_found` | Verify the channel ID (must start with C, G, D, or Z) |
| `missing_scope` | Add required scopes and reinstall the app |
| No image uploads | Ensure `files:write` scope is added; use a channel, not DM |

## INI Configuration

Settings can also be configured in the INI file:

```ini
[SLACK]
enabled = False
notify_on_error = True
notify_on_timepoint_complete = True
notify_on_acquisition_start = False
notify_on_acquisition_finished = True
send_mosaic_snapshots = True
```

> **Note:** Bot Token and Channel ID are stored in a local cache file (`cache/slack_settings.yaml`) for security and are not saved to the INI file.

## Settings Persistence

- Settings are saved to `cache/slack_settings.yaml`
- Changes take effect immediately without restart
- Settings dialog can remain open during acquisition for live toggling
