"""
Datetime tool — current local date and time.
Models don't know the current date; this gives it to them.
"""

from __future__ import annotations

from datetime import datetime

SCHEMA = {
    "type": "function",
    "function": {
        "name": "datetime",
        "description": (
            "Get the current local date and time. Use whenever the user asks about "
            "today's date, the current time, the day of the week, or anything time-relative "
            "(e.g. 'how many days until...', 'what day is it')."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}

_DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
_MONTHS = ["January", "February", "March", "April", "May", "June",
           "July", "August", "September", "October", "November", "December"]


def execute() -> str:
    now = datetime.now().astimezone()
    day = _DAYS[now.weekday()]
    month = _MONTHS[now.month - 1]
    return (
        f"Current date and time:\n"
        f"- {day}, {month} {now.day}, {now.year}\n"
        f"- Time: {now.strftime('%H:%M:%S')}\n"
        f"- ISO: {now.strftime('%Y-%m-%d %H:%M:%S %z')}\n"
        f"- Timezone: {now.tzname()}"
    )
