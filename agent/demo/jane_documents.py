"""Native editions of fictional sources; stored in the event log by the seeder.

Original PDFs and exact note wording remain unchanged. Release metadata here is
simulated workflow information, not an identity or permission mechanism.
"""
from __future__ import annotations
import json
import re
from pathlib import Path


def prescriptions(day):
    """Explicit fictional prescribing data, not inferred from dosing or elapsed time."""
    specs = {
        '2025-05-06': [('Apixaban','5 mg','twice daily','2026-05-06',60,5), ('Metoprolol','25 mg','twice daily','2026-05-06',60,5)],
        '2025-07-24': [('Rosuvastatin','5 mg','nightly','2026-07-24',30,5)],
        '2026-08-20': [('Amlodipine','5 mg','once daily','2026-10-12',30,0)],
        '2026-08-28': [('Amlodipine','2.5 mg','once daily','2026-10-31',30,2),
                       ('Apixaban','5 mg','twice daily','2027-02-28',60,5),
                       ('Metoprolol','25 mg','twice daily','2027-08-28',60,5),
                       ('Rosuvastatin','5 mg','nightly','2027-07-24',30,5)],
    }
    rows=[]
    for name,strength,frequency,expiry,quantity,repeats in specs.get(day,[]):
        rows.append({'id':day+':'+name.lower(), 'medicine_id':'med:'+name.lower(),
            'medicine':name,'strength':strength,'formulation':'tablet',
            'dose_per_administration':'1 tablet ('+strength+')', 'route':'oral',
            'frequency':frequency, 'timing':'night' if frequency=='nightly' else None,
            'prn':False, 'indication':{'Amlodipine':'Blood pressure management',
                'Apixaban':'Anticoagulation for atrial fibrillation','Metoprolol':'Rate control for atrial fibrillation',
                'Rosuvastatin':'Cholesterol management'}[name],
            'maximum_daily_dose':None,'minimum_interval':None,
            'quantity_per_dispensing':str(quantity)+' tablets', 'prescribed_date':day,
            'expiry_date':expiry,'prescriber':'Dr James Smith', 'prescriber_organisation':'Sydney Aged Care',
            'original_repeats':repeats, 'repeats_remaining':0 if repeats==0 else None,
            'supply_remaining':None, 'dispensing_date':None, 'estimated_supply_until':None,
            'original_directions':strength+' '+frequency,
            'plain_directions':'Take one tablet each night.' if frequency=='nightly' else
                               'Take one tablet twice daily.' if frequency=='twice daily' else 'Take one tablet once daily.',
            'treatment_status':'current' if day=='2026-08-28' else 'superseded',
            'medication_start_date':{'Amlodipine':None,'Apixaban':'2025-05-06',
                'Metoprolol':'2025-05-06','Rosuvastatin':'2025-07-24'}[name]})
    return rows


def clean(text):
    # Remove only known generation commentary, never clinical results or original
    # personal wording. The full former edition remains in the immutable source.
    sentences = re.split(r'(?<=[.!?])\s+(?=[A-Z])',text)
    internal = ('This demo copy','The pre-existing example library','Separate scenario-specific review:',
                'The original generic example','Release wording is demonstration workflow metadata',
                'This copy reproduces the matching fictional')
    return ' '.join(s for s in sentences if not s.startswith(internal))


def native_document(item):
    from .jane import CHARTS, STATIN, BASE
    day=item['date']; author=item['author']; personal=author in ('Jane Doe','Emily Doe, daughter')
    organisation = ('Harbourside Cardiology' if 'Thompson' in author or author=='Harbourside Cardiology' else
        'Harbour Clinical Pharmacy' if 'Patel' in author else 'Sydney Labs' if 'Labs' in author or 'Rao' in author else
        'Example Gardens Residential Aged Care' if personal or any(x in author for x in ('nurse','physiotherapist','therapist')) else
        'Sydney Aged Care' if 'Smith' in author else 'Example Community Health')
    specialty=('Cardiology' if 'Thompson' in author else 'Pharmacy' if 'Patel' in author else
               'Pathology' if 'Labs' in author else 'Radiology' if 'Rao' in author else
               'General practice' if 'Smith' in author else author.split(', ',1)[1] if ', ' in author else None)
    doc={'title':item['title'],'type':item['source_type'], 'date':day, 'consultation_date':day,
         'patient':{'name':'Jane Doe','dob':'1935-01-01','address':'Example Gardens Residential Aged Care, 1 Demonstration Lane, Sydney NSW 2000'},
         'author':author,'specialty':specialty,'organisation':None if personal else organisation,
         'recipient':None if personal else 'Dr James Smith, Sydney Aged Care',
         'fictional':True,'edition':'Native demo edition; original files retained',
         'release':None if personal else {'released_by':'Dr James Smith','organisation':'Sydney Aged Care','date':day,'simulated':True},
         'contribution':{'name':'Emily Doe' if 'Emily' in author else 'Jane Doe','role':'Daughter and involved family carer' if 'Emily' in author else 'Patient','date':day} if personal else None,
         'sections':[{'heading':'Original wording' if personal else 'Shared record','text':item['text'] if personal else clean(item['text'])}],
         'results':[], 'medications':[], 'prescriptions':prescriptions(day)}
    if day=='2024-10-24':
        from .jane import PROFILE
        from .. import profile
        doc['sections'].append({'heading':'Identity, family and practical care information',
            'text':'\n'.join(f'{profile.FIELDS[k]["label"]}: {v}' for k,v in PROFILE.items()
                    if k not in ('important_notes','accessibility','allergy_notes'))})
    if 'Smith' in author:
        doc['recipient']='Jane Doe (shared copy)'
    if day in CHARTS:
        doc['medications']=[{'medicine':n,'directions':v} for n,v in CHARTS[day].items()]
    if day in ('2025-05-02','2026-07-13','2026-09-24'):
        doc['title']='Cardiology consultation letter' if day=='2025-05-02' else 'Cardiology follow-up letter'
        meds=BASE if day=='2025-05-02' else STATIN
        context=('Thank you for referring Mrs Jane Doe for assessment of intermittent palpitations. '
                 'She described a fluttering sensation while sitting, coming and going. Your GP review on 11 April led to a resting ECG on 14 April and ambulatory monitoring completed on 24 April, with her symptom diary submitted.' if day=='2025-05-02' else
                 'Thank you for involving me in Mrs Jane Doe’s ongoing care for atrial fibrillation. This letter records the patient-visible follow-up plan and should be read with her current GP medication chart.')
        history=('Jane lives in residential aged care. Her recorded history includes hypertension and knee osteoarthritis. She usually walks to meals with a frame and values communal meals, gardening and family visits. Knee discomfort can limit longer walks. She prefers plain-language explanations, with written information whenever a medicine changes, and wishes her daughter Emily to be included with her permission.')
        assessment=('Atrial fibrillation is documented following the palpitations assessment. Rate control and anticoagulation were discussed as the shared management approach. The full monitoring traces, examination measurements and numerical investigation findings are not reproduced in the released material; they should not be inferred from this letter.' if day=='2025-05-02' else
                    'The established diagnosis is atrial fibrillation. Ongoing management is coordinated with Dr Smith. No medication change is recorded at this consultation. The released source does not supply numerical examination findings or a detailed ECG interpretation, and none is added here.')
        plan=('Recommendations for metoprolol 25 mg twice daily and apixaban 5 mg twice daily are to be reviewed with Jane by Dr Smith before issuing the updated prescriptions and chart. Provide the new written list, explain the purpose and directions of each medicine and reconcile it with the medicines supplied in residential care. The GP review and chart issue are recorded separately on 6 May.' if day=='2025-05-02' else
              'Continue the directions in the released GP chart; this visit does not replace or alter that chart. Bring the current list to subsequent reviews and discuss any remaining questions about medication changes. Provide written information and allow time for Jane to ask questions. Follow-up remains coordinated between Harbourside Cardiology and Dr Smith.')
        if day=='2026-07-13':
            plan += ' An echocardiogram is arranged as part of follow-up; completion is recorded on 20 July and the shared results discussion on 30 July.'
        if day=='2026-09-24':
            history += ' Jane’s 2 September note reports less frequent dizziness and taking time before walking. This is her attributed symptom update, not proof of a medication cause.'
            plan += ' No additional medication change is documented in this released letter.'
        doc['sections']=[{'heading':'Dear Dr Smith','text':context}, {'heading':'Relevant history and daily function','text':history},
             {'heading':'Allergies and reactions','text':'Jane reports an itchy rash after penicillin many years ago. Timing and severity are uncertain; this history is patient-reported and not clinician-verified.'},
             {'heading':'Assessment and available investigations','text':assessment}, {'heading':'Shared management and follow-up','text':plan},
             {'heading':'Yours sincerely','text':'Dr Matthew Thompson\nCardiologist\nHarbourside Cardiology'}]
        doc['medications']=[{'medicine':n,'directions':v} for n,v in meds.items()]
        if day=='2026-09-24':
            # Preserve the entire earlier released narrative, including the recorded
            # examination/ECG mention, rather than replace it with a shorter summary.
            library=json.loads((Path(__file__).parents[1]/'server/static_files/document-examples.json').read_text())
            old=next(d for d in library['documents'] if d['id']=='cardiology-2026-09-24')
            doc['sections'][3:3]=old['sections']
    if day=='2026-10-08':
        library=json.loads((Path(__file__).parents[1]/'server/static_files/document-examples.json').read_text())
        old=next(d for d in library['documents'] if d['id']=='blood-panel-2026-10-08')
        doc.update(title=old['title'], sections=old['sections'], results=old['results'], type=old['category'])
        # Preserve the recorded release identity independently of author/recipient.
        match=re.fullmatch(r'Released for viewing by (.+)',old.get('release_note',''))
        if match: doc['release']['released_by']=match.group(1)
    if day in ('2025-11-05','2026-03-04','2026-10-12'):
        doc['medications']=[{'medicine':n,'directions':v} for n,v in STATIN.items()]
    if day=='2026-10-12':
        doc['sections'] += [
            {'heading':'Chart comparison', 'text':'The 20 August prescription changed amlodipine to 5 mg once daily. The 28 August chart explicitly supersedes that direction with 2.5 mg once daily. Apixaban 5 mg twice daily, metoprolol 25 mg twice daily and rosuvastatin 5 mg nightly remain as listed in the current chart. The September cardiology letter records no further medication change.'},
            {'heading':'Questions for the GP review', 'text':'Jane wants to understand which heart tablet changed in August and what each medicine is for. Provide a written explanation using the current chart and discuss her question at the next review. Her report of dizziness after a tablet change is a temporal association, not an established cause. No additional prescribing change is made by this pharmacist review.'}]
    if day=='2026-10-15':
        doc['sections'].append({'heading':'Recorded prescription update',
            'text':'The remaining repeat balance for the 28 August amlodipine prescription is recorded as zero on 15 October 2026. Current medicine supply and dispensing dates are not recorded. This does not stop the medicine or change the prescription expiry date.'})
        doc['repeat_updates']=[{'prescription_id':'2026-08-28:amlodipine','remaining':0,'date':'2026-10-15','contributor':'Dr James Smith'}]
    return doc
