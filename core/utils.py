import calendar
import datetime


def last_sunday(year, month):
    """Return the last Sunday of the given month (calculated, never hard-coded)."""
    last_day = calendar.monthrange(year, month)[1]
    date = datetime.date(year, month, last_day)
    return date - datetime.timedelta(days=(date.weekday() - calendar.SUNDAY) % 7)


def add_months(date, months):
    month_index = date.month - 1 + months
    year = date.year + month_index // 12
    month = month_index % 12 + 1
    day = min(date.day, calendar.monthrange(year, month)[1])
    return datetime.date(year, month, day)


def month_bounds(year, month):
    start = datetime.date(year, month, 1)
    end = datetime.date(year, month, calendar.monthrange(year, month)[1])
    return start, end
