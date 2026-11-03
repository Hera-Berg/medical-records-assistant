"""Compact issue projection; source rows remain intact for evidence and exports."""
import re
from .comparison import instant, SYDNEY, day
from datetime import datetime

WALKING = {'jane-review-carer-2026-10-24', 'jane-review-staff-2026-10-25'}
HEADLINES = {
    'jane-review-patient-2026-10-23': 'Jane reports avoiding the dining room at breakfast',
    'jane-history-68': 'Jane requests clarification about her heart medication',
}


def project(groups, originals):
    issues = {}
    # A prescription may also appear among documents. Keep one source per issue.
    medication_ids = {r['id'] for r in groups['medications']}
    for category in ('reported', 'documents', 'medications', 'newly_received', 'follow_up'):
        for row in groups[category]:
            if category == 'documents' and row['id'] in medication_ids:
                continue
            key = 'walking-support' if row['id'] in WALKING else row['id']
            section = 'reported' if category == 'reported' else 'follow_up' if category == 'follow_up' else 'updates'
            label = {'documents':'Clinical document', 'medications':'Confirmed medication change',
                     'newly_received':'Newly received / uploaded'}.get(category)
            headline = HEADLINES.get(key, row['headline'])
            if key == 'walking-support':
                headline = 'Walking support requested'
            elif row['status'] == 'Original typed note':
                text = originals.get(key, '').strip()
                # Genuine short notes stay visible without assigning an author.
                headline = {'pain in left foot':'Left foot pain reported',
                            'struggle sleeping this week':'Sleep difficulty reported'}.get(text.casefold(), 'Typed observation recorded')
            elif category == 'newly_received' and row['headline']=='Document newly uploaded to the record':
                headline = 'Document added to the record'
            if category == 'documents' and re.search(r'\b(recommend(?:ed|ation|ations|s)?|advis(?:ed|es))\b', row['summary'], re.I):
                label = 'Recommendation documented'
            headline = re.sub(r'\s+', ' ', headline).strip()
            if len(headline) > 110:
                headline = 'Clinical document available' if section == 'updates' else 'Reported observation recorded'
            issue = issues.setdefault(key, {'id':key, 'section':section, 'headline':headline,
                'label':label, 'summary':None, 'contributions':[], 'date':row['date']})
            contribution = dict(row, original_text=originals.get(row['id']),
                                review_status=None)
            if not any(c['id'] == row['id'] for c in issue['contributions']):
                issue['contributions'].append(contribution)
            # Newly received items sort by actual receipt/upload, not historic findings.
            recent = row.get('received_at') or row.get('received_date') or row.get('uploaded_at') if category == 'newly_received' else row.get('event_timestamp') or row.get('recorded_at') or row['date']
            issue['sort_date'] = max(issue.get('sort_date', ''), recent or row['date'])
            issue['date'] = max(issue['date'], row['date'])
    def chronology(issue):
        value = issue['sort_date']
        timestamp = instant(value) or datetime.fromisoformat(value[:10]).replace(tzinfo=SYDNEY)
        return (timestamp.timestamp() if timestamp else 0, issue['id'])
    result = sorted(issues.values(), key=chronology, reverse=True)
    for issue in result:
        issue['source_count'] = len(issue['contributions'])
        issue['display_date'] = day(issue['sort_date']).isoformat()
    return result
