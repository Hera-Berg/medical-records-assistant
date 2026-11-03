"""Issue grouping is a lossless display projection, not clinical inference."""
from agent.server.review_issues import project


def row(id, headline='Observation', date='2026-10-24'):
    return dict(id=id, headline=headline, summary='Original summary',date=date,
                source_url='/clinician/artifact/'+id, status='Prepared demo summary',author='Reporter',role='Carer')


def test_walking_group_keeps_both_perspectives_and_sources():
    carer=row('jane-review-carer-2026-10-24')
    staff=row('jane-review-staff-2026-10-25',date='2026-10-25')
    sleep=row('sleep','Difficulty sleeping')
    groups=dict(reported=[carer,staff,sleep],documents=[],medications=[],newly_received=[],follow_up=[])
    issues=project(groups,{carer['id']:'Emily reports...',staff['id']:'Morgan records...'})
    walking=next(i for i in issues if i['id']=='walking-support')
    assert walking['headline']=='Walking support requested'
    assert walking['source_count']==2
    assert [c['original_text'] for c in walking['contributions']]==['Emily reports...','Morgan records...']
    assert len(issues)==2
    assert len(groups['reported'])==3
    assert {c['source_url'] for i in issues for c in i['contributions']}=={r['source_url'] for r in groups['reported']}


def test_prescription_duplicate_is_one_issue_and_receipts_use_receipt_order():
    prescription=row('rx')
    old=row('old',date='2020-01-01');old['uploaded_at']='2026-11-01T01:00:00Z'
    issues=project(dict(reported=[],documents=[prescription],medications=[prescription],newly_received=[old],follow_up=[]),{})
    assert len(issues)==2
    assert issues[0]['id']=='old'
    assert next(i for i in issues if i['id']=='rx')['label']=='Confirmed medication change'
    assert sum(i['source_count'] for i in issues)==2


def test_short_symptoms_keep_unknown_author_without_showing_note_text():
    note=row('foot');note['status']='Original typed note';note['author']='Author not recorded'
    issue=project(dict(reported=[note],documents=[],medications=[],newly_received=[],follow_up=[]),{'foot':'pain in left foot'})[0]
    assert issue['headline']=='Left foot pain reported'
    assert issue['contributions'][0]['author']=='Author not recorded'
    assert issue['contributions'][0]['original_text']=='pain in left foot'
