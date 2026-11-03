"""Shared, read-only clinician comparison semantics. All calendar days are Sydney days."""
from dataclasses import dataclass
from datetime import date, datetime, time
from zoneinfo import ZoneInfo
from fastapi import HTTPException
from .. import care_team

SYDNEY = ZoneInfo('Australia/Sydney')


def instant(value):
    if not isinstance(value, str) or 'T' not in value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z','+00:00'))
        return parsed.astimezone(SYDNEY) if parsed.tzinfo is not None else None
    except ValueError:
        return None


def day(value):
    moment = instant(value)
    if moment:
        return moment.date()
    try:
        parsed = date.fromisoformat(value)
        return parsed if parsed.isoformat()==value else None
    except (ValueError, TypeError):
        return None


def appointment_time(appointment):
    for key in ('completed_at','consultation_timestamp'):
        moment = instant(appointment.get(key))
        if moment:
            return moment
    if appointment.get('time') and day(appointment.get('date')):
        try:
            wall = datetime.fromisoformat(appointment['date']+'T'+appointment['time']).replace(tzinfo=SYDNEY)
            # Reject impossible spring-forward wall times instead of normalising them.
            from datetime import timezone
            if wall.replace(fold=1).utcoffset() != wall.utcoffset():
                return None
            if wall.astimezone(timezone.utc).astimezone(SYDNEY).replace(tzinfo=None) != wall.replace(tzinfo=None):
                return None
            return wall
        except ValueError:
            pass
    return None


@dataclass(frozen=True)
class Period:
    now: datetime
    last: dict | None
    start_day: date | None
    start: datetime | None
    mode: str

    def position(self, value):
        """after / same-day-unknown / None. Unknown dates never acquire a timestamp."""
        if not self.start_day:
            return None
        moment, dated = instant(value), day(value)
        if dated is None or dated > self.now.date() or (moment and moment > self.now):
            return None
        if self.mode == 'custom':
            return 'after' if (moment >= self.start if moment else dated >= self.start_day) else None
        if moment and self.start:
            return 'after' if moment > self.start else None
        if dated == self.start_day:
            return 'same-day-unknown'
        return 'after' if dated > self.start_day else None

    @property
    def data(self):
        return {'mode':self.mode, 'timezone':'Australia/Sydney',
            'start_date':self.start_day.isoformat() if self.start_day else None,
            'start_timestamp':self.start.isoformat() if self.start else None,
            'end_timestamp':self.now.isoformat(),
            'precision':'timestamp' if self.start else 'date' if self.start_day else None,
            'boundary_note':('Custom period starts at 00:00 Australia/Sydney on the selected date.' if self.mode=='custom'
                else 'Compared after the recorded consultation timestamp. Date-only sources on that day have unknown ordering.' if self.start
                else 'Consultation time not recorded precisely enough to order same-day entries. Date-only comparison.' if self.start_day
                else 'First review of this patient. Select a comparison date.'),
            'default_date':(appointment_time(self.last).date().isoformat() if appointment_time(self.last) else self.last['date']) if self.last else None}


def resolve(appointments, account, now, selected=None):
    now = now.astimezone(SYDNEY)
    candidates = [a for a in appointments if a.get('status') in ('Attended','Completed')
                  and a.get('kind')=='appointment' and day(a.get('date'))
                  and day(a['date']) <= now.date()
                  and (not appointment_time(a) or appointment_time(a)<=now)
                  and care_team.key(a.get('professional','').split(', ')[0]) == care_team.key(account['name'])]
    last = max(candidates, key=lambda a:appointment_time(a) or datetime.combine(day(a['date']),time.min,tzinfo=SYDNEY), default=None)
    if selected is not None and selected != '':
        parsed = day(selected) if isinstance(selected,str) and 'T' not in selected else None
        if parsed is None or parsed > now.date():
            raise HTTPException(400,'Choose a valid comparison date on or before today in Australia/Sydney.')
        return Period(now,last,parsed,datetime.combine(parsed,time.min,tzinfo=SYDNEY),'custom')
    completed_at=appointment_time(last) if last else None
    return Period(now,last,completed_at.date() if completed_at else day(last['date']) if last else None,completed_at,'last_consultation' if last else 'first_review')
