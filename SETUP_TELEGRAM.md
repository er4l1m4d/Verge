# Telegram Bot Setup

## 1. Create Bot via @BotFather

1. Open Telegram, search for **@BotFather**
2. Send `/newbot`
3. Choose a name (e.g. "Verge Signals")
4. Copy the **BOT_TOKEN** (format: `123456789:ABCdefGHIjklMNOpqrsTUVwxyz`)

## 2. Get Your Chat ID

1. Send any message to your new bot (e.g. `/start`)
2. Visit: `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates`
3. Find your **CHAT_ID** in the `chat.id` field:
   ```json
   "chat": {
     "id": 6328240938,
     ...
   }
   ```

## 3. Set Credentials in Render

1. Go to Render dashboard → **verge-backend** → **Environment**
2. Add:
   - `TELEGRAM_BOT_TOKEN` = your token
   - `TELEGRAM_CHAT_ID` = your chat ID
3. Save and redeploy

## 4. Verify

1. Trigger a heartbeat: `GET /api/heartbeat` (with `X-Secret` header)
2. Check Telegram for a message
3. If no message, check Render logs for `Telegram alert failed`

## 5. Deep Linking

Each Telegram alert includes a "View Signal" button that opens the
signal detail modal directly in the dashboard. The URL format is:
```
https://vergesignals.vercel.app/#signal/<SIGNAL_ID>
```
