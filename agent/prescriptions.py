"""Read explicit source-backed prescription fields; expiry is not treatment status.

No calendar arithmetic infers repeat balances, dispensing or remaining supply.
"""
from datetime import datetime
from zoneinfo import ZoneInfo
from .projection.dates import parse_iso_date
from .demo_metadata import entries

SYDNEY = ZoneInfo('Australia/Sydney')


def expiry_status(expiry, now):
    day = now.astimezone(SYDNEY).date() if isinstance(now, datetime) else now
    expires = parse_iso_date(expiry)
    if expires is None:
        return {'state':'unknown','label':'Expiry not recorded','days':None}
    delta=(expires-day).days
    formatted=expires.strftime('%d %B %Y').lstrip('0')
    label=(f'Expired — {formatted}' if delta<0 else 'Expires today' if delta==0 else
           f'Expires in {delta} days — {formatted}' if delta<=30 else f'Valid until {formatted}')
    return {'state':'expired' if delta<0 else 'soon' if delta<=30 else 'valid','label':label,'days':delta}


def listing(events, artifacts, now, projection=None):
    demo=entries(events,artifacts)
    updates={}
    for event in demo.values():
        for update in event.payload.get('native_document',{}).get('repeat_updates',[]):
            key=update.get('prescription_id')
            if key and (key not in updates or update.get('date','')>updates[key].get('date','')):
                updates[key]={**update,'source':event.payload['artifact']}
    rows={}
    for event in demo.values():
        p=event.payload
        for value in p.get('native_document',{}).get('prescriptions',[]):
            row={**value,'source':p['artifact'],'source_url':'/artifact/'+p['artifact'],
                 'document_date':p['event_date'],'added_at':event.ts,'fictional':True}
            update=updates.get(row['id'])
            if update:
                row.update(repeats_remaining=update['remaining'],repeat_update=update)
            row['expiry']=expiry_status(row.get('expiry_date'),now)
            remaining=row.get('repeats_remaining')
            row['repeat_label']=('No repeats remaining' if remaining==0 else f'{remaining} repeats remaining'
                                 if isinstance(remaining,int) else 'Remaining repeats not recorded')
            row['supply_label']='Supply remaining not recorded'
            # Never derive a PRN run-out date from quantity or a maximum dose.
            row['estimated_supply_until']=None
            if projection is not None:
                entity=projection.entities.get(row['medicine_id'])
                if entity:
                    dose=entity.slots.get('dose')
                    if entity.status=='stopped': row['treatment_status']='stopped'
                    elif row['treatment_status']=='current' and (dose is None or dose.winner is None or dose.winner.value.literal!=row['original_directions']):
                        row['treatment_status']='historical'
            rows[row['id']]=row
    return sorted(rows.values(),key=lambda r:(r['prescribed_date'],r['medicine']),reverse=True)


def release_label(document):
    contributor=document.get('contribution')
    if contributor:
        return 'Added by '+contributor['name']
    release=document.get('release') or {}
    name=release.get('released_by')
    return 'Released for viewing by '+name if name else 'Release information not recorded'
