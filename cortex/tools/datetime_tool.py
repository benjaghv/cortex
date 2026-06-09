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

_DAYS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
_MONTHS = ["enero", "febrero", "marzo", "abril", "mayo", "junio",
           "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]


def execute() -> str:
    now = datetime.now().astimezone()
    day = _DAYS[now.weekday()]
    month = _MONTHS[now.month - 1]
    return (
        f"Fecha y hora actual:\n"
        f"- {day}, {now.day} de {month} de {now.year}\n"
        f"- Hora: {now.strftime('%H:%M:%S')}\n"
        f"- ISO: {now.strftime('%Y-%m-%d %H:%M:%S %z')}\n"
        f"- Zona horaria: {now.tzname()}"
    )
