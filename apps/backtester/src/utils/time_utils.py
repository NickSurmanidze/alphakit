from datetime import UTC, datetime


def now(utc: bool = True) -> datetime:
    if utc:
        return datetime.utcnow().replace(tzinfo=UTC)
    else:
        return datetime.now()


def now_str_format(format: str = "%Y-%m-%d %H:%M:%S", utc: bool = False) -> str:
    return now(utc).strftime(format)


def now_iso_str(utc: bool = True, microseconds: bool = False) -> str:
    d: datetime = now(utc)
    if microseconds:
        if utc:
            return d.replace(microsecond=0).isoformat()
        else:
            return d.astimezone().replace(microsecond=0).isoformat()
    elif utc:
        return d.isoformat()
    else:
        return d.astimezone().isoformat()
