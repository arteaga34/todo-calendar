# Todo Calendar

A native macOS app that combines Google Calendar with Things 3 for seamless task management.

![Brutalist Design](https://img.shields.io/badge/design-brutalist-black)
![Python](https://img.shields.io/badge/python-3.9+-blue)
![macOS](https://img.shields.io/badge/platform-macOS-lightgrey)

## Features

- **Weekly Calendar View** - See your entire week at a glance
- **Natural Language Input** - Add tasks with "tomorrow 2pm" or "friday 9am - 11am"
- **Drag & Drop** - Reschedule events by dragging them
- **Things 3 Integration** - Tasks automatically added to your Today list
- **Google Calendar Sync** - Events sync with your Google Calendar
- **All-Day Events** - Displayed in a dedicated bar
- **Current Time Indicator** - Red line showing "now"
- **Auto-Scroll** - Calendar scrolls to current time on launch

## Setup

### 1. Install Dependencies

```bash
pip3 install pywebview google-api-python-client google-auth-oauthlib dateparser
```

### 2. Get Google Calendar API Credentials

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project (or select existing)
3. Enable "Google Calendar API" in APIs & Services
4. Go to Credentials → Create Credentials → OAuth client ID
5. Choose "Desktop app" as application type
6. Download the JSON file and rename to `credentials.json`
7. Place it in the project directory

### 3. Run the App

```bash
python3 todo_calendar_gui.py
```

First run will open a browser for Google authorization.

### 4. Build Standalone App (Optional)

```bash
pip3 install pyinstaller
python3 -m PyInstaller --name "Todo Calendar" --windowed --onedir --noconfirm todo_calendar_gui.py
cp credentials.json token.json dist/Todo\ Calendar.app/Contents/MacOS/
```

The app will be in `dist/Todo Calendar.app`

## Keyboard Shortcuts

- **⌘ + Enter** - Add task
- **Escape** - Close context menu

## Usage

- **Add Task** - Fill in task name, time, and duration, then press ⌘+Enter
- **Quick Times** - Click preset buttons like "In 30 min" or "Tomorrow 9am"
- **Edit Event** - Right-click → Edit
- **Delete Event** - Right-click → Delete
- **Move Event** - Drag and drop to new time/day
- **Navigate Weeks** - Use ← → buttons or click "Today" to return

## Requirements

- macOS 10.13+
- Python 3.9+
- Things 3 app (for task integration)
- Google account

## License

MIT
