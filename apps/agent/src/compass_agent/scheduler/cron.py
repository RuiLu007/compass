"""Five-field Cron on IANA wall time. Store and compare only UTC instants."""

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from compass_agent.models.errors import ModelError

MONTHS = {
    name: n for n, name in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split(), 1)
}
WEEKDAYS = {name: n for n, name in enumerate("sun mon tue wed thu fri sat".split())}
DAY = timedelta(days=1)


def parse_field(value, low, high, names=None):
    def number(raw):
        if names and raw.lower() in names:
            return names[raw.lower()]
        if not raw.isascii() or not raw.isdecimal():
            raise ValueError("Invalid field")
        return int(raw)

    values = set()
    for part in value.split(","):
        field, divider, step_text = part.partition("/")
        step = number(step_text) if divider else 1
        if not 1 <= step <= high - low + 1:
            raise ValueError("Invalid step")
        if field == "*":
            first, last = low, high
        elif "-" in field:
            start, end = field.split("-", 1)
            first, last = number(start), number(end)
        elif not divider:
            first = last = number(field)
        else:
            raise ValueError("A step needs a wildcard or range")
        if not low <= first <= last <= high:
            raise ValueError("Invalid range")
        values.update(range(first, last + 1, step))
    return tuple(sorted(values))


@dataclass(frozen=True)
class Cron:
    zone: ZoneInfo
    times: tuple[time, ...]
    days: tuple[int, ...]
    months: tuple[int, ...]
    weekdays: frozenset[int]
    either_day: bool

    @classmethod
    def parse(cls, expression: str, zone: str):
        try:
            zone_info = ZoneInfo(zone)
        except (ValueError, ZoneInfoNotFoundError):
            raise ModelError("schedule_invalid_timezone", 422) from None
        try:
            minute, hour, day, month, weekday = expression.split()
            minutes = parse_field(minute, 0, 59)
            hours = parse_field(hour, 0, 23)
            return cls(
                zone_info,
                tuple(time(h, m) for h in hours for m in minutes),
                parse_field(day, 1, 31),
                parse_field(month, 1, 12, MONTHS),
                frozenset(n % 7 for n in parse_field(weekday, 0, 7, WEEKDAYS)),
                not (day.startswith("*") or weekday.startswith("*")),
            )
        except ValueError:
            raise ModelError("schedule_invalid_cron", 422) from None

    def matches_date(self, day: date):
        if day.month not in self.months:
            return False
        month_day = day.day in self.days
        weekday = (day.weekday() + 1) % 7 in self.weekdays
        return (month_day or weekday) if self.either_day else (month_day and weekday)

    def nearest(self, instant: datetime, *, previous: bool = False):
        day = instant.astimezone(self.zone).date()
        direction = -DAY if previous else DAY
        times = self.times[::-1] if previous else self.times
        # Includes the largest Gregorian leap-day gap across a century boundary.
        for _ in range(8 * 366 + 2):
            if self.matches_date(day):
                for point in times:
                    wall = datetime.combine(day, point, tzinfo=self.zone)
                    utc = wall.astimezone(UTC)
                    if utc.astimezone(self.zone).replace(tzinfo=None) != wall.replace(tzinfo=None):
                        continue
                    if (utc <= instant) if previous else (utc > instant):
                        return utc
            day += direction
        raise ModelError("schedule_invalid_cron", 422)

    def preview(self, now: datetime, count: int = 5):
        points = []
        for _ in range(count):
            now = self.nearest(now)
            points.append(now)
        return points
