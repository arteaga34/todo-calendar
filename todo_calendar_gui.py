#!/usr/bin/env python3
"""
Todo + Calendar GUI Application

A native macOS GUI app using pywebview that displays Google Calendar events
and allows adding tasks to both Things app and Google Calendar.
"""

import json
import os
import subprocess
import sys
import threading
from datetime import datetime, timedelta
from pathlib import Path

import dateparser
import webview
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# Google Calendar API scope
SCOPES = ['https://www.googleapis.com/auth/calendar']

# File paths - handle PyInstaller bundle
if getattr(sys, 'frozen', False):
    # Running as PyInstaller bundle
    SCRIPT_DIR = Path(sys.executable).parent
else:
    # Running as script
    SCRIPT_DIR = Path(__file__).parent

CREDENTIALS_FILE = SCRIPT_DIR / 'credentials.json'
TOKEN_FILE = SCRIPT_DIR / 'token.json'


class CalendarAPI:
    """API class exposed to JavaScript."""

    def __init__(self):
        self.service = None
        self.window = None

    def set_window(self, window):
        self.window = window

    def init_calendar(self):
        """Initialize Google Calendar service."""
        try:
            self.service = self.get_google_calendar_service()
            return {"success": True, "message": "Connected to Google Calendar"}
        except Exception as e:
            return {"success": False, "message": str(e)}

    def get_google_calendar_service(self):
        """Authenticate and return Google Calendar service."""
        creds = None

        if TOKEN_FILE.exists():
            creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                if not CREDENTIALS_FILE.exists():
                    raise Exception(f"credentials.json not found in {SCRIPT_DIR}")

                flow = InstalledAppFlow.from_client_secrets_file(
                    str(CREDENTIALS_FILE), SCOPES
                )
                creds = flow.run_local_server(port=0)

            with open(TOKEN_FILE, 'w') as token:
                token.write(creds.to_json())

        return build('calendar', 'v3', credentials=creds)

    def get_events(self, week_offset=0):
        """Fetch events for a week. week_offset: 0=current, -1=previous, 1=next"""
        if not self.service:
            return {"success": False, "events": [], "message": "Not connected"}

        try:
            today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            monday = today - timedelta(days=today.weekday()) + timedelta(weeks=week_offset)
            sunday = monday + timedelta(days=6, hours=23, minutes=59, seconds=59)

            events_result = self.service.events().list(
                calendarId='primary',
                timeMin=monday.isoformat() + 'Z',
                timeMax=sunday.isoformat() + 'Z',
                singleEvents=True,
                orderBy='startTime'
            ).execute()

            events = []
            for event in events_result.get('items', []):
                start = event['start'].get('dateTime', event['start'].get('date'))
                end = event['end'].get('dateTime', event['end'].get('date'))

                events.append({
                    'id': event.get('id'),
                    'title': event.get('summary', 'No title'),
                    'start': start,
                    'end': end,
                    'isAllDay': 'T' not in start
                })

            return {
                "success": True,
                "events": events,
                "weekStart": monday.isoformat(),
                "weekEnd": sunday.isoformat(),
                "weekOffset": week_offset
            }
        except Exception as e:
            return {"success": False, "events": [], "message": str(e)}

    def delete_event(self, event_id):
        """Delete an event from Google Calendar."""
        if not self.service:
            return {"success": False, "message": "Not connected"}

        try:
            self.service.events().delete(calendarId='primary', eventId=event_id).execute()
            return {"success": True}
        except Exception as e:
            return {"success": False, "message": str(e)}

    def move_event(self, event_id, new_start_iso, duration_minutes):
        """Move an event to a new time."""
        if not self.service:
            return {"success": False, "message": "Not connected"}

        try:
            # Get the existing event
            event = self.service.events().get(calendarId='primary', eventId=event_id).execute()

            # Calculate new end time
            new_start = datetime.fromisoformat(new_start_iso.replace('Z', ''))
            new_end = new_start + timedelta(minutes=int(duration_minutes))

            # Update times
            event['start'] = {
                'dateTime': new_start.isoformat(),
                'timeZone': 'America/Los_Angeles'
            }
            event['end'] = {
                'dateTime': new_end.isoformat(),
                'timeZone': 'America/Los_Angeles'
            }

            # Update the event
            self.service.events().update(calendarId='primary', eventId=event_id, body=event).execute()
            return {"success": True}
        except Exception as e:
            return {"success": False, "message": str(e)}

    def parse_time(self, time_input):
        """Parse natural language time input, including time ranges."""
        import re

        # Check for time range patterns like "9am - 11", "9am to 11am", "9:00 - 11:00"
        range_pattern = r'(.+?)\s*[-–to]+\s*(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)\s*$'
        match = re.search(range_pattern, time_input, re.IGNORECASE)

        duration_minutes = None

        if match:
            start_part = match.group(1).strip()
            end_part = match.group(2).strip()

            # Parse start time
            start_parsed = dateparser.parse(
                start_part,
                settings={'PREFER_DATES_FROM': 'future'}
            )

            if start_parsed:
                # Parse end time - use the start date as context
                # If end_part doesn't have am/pm, infer from start
                if not re.search(r'(am|pm)', end_part, re.IGNORECASE):
                    # Add the same am/pm as start
                    start_hour = start_parsed.hour
                    if start_hour < 12:
                        end_part += 'am'
                    else:
                        end_part += 'pm'

                # Parse end time using start date as base
                end_str = start_parsed.strftime('%Y-%m-%d') + ' ' + end_part
                end_parsed = dateparser.parse(
                    end_str,
                    settings={'PREFER_DATES_FROM': 'future'}
                )

                if end_parsed:
                    # If end time is before start, assume it's the next period (am->pm)
                    if end_parsed <= start_parsed:
                        end_parsed = end_parsed.replace(hour=end_parsed.hour + 12)

                    # Calculate duration
                    duration_minutes = int((end_parsed - start_parsed).total_seconds() / 60)

                    return {
                        "success": True,
                        "parsed": start_parsed.strftime('%A, %B %d at %I:%M %p'),
                        "iso": start_parsed.isoformat(),
                        "duration": duration_minutes
                    }

        # Standard parsing (no range)
        parsed = dateparser.parse(
            time_input,
            settings={'PREFER_DATES_FROM': 'future'}
        )
        if parsed:
            return {
                "success": True,
                "parsed": parsed.strftime('%A, %B %d at %I:%M %p'),
                "iso": parsed.isoformat(),
                "duration": None
            }
        return {"success": False, "message": "Could not parse time"}

    def add_task(self, task_name, time_iso, duration_minutes):
        """Add task to Things and Google Calendar."""
        try:
            start_time = datetime.fromisoformat(time_iso)
            duration = int(duration_minutes) if duration_minutes else 60

            # Add to Things
            things_ok = self.add_to_things(task_name, start_time)

            # Add to Calendar
            calendar_ok = self.add_to_calendar(task_name, start_time, duration)

            return {
                "success": things_ok and calendar_ok,
                "things": things_ok,
                "calendar": calendar_ok
            }
        except Exception as e:
            return {"success": False, "message": str(e)}

    def add_to_things(self, task_name, scheduled_time):
        """Add task to Things app."""
        time_str = scheduled_time.strftime('%I:%M %p on %A, %B %d').lstrip('0')
        notes = f"Scheduled: {time_str}"

        task_name_escaped = task_name.replace('"', '\\"')
        notes_escaped = notes.replace('"', '\\"')

        applescript = f'''
        tell application "Things3"
            set newToDo to make new to do with properties {{name:"{task_name_escaped}", notes:"{notes_escaped}"}}
            move newToDo to list "Today"
        end tell
        '''

        try:
            subprocess.run(['osascript', '-e', applescript], check=True, capture_output=True)
            return True
        except:
            return False

    def add_to_calendar(self, task_name, start_time, duration_minutes):
        """Add event to Google Calendar."""
        if not self.service:
            return False

        try:
            end_time = start_time + timedelta(minutes=duration_minutes)

            event = {
                'summary': task_name,
                'start': {
                    'dateTime': start_time.isoformat(),
                    'timeZone': 'America/Los_Angeles',
                },
                'end': {
                    'dateTime': end_time.isoformat(),
                    'timeZone': 'America/Los_Angeles',
                },
            }

            self.service.events().insert(calendarId='primary', body=event).execute()
            return True
        except:
            return False


HTML = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>AGENDA</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Bebas+Neue&family=Space+Mono:wght@400;700&display=swap" rel="stylesheet">
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }

        :root {
            --black: #000000;
            --white: #ffffff;
            --mint: #3DFFC0;
            --mint-dim: rgba(61, 255, 192, 0.1);
            --mint-medium: rgba(61, 255, 192, 0.3);
            --gray-dark: #111111;
            --gray-mid: #1a1a1a;
            --gray-light: #333333;
            --gray-text: #666666;
        }

        body {
            font-family: 'Space Mono', monospace;
            background: var(--black);
            color: var(--white);
            min-height: 100vh;
            overflow: hidden;
        }

        .container {
            display: grid;
            grid-template-columns: 1fr 400px;
            grid-template-rows: auto 1fr;
            height: 100vh;
        }

        /* Header */
        .header {
            grid-column: 1 / -1;
            padding: 20px 32px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            border-bottom: 2px solid var(--white);
        }

        .brand {
            display: flex;
            align-items: center;
            gap: 24px;
        }

        .brand h1 {
            font-family: 'Bebas Neue', sans-serif;
            font-size: 48px;
            letter-spacing: 0.1em;
            line-height: 1;
        }

        .status {
            font-size: 10px;
            text-transform: uppercase;
            letter-spacing: 0.2em;
            color: var(--gray-text);
            display: flex;
            align-items: center;
            gap: 8px;
            padding: 8px 12px;
            border: 1px solid var(--gray-light);
        }

        .status-dot {
            width: 8px;
            height: 8px;
            background: var(--gray-text);
        }

        .status.connected .status-dot {
            background: var(--mint);
            box-shadow: 0 0 10px var(--mint);
        }

        .status.connected {
            border-color: var(--mint);
            color: var(--mint);
        }

        .header-right {
            display: flex;
            align-items: center;
            gap: 16px;
        }

        .week-nav {
            display: flex;
            align-items: center;
            gap: 8px;
        }

        .nav-btn {
            background: transparent;
            border: 2px solid var(--gray-light);
            color: var(--white);
            width: 36px;
            height: 36px;
            font-size: 16px;
            cursor: pointer;
            transition: all 0.15s;
            display: flex;
            align-items: center;
            justify-content: center;
        }

        .nav-btn:hover {
            border-color: var(--mint);
            color: var(--mint);
        }

        .today-btn {
            background: transparent;
            border: 2px solid var(--gray-light);
            color: var(--white);
            padding: 8px 16px;
            font-size: 10px;
            font-family: 'Space Mono', monospace;
            font-weight: 700;
            letter-spacing: 0.1em;
            text-transform: uppercase;
            cursor: pointer;
            transition: all 0.15s;
        }

        .today-btn:hover {
            border-color: var(--mint);
            color: var(--mint);
        }

        .today-btn.hidden {
            opacity: 0;
            pointer-events: none;
        }

        .week-label {
            font-family: 'Bebas Neue', sans-serif;
            font-size: 24px;
            letter-spacing: 0.05em;
            color: var(--gray-text);
            min-width: 280px;
            text-align: center;
        }

        .refresh-btn {
            background: transparent;
            border: 2px solid var(--white);
            color: var(--white);
            padding: 12px 24px;
            font-size: 10px;
            font-family: 'Space Mono', monospace;
            font-weight: 700;
            letter-spacing: 0.15em;
            text-transform: uppercase;
            cursor: pointer;
            transition: all 0.15s;
        }

        .refresh-btn:hover {
            background: var(--white);
            color: var(--black);
        }

        /* Calendar Section */
        .calendar-section {
            padding: 24px 32px;
            overflow: hidden;
            display: flex;
            flex-direction: column;
            border-right: 2px solid var(--white);
        }

        .days-header {
            display: grid;
            grid-template-columns: 80px repeat(7, 1fr);
            border-bottom: 2px solid var(--white);
            margin-bottom: 0;
        }

        .day-header {
            text-align: center;
            padding: 16px 8px;
            border-right: 1px solid var(--gray-light);
        }

        .day-header:last-child {
            border-right: none;
        }

        /* All-day events bar */
        .all-day-bar {
            display: grid;
            grid-template-columns: 80px repeat(7, 1fr);
            border-bottom: 1px solid var(--gray-light);
            min-height: 32px;
        }

        .all-day-label {
            font-size: 9px;
            color: var(--gray-text);
            text-align: right;
            padding: 8px 12px 8px 8px;
            border-right: 1px solid var(--gray-light);
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }

        .all-day-cell {
            padding: 4px;
            border-right: 1px solid var(--gray-light);
            display: flex;
            flex-wrap: wrap;
            gap: 2px;
            align-content: flex-start;
        }

        .all-day-cell:last-child {
            border-right: none;
        }

        .all-day-event {
            background: #4285f4;
            color: var(--white);
            padding: 2px 6px;
            font-size: 9px;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
            max-width: 100%;
            cursor: pointer;
        }

        .all-day-event:hover {
            opacity: 0.8;
        }

        .day-name {
            font-size: 10px;
            text-transform: uppercase;
            letter-spacing: 0.2em;
            color: var(--gray-text);
            margin-bottom: 4px;
        }

        .day-number {
            font-family: 'Bebas Neue', sans-serif;
            font-size: 36px;
            color: var(--white);
            line-height: 1;
        }

        .day-header.today {
            background: var(--mint);
        }

        .day-header.today .day-name,
        .day-header.today .day-number {
            color: var(--black);
        }

        .time-grid {
            flex: 1;
            overflow-y: auto;
            display: grid;
            grid-template-columns: 80px repeat(7, 1fr);
        }

        .time-grid::-webkit-scrollbar {
            width: 8px;
        }

        .time-grid::-webkit-scrollbar-track {
            background: var(--black);
        }

        .time-grid::-webkit-scrollbar-thumb {
            background: var(--gray-light);
        }

        .time-grid::-webkit-scrollbar-thumb:hover {
            background: var(--mint);
        }

        .time-labels {
            display: flex;
            flex-direction: column;
        }

        .time-label {
            height: 60px;
            font-size: 10px;
            color: var(--gray-text);
            text-align: right;
            padding: 4px 12px 0 8px;
            border-right: 1px solid var(--gray-light);
            border-bottom: 1px solid var(--gray-light);
            font-weight: 700;
            letter-spacing: 0.05em;
        }

        .time-label.current {
            color: var(--mint);
            background: var(--mint-dim);
        }

        .day-column {
            position: relative;
            border-right: 1px solid var(--gray-light);
        }

        .day-column:last-child {
            border-right: none;
        }

        .hour-line {
            height: 60px;
            border-bottom: 1px solid var(--gray-light);
        }

        /* Current time indicator */
        .current-time-line {
            position: absolute;
            left: 0;
            right: 0;
            height: 2px;
            background: #ff4444;
            z-index: 50;
            pointer-events: none;
        }

        .current-time-line::before {
            content: '';
            position: absolute;
            left: -4px;
            top: -4px;
            width: 10px;
            height: 10px;
            background: #ff4444;
            border-radius: 50%;
        }

        .event {
            position: absolute;
            left: 2px;
            right: 2px;
            padding: 4px 6px;
            font-size: 10px;
            border-left: 3px solid;
            background: var(--gray-mid);
            overflow: hidden;
            z-index: 1;
            transition: box-shadow 0.1s, background 0.1s;
            cursor: grab;
        }

        .event:active {
            cursor: grabbing;
        }

        .event.dragging {
            opacity: 0.7;
            z-index: 100;
            box-shadow: 0 4px 20px rgba(0,0,0,0.5);
        }

        .day-column.drag-over {
            background: var(--mint-dim);
        }

        .event:hover {
            z-index: 10;
            background: var(--gray-light);
            box-shadow: 0 2px 8px rgba(0,0,0,0.5);
        }

        /* Event tooltip */
        .event-tooltip {
            position: fixed;
            background: var(--gray-dark);
            border: 2px solid var(--mint);
            padding: 12px 16px;
            z-index: 1000;
            max-width: 300px;
            pointer-events: none;
            opacity: 0;
            transition: opacity 0.15s;
        }

        .event-tooltip.show {
            opacity: 1;
        }

        .event-tooltip .tooltip-title {
            font-weight: 700;
            margin-bottom: 8px;
            font-size: 13px;
        }

        .event-tooltip .tooltip-time {
            font-size: 11px;
            color: var(--mint);
            margin-bottom: 8px;
        }

        .event-tooltip .tooltip-hint {
            font-size: 10px;
            color: var(--gray-text);
            text-transform: uppercase;
            letter-spacing: 0.1em;
        }

        /* Context menu */
        .context-menu {
            position: fixed;
            background: var(--gray-dark);
            border: 2px solid var(--mint);
            z-index: 1001;
            min-width: 150px;
            display: none;
        }

        .context-menu.show {
            display: block;
        }

        .context-menu-item {
            padding: 12px 16px;
            font-size: 12px;
            font-family: 'Space Mono', monospace;
            cursor: pointer;
            transition: all 0.1s;
            text-transform: uppercase;
            letter-spacing: 0.1em;
        }

        .context-menu-item:hover {
            background: var(--mint);
            color: var(--black);
        }

        .context-menu-item.delete {
            color: #ff6b6b;
        }

        .context-menu-item.delete:hover {
            background: #ff6b6b;
            color: var(--white);
        }

        .event .event-time {
            font-weight: 700;
            margin-bottom: 2px;
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }

        .event .event-title {
            font-weight: 400;
            line-height: 1.2;
        }

        /* Sidebar */
        .sidebar {
            background: var(--gray-dark);
            padding: 24px;
            display: flex;
            flex-direction: column;
            overflow-y: auto;
        }

        .sidebar-section {
            margin-bottom: 32px;
        }

        .section-title {
            font-family: 'Bebas Neue', sans-serif;
            font-size: 32px;
            letter-spacing: 0.1em;
            color: var(--white);
            margin-bottom: 24px;
            padding-bottom: 12px;
            border-bottom: 2px solid var(--mint);
        }

        .form-group {
            margin-bottom: 20px;
        }

        .form-group label {
            display: block;
            font-size: 10px;
            text-transform: uppercase;
            letter-spacing: 0.2em;
            color: var(--gray-text);
            margin-bottom: 8px;
            font-weight: 700;
        }

        .form-group input {
            width: 100%;
            padding: 14px 16px;
            border: 2px solid var(--gray-light);
            background: var(--black);
            color: var(--white);
            font-size: 14px;
            font-family: 'Space Mono', monospace;
            transition: all 0.15s;
        }

        .form-group input:focus {
            outline: none;
            border-color: var(--mint);
        }

        .form-group input::placeholder {
            color: var(--gray-text);
        }

        .parsed-time {
            font-size: 11px;
            color: var(--mint);
            margin-top: 8px;
            min-height: 18px;
            font-weight: 700;
        }

        .parsed-time.error {
            color: #ff6b6b;
        }

        /* Quick time buttons */
        .quick-times {
            display: flex;
            flex-wrap: wrap;
            gap: 6px;
            margin-top: 12px;
        }

        .quick-time-btn {
            background: transparent;
            border: 1px solid var(--gray-light);
            color: var(--gray-text);
            padding: 6px 10px;
            font-size: 9px;
            font-family: 'Space Mono', monospace;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            cursor: pointer;
            transition: all 0.15s;
        }

        .quick-time-btn:hover {
            border-color: var(--mint);
            color: var(--mint);
        }

        .add-btn {
            width: 100%;
            padding: 16px;
            background: var(--mint);
            border: none;
            color: var(--black);
            font-size: 12px;
            font-weight: 700;
            font-family: 'Space Mono', monospace;
            letter-spacing: 0.15em;
            text-transform: uppercase;
            cursor: pointer;
            transition: all 0.15s;
        }

        .add-btn:hover {
            background: var(--white);
            box-shadow: 0 0 30px var(--mint-medium);
        }

        .add-btn:disabled {
            background: var(--gray-light);
            color: var(--gray-text);
            cursor: not-allowed;
            box-shadow: none;
        }

        /* Today's Schedule */
        .today-schedule {
            flex: 1;
            overflow-y: auto;
        }

        .schedule-item {
            padding: 16px;
            margin-bottom: 8px;
            background: var(--black);
            border-left: 4px solid;
            transition: all 0.1s;
        }

        .schedule-item:hover {
            transform: translateX(8px);
        }

        .schedule-time {
            font-size: 10px;
            color: var(--gray-text);
            text-transform: uppercase;
            letter-spacing: 0.1em;
            margin-bottom: 4px;
            font-weight: 700;
        }

        .schedule-status {
            display: inline-block;
            margin-right: 8px;
        }

        .schedule-title {
            font-size: 13px;
            font-weight: 400;
            color: var(--white);
            line-height: 1.4;
        }

        .no-events {
            color: var(--gray-text);
            padding: 32px 0;
            text-align: center;
            font-size: 12px;
            text-transform: uppercase;
            letter-spacing: 0.1em;
        }

        /* Toast */
        .toast {
            position: fixed;
            bottom: 24px;
            right: 24px;
            background: var(--gray-dark);
            color: var(--white);
            padding: 16px 24px;
            border: 2px solid var(--mint);
            font-size: 12px;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.1em;
            opacity: 0;
            transform: translateY(20px);
            transition: all 0.2s;
            z-index: 100;
        }

        .toast.show {
            opacity: 1;
            transform: translateY(0);
        }

        .toast.error {
            border-color: #ff6b6b;
        }

        /* Event colors - Google Calendar style */
        .event-normal { border-color: #4285f4; color: #4285f4; }
        .event-completed { border-color: #0f9d58; color: #0f9d58; }
    </style>
</head>
<body>
    <div class="container">
        <header class="header">
            <div class="brand">
                <h1>AGENDA</h1>
                <div class="status" id="status">
                    <span class="status-dot"></span>
                    <span>OFFLINE</span>
                </div>
            </div>
            <div class="header-right">
                <div class="week-nav">
                    <button class="nav-btn" onclick="navigateWeek(-1)" title="Previous week">←</button>
                    <button class="today-btn" id="todayBtn" onclick="goToToday()">TODAY</button>
                    <button class="nav-btn" onclick="navigateWeek(1)" title="Next week">→</button>
                </div>
                <span class="week-label" id="weekLabel"></span>
                <button class="refresh-btn" onclick="refreshCalendar()">REFRESH</button>
            </div>
        </header>

        <section class="calendar-section">
            <div class="days-header" id="daysHeader"></div>
            <div class="all-day-bar" id="allDayBar"></div>
            <div class="time-grid" id="timeGrid"></div>
        </section>

        <!-- Event tooltip -->
        <div class="event-tooltip" id="eventTooltip">
            <div class="tooltip-title"></div>
            <div class="tooltip-time"></div>
            <div class="tooltip-hint">Right-click for options</div>
        </div>

        <!-- Context menu -->
        <div class="context-menu" id="contextMenu">
            <div class="context-menu-item" onclick="editEvent()">Edit</div>
            <div class="context-menu-item delete" onclick="deleteEvent()">Delete</div>
        </div>

        <aside class="sidebar">
            <div class="sidebar-section">
                <h2 class="section-title">NEW TASK</h2>

                <div class="form-group">
                    <label>Task</label>
                    <input type="text" id="taskName" placeholder="What needs to be done?">
                </div>

                <div class="form-group">
                    <label>When</label>
                    <input type="text" id="taskTime" placeholder="tomorrow 2pm">
                    <div class="parsed-time" id="parsedTime"></div>
                    <div class="quick-times">
                        <button class="quick-time-btn" onclick="setQuickTime('in 30 minutes')">In 30 min</button>
                        <button class="quick-time-btn" onclick="setQuickTime('in 1 hour')">In 1 hour</button>
                        <button class="quick-time-btn" onclick="setQuickTime('tomorrow 9am')">Tomorrow 9am</button>
                        <button class="quick-time-btn" onclick="setQuickTime('next monday 9am')">Next Monday</button>
                    </div>
                </div>

                <div class="form-group">
                    <label>Duration (min)</label>
                    <input type="text" id="taskDuration" placeholder="60" style="width: 120px;">
                    <div class="quick-times" style="margin-top: 8px;">
                        <button class="quick-time-btn" onclick="setDuration(30)">30m</button>
                        <button class="quick-time-btn" onclick="setDuration(60)">1hr</button>
                        <button class="quick-time-btn" onclick="setDuration(90)">1.5hr</button>
                        <button class="quick-time-btn" onclick="setDuration(120)">2hr</button>
                        <button class="quick-time-btn" onclick="setDuration(180)">3hr</button>
                    </div>
                </div>

                <button class="add-btn" id="addBtn" onclick="addTask()">ADD TASK (⌘+ENTER)</button>
            </div>

            <div class="sidebar-section" style="flex: 1; display: flex; flex-direction: column;">
                <h2 class="section-title">TODAY</h2>
                <div class="today-schedule" id="todaySchedule">
                    <div class="no-events">LOADING...</div>
                </div>
            </div>
        </aside>
    </div>

    <div class="toast" id="toast"></div>

    <script>
        let parsedTimeISO = null;
        let currentWeekOffset = 0;

        // Initialize
        async function init() {
            const result = await pywebview.api.init_calendar();
            const status = document.getElementById('status');

            if (result.success) {
                status.innerHTML = '<span class="status-dot"></span><span>ONLINE</span>';
                status.classList.add('connected');
                refreshCalendar();
            } else {
                status.innerHTML = '<span class="status-dot"></span><span>OFFLINE</span>';
            }
        }

        // Week navigation
        async function navigateWeek(direction) {
            currentWeekOffset += direction;
            await refreshCalendar();
        }

        async function goToToday() {
            currentWeekOffset = 0;
            await refreshCalendar();
        }

        // Quick time buttons
        async function setQuickTime(timeStr) {
            document.getElementById('taskTime').value = timeStr;
            // Trigger parsing
            const result = await pywebview.api.parse_time(timeStr);
            const parsedDiv = document.getElementById('parsedTime');
            if (result.success) {
                parsedDiv.textContent = '→ ' + result.parsed;
                parsedDiv.classList.remove('error');
                parsedTimeISO = result.iso;
                if (result.duration) {
                    document.getElementById('taskDuration').value = result.duration;
                }
            }
        }

        // Refresh calendar
        async function refreshCalendar() {
            const result = await pywebview.api.get_events(currentWeekOffset);

            if (result.success) {
                const weekStart = new Date(result.weekStart);
                const weekEnd = new Date(result.weekEnd);
                document.getElementById('weekLabel').textContent =
                    weekStart.toLocaleDateString('en-US', { month: 'long', day: 'numeric' }) +
                    ' — ' +
                    weekEnd.toLocaleDateString('en-US', { month: 'long', day: 'numeric' });

                // Show/hide Today button
                const todayBtn = document.getElementById('todayBtn');
                todayBtn.classList.toggle('hidden', currentWeekOffset === 0);

                renderCalendar(result.events, weekStart, currentWeekOffset);
            }
        }

        // Render calendar grid
        function renderCalendar(events, weekStart, weekOffset = 0) {
            const now = new Date();
            const todayIdx = now.getDay() === 0 ? 6 : now.getDay() - 1;
            const isCurrentWeek = weekOffset === 0;
            const START_HOUR = 6;
            const END_HOUR = 22;
            const HOUR_HEIGHT = 60; // pixels per hour

            // Days header
            const daysHeader = document.getElementById('daysHeader');
            daysHeader.innerHTML = '<div></div>';
            const dayNames = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

            for (let i = 0; i < 7; i++) {
                const date = new Date(weekStart);
                date.setDate(date.getDate() + i);
                const isToday = isCurrentWeek && i === todayIdx;

                daysHeader.innerHTML += `
                    <div class="day-header ${isToday ? 'today' : ''}">
                        <div class="day-name">${dayNames[i]}</div>
                        <div class="day-number">${date.getDate()}</div>
                    </div>
                `;
            }

            // Group events by day (separate all-day and timed events)
            const eventsByDay = [[], [], [], [], [], [], []];
            const allDayByDay = [[], [], [], [], [], [], []];

            events.forEach(event => {
                const start = new Date(event.start);
                const end = new Date(event.end);
                const dayIdx = Math.floor((start - weekStart) / (24 * 60 * 60 * 1000));
                const isCompleted = end < now;

                if (dayIdx >= 0 && dayIdx < 7) {
                    if (event.isAllDay) {
                        allDayByDay[dayIdx].push({
                            ...event,
                            colorClass: isCompleted ? 'event-completed' : 'event-normal'
                        });
                    } else {
                        eventsByDay[dayIdx].push({
                            ...event,
                            startDate: start,
                            endDate: end,
                            colorClass: isCompleted ? 'event-completed' : 'event-normal'
                        });
                    }
                }
            });

            // Render all-day events bar
            const allDayBar = document.getElementById('allDayBar');
            const hasAllDay = allDayByDay.some(day => day.length > 0);
            allDayBar.style.display = hasAllDay ? 'grid' : 'none';

            if (hasAllDay) {
                allDayBar.innerHTML = '<div class="all-day-label">All Day</div>';
                for (let day = 0; day < 7; day++) {
                    let cellHTML = '<div class="all-day-cell">';
                    allDayByDay[day].forEach(event => {
                        const title = event.title.length > 12 ? event.title.substring(0, 12) + '…' : event.title;
                        cellHTML += `<div class="all-day-event" title="${event.title}">${title}</div>`;
                    });
                    cellHTML += '</div>';
                    allDayBar.innerHTML += cellHTML;
                }
            }

            // Time grid
            const timeGrid = document.getElementById('timeGrid');
            timeGrid.innerHTML = '';

            // Time labels column
            let timeLabelsHTML = '<div class="time-labels">';
            for (let hour = START_HOUR; hour <= END_HOUR; hour++) {
                const isCurrent = isCurrentWeek && now.getHours() === hour;
                const timeStr = hour === 0 ? '12 AM' : hour < 12 ? `${hour} AM` : hour === 12 ? '12 PM' : `${hour - 12} PM`;
                timeLabelsHTML += `<div class="time-label ${isCurrent ? 'current' : ''}">${timeStr}</div>`;
            }
            timeLabelsHTML += '</div>';
            timeGrid.innerHTML += timeLabelsHTML;

            // Render day columns with absolutely positioned events
            for (let day = 0; day < 7; day++) {
                // Calculate the date for this column
                const colDate = new Date(weekStart);
                colDate.setDate(colDate.getDate() + day);
                const colDateStr = colDate.toISOString().split('T')[0];

                let columnHTML = `<div class="day-column" data-day="${day}" data-date="${colDateStr}">`;

                // Hour lines (background grid)
                for (let hour = START_HOUR; hour <= END_HOUR; hour++) {
                    columnHTML += '<div class="hour-line"></div>';
                }

                // Current time indicator (only on today's column in current week)
                if (isCurrentWeek && day === todayIdx) {
                    const currentHour = now.getHours();
                    const currentMin = now.getMinutes();
                    if (currentHour >= START_HOUR && currentHour <= END_HOUR) {
                        const timeOffset = (currentHour - START_HOUR) + (currentMin / 60);
                        const timeTop = timeOffset * HOUR_HEIGHT;
                        columnHTML += `<div class="current-time-line" style="top: ${timeTop}px;"></div>`;
                    }
                }

                // Events
                eventsByDay[day].forEach(event => {
                    const startHour = event.startDate.getHours();
                    const startMin = event.startDate.getMinutes();
                    const endHour = event.endDate.getHours();
                    const endMin = event.endDate.getMinutes();

                    // Calculate position from START_HOUR
                    const startOffset = (startHour - START_HOUR) + (startMin / 60);
                    const endOffset = (endHour - START_HOUR) + (endMin / 60);
                    const duration = endOffset - startOffset;

                    // Skip events outside visible range
                    if (endOffset < 0 || startOffset > (END_HOUR - START_HOUR + 1)) return;

                    const top = Math.max(0, startOffset * HOUR_HEIGHT);
                    const height = Math.max(20, duration * HOUR_HEIGHT - 2);

                    const eventTime = event.startDate.toLocaleTimeString('en-US', {
                        hour: 'numeric',
                        minute: '2-digit'
                    });
                    const endTime = event.endDate.toLocaleTimeString('en-US', {
                        hour: 'numeric',
                        minute: '2-digit'
                    });

                    // Escape title for data attribute
                    const escapedTitle = event.title.replace(/"/g, '&quot;');
                    const durationMins = Math.round(duration * 60);

                    columnHTML += `
                        <div class="event ${event.colorClass}"
                             style="top: ${top}px; height: ${height}px;"
                             draggable="true"
                             data-id="${event.id}"
                             data-title="${escapedTitle}"
                             data-start="${eventTime}"
                             data-end="${endTime}"
                             data-duration="${durationMins}"
                             data-start-iso="${event.start}"
                             onmouseenter="showTooltip(event, this)"
                             onmouseleave="hideTooltip()"
                             oncontextmenu="showContextMenu(event, this)"
                             ondragstart="handleDragStart(event, this)"
                             ondragend="handleDragEnd(event, this)">
                            <div class="event-time">${eventTime}</div>
                            <div class="event-title">${event.title}</div>
                        </div>
                    `;
                });

                columnHTML += '</div>';
                timeGrid.innerHTML += columnHTML;
            }

            // Setup drop zones on day columns
            setupDropZones();

            // Auto-scroll to current time (only on current week)
            if (isCurrentWeek) {
                const currentHour = now.getHours();
                if (currentHour >= START_HOUR && currentHour <= END_HOUR) {
                    const scrollTo = ((currentHour - START_HOUR) - 2) * HOUR_HEIGHT;
                    setTimeout(() => {
                        timeGrid.scrollTop = Math.max(0, scrollTo);
                    }, 100);
                }
            }

            updateTodaySchedule(events, weekStart, todayIdx);
        }

        // Update today's schedule sidebar
        function updateTodaySchedule(events, weekStart, todayIdx) {
            const container = document.getElementById('todaySchedule');
            const now = new Date();

            const todayEvents = events.filter(event => {
                if (event.isAllDay) return false;
                const start = new Date(event.start);
                const dayIdx = Math.floor((start - weekStart) / (24 * 60 * 60 * 1000));
                return dayIdx === todayIdx;
            }).sort((a, b) => new Date(a.start) - new Date(b.start));

            if (todayEvents.length === 0) {
                container.innerHTML = '<div class="no-events">Your day is clear</div>';
                return;
            }

            container.innerHTML = todayEvents.map(event => {
                const start = new Date(event.start);
                const end = new Date(event.end);
                const isCompleted = end < now;
                const color = isCompleted ? '#0f9d58' : '#4285f4';

                let status = '○';
                if (isCompleted) status = '✓';
                else if (start <= now && now <= end) status = '▸';

                const timeStr = start.toLocaleTimeString('en-US', {
                    hour: 'numeric',
                    minute: '2-digit'
                });

                return `
                    <div class="schedule-item" style="border-color: ${color}">
                        <div class="schedule-time">
                            <span class="schedule-status">${status}</span>
                            ${timeStr}
                        </div>
                        <div class="schedule-title">${event.title}</div>
                    </div>
                `;
            }).join('');
        }

        // Parse time input
        let parseTimeout;
        document.getElementById('taskTime').addEventListener('input', function() {
            clearTimeout(parseTimeout);
            parseTimeout = setTimeout(async () => {
                const value = this.value.trim();
                const parsedDiv = document.getElementById('parsedTime');

                if (!value) {
                    parsedDiv.textContent = '';
                    parsedTimeISO = null;
                    return;
                }

                const result = await pywebview.api.parse_time(value);
                if (result.success) {
                    parsedDiv.textContent = '→ ' + result.parsed;
                    parsedDiv.classList.remove('error');
                    parsedTimeISO = result.iso;

                    // Auto-fill duration if a time range was detected
                    if (result.duration) {
                        document.getElementById('taskDuration').value = result.duration;
                    }
                } else {
                    parsedDiv.textContent = 'Could not parse time';
                    parsedDiv.classList.add('error');
                    parsedTimeISO = null;
                }
            }, 300);
        });

        // Add task
        async function addTask() {
            const taskName = document.getElementById('taskName').value.trim();
            const duration = document.getElementById('taskDuration').value.trim() || '60';

            if (!taskName) {
                showToast('Please enter a task name', true);
                return;
            }

            if (!parsedTimeISO) {
                showToast('Please enter a valid time', true);
                return;
            }

            const btn = document.getElementById('addBtn');
            btn.disabled = true;
            btn.textContent = 'Adding...';

            const result = await pywebview.api.add_task(taskName, parsedTimeISO, duration);

            btn.disabled = false;
            btn.textContent = 'ADD TASK (⌘+ENTER)';

            if (result.success) {
                showToast('Task added successfully');
                document.getElementById('taskName').value = '';
                document.getElementById('taskTime').value = '';
                document.getElementById('taskDuration').value = '';
                document.getElementById('parsedTime').textContent = '';
                parsedTimeISO = null;
                refreshCalendar();
            } else {
                let msg = 'Failed to add task';
                if (!result.things) msg += ' (Things)';
                if (!result.calendar) msg += ' (Calendar)';
                showToast(msg, true);
            }
        }

        // Show toast notification
        function showToast(message, isError = false) {
            const toast = document.getElementById('toast');
            toast.textContent = message;
            toast.classList.toggle('error', isError);
            toast.classList.add('show');
            setTimeout(() => toast.classList.remove('show'), 3000);
        }

        // Event tooltip
        function showTooltip(e, element) {
            const tooltip = document.getElementById('eventTooltip');
            const title = element.dataset.title;
            const startTime = element.dataset.start;
            const endTime = element.dataset.end;

            tooltip.querySelector('.tooltip-title').textContent = title;
            tooltip.querySelector('.tooltip-time').textContent = `${startTime} — ${endTime}`;

            // Position tooltip near cursor
            const x = Math.min(e.clientX + 10, window.innerWidth - 320);
            const y = Math.min(e.clientY + 10, window.innerHeight - 100);
            tooltip.style.left = x + 'px';
            tooltip.style.top = y + 'px';

            tooltip.classList.add('show');
        }

        function hideTooltip() {
            document.getElementById('eventTooltip').classList.remove('show');
        }

        // Context menu for events
        let selectedEvent = null;

        function showContextMenu(e, element) {
            e.preventDefault();
            hideTooltip();

            selectedEvent = {
                id: element.dataset.id,
                title: element.dataset.title.replace(/&quot;/g, '"'),
                start: element.dataset.start,
                end: element.dataset.end,
                duration: element.dataset.duration,
                startIso: element.dataset.startIso
            };

            const menu = document.getElementById('contextMenu');
            const x = Math.min(e.clientX, window.innerWidth - 160);
            const y = Math.min(e.clientY, window.innerHeight - 100);
            menu.style.left = x + 'px';
            menu.style.top = y + 'px';
            menu.classList.add('show');
        }

        function hideContextMenu() {
            document.getElementById('contextMenu').classList.remove('show');
        }

        // Hide context menu when clicking elsewhere
        document.addEventListener('click', hideContextMenu);

        // Edit event - prefill form with event data
        function editEvent() {
            hideContextMenu();
            if (!selectedEvent) return;

            // Prefill the form
            document.getElementById('taskName').value = selectedEvent.title;
            document.getElementById('taskDuration').value = selectedEvent.duration;

            // Set parsed time display
            document.getElementById('parsedTime').textContent = `→ ${selectedEvent.start} — ${selectedEvent.end}`;
            parsedTimeISO = selectedEvent.startIso;

            // Delete the old event
            pywebview.api.delete_event(selectedEvent.id);
            showToast('Edit event and save');
        }

        // Delete event
        async function deleteEvent() {
            hideContextMenu();
            if (!selectedEvent) return;

            if (!confirm(`Delete "${selectedEvent.title}"?`)) return;

            const result = await pywebview.api.delete_event(selectedEvent.id);
            if (result.success) {
                showToast('Event deleted');
                refreshCalendar();
            } else {
                showToast('Failed to delete event', true);
            }
        }

        // Set duration preset
        function setDuration(minutes) {
            document.getElementById('taskDuration').value = minutes;
        }

        // Drag and drop functionality
        let draggedEvent = null;
        const START_HOUR = 6;
        const HOUR_HEIGHT = 60;

        function handleDragStart(e, element) {
            draggedEvent = {
                id: element.dataset.id,
                duration: parseInt(element.dataset.duration)
            };
            element.classList.add('dragging');
            hideTooltip();
            // Required for Firefox
            e.dataTransfer.setData('text/plain', element.dataset.id);
            e.dataTransfer.effectAllowed = 'move';
        }

        function handleDragEnd(e, element) {
            element.classList.remove('dragging');
            document.querySelectorAll('.day-column').forEach(col => {
                col.classList.remove('drag-over');
            });
        }

        function setupDropZones() {
            document.querySelectorAll('.day-column').forEach(column => {
                column.addEventListener('dragover', handleDragOver);
                column.addEventListener('dragleave', handleDragLeave);
                column.addEventListener('drop', handleDrop);
            });
        }

        function handleDragOver(e) {
            e.preventDefault();
            e.dataTransfer.dropEffect = 'move';
            this.classList.add('drag-over');
        }

        function handleDragLeave(e) {
            this.classList.remove('drag-over');
        }

        async function handleDrop(e) {
            e.preventDefault();
            this.classList.remove('drag-over');

            if (!draggedEvent) return;

            // Calculate new time based on drop position
            const rect = this.getBoundingClientRect();
            const y = e.clientY - rect.top;
            const hourOffset = y / HOUR_HEIGHT;
            const newHour = START_HOUR + Math.floor(hourOffset);
            const newMinutes = Math.round((hourOffset % 1) * 60 / 15) * 15; // Round to 15 min

            // Get the date from the column
            const dateStr = this.dataset.date;

            // Create new ISO datetime
            const newDateTime = `${dateStr}T${String(newHour).padStart(2, '0')}:${String(newMinutes).padStart(2, '0')}:00`;

            // Move the event
            const result = await pywebview.api.move_event(draggedEvent.id, newDateTime, draggedEvent.duration);

            if (result.success) {
                showToast('Event moved');
                refreshCalendar();
            } else {
                showToast('Failed to move event', true);
            }

            draggedEvent = null;
        }

        // Keyboard shortcut: Cmd+Enter to add task
        document.addEventListener('keydown', function(e) {
            if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
                e.preventDefault();
                addTask();
            }
            // Escape to close context menu
            if (e.key === 'Escape') {
                hideContextMenu();
            }
        });

        // Wait for pywebview to be ready
        window.addEventListener('pywebviewready', init);
    </script>
</body>
</html>
"""


def main():
    api = CalendarAPI()

    window = webview.create_window(
        'Todo + Calendar',
        html=HTML,
        js_api=api,
        width=1200,
        height=800,
        resizable=True,
        background_color='#1a1a2e'
    )

    api.set_window(window)
    webview.start()


if __name__ == '__main__':
    main()
