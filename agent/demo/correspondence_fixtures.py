"""New, dated fictional encounters and editorial source editions, not retrospective findings."""
from ..document_titles import title
from .document_templates import FACILITIES

def para(text):return {'type':'paragraph','text':text}
def plan(*items):return {'type':'plan','items':list(items)}
def table(headers,rows,widths):return {'type':'table','headers':headers,'rows':rows,'widths':widths}

def encounters(day):
    return {
    'cardio-recommendation':dict(author='Dr Matthew Thompson',role='Cardiologist',organisation='Harbourside Cardiology',subject='Cardiovascular case consultation — persistent dizziness',
      encounter='Telephone case consultation with facility registered nurse; no patient examination',
      additions=['New telephone case consultation with Morgan Bell on '+day,'Specific recommendations for same-day medical assessment, repeat blood tests and medication reconciliation; recommendations are not recorded as completed.'],
      blocks=[
       para('Thank you for asking me to review Jane\'s ongoing dizziness in the context of atrial fibrillation and her recent blood results. I undertook a telephone case consultation with Morgan Bell, the facility registered nurse, today. This letter follows my September advice and the completed GP consultation of 21 October. Jane was not examined during this case consultation.'),
       para('The supplied history describes intermittent dizziness on getting up, continuing in Jane\'s own update of 23 October. Emily observed Jane asking for support and pausing on their walk to the dining room on 24 October. Staff have subsequently recorded similar requests. Jane has also described remaining in her room for breakfast because she worries about becoming dizzy. These reports indicate a practical effect on participation and confidence; they do not establish a cardiovascular cause.'),
       para('Her usual mobility is with a frame, and she normally eats independently. Atrial fibrillation was documented in May 2025, with hypertension and knee osteoarthritis also recorded. No current examination, standing blood-pressure series, pulse record or ECG accompanies this referral. I have not inferred these findings from her medication chart.'),
       {'type':'heading','text':'Current recorded medicines'},
       table(['Medicine','Directions in the 28 August 2026 chart'],[['Apixaban','5 mg twice daily'],['Metoprolol','25 mg twice daily'],['Amlodipine','2.5 mg once daily'],['Rosuvastatin','5 mg at night']],[155,354]),
       para('The chart records a reduction of amlodipine from 5 mg to 2.5 mg on 28 August. No subsequent issued dose change is documented. Jane\'s question about which heart tablet changed remains relevant to reconciliation and to a clear explanation of her current regimen. This correspondence does not issue or renew a prescription.'),
       para('The full blood examination collected on 27 October reports haemoglobin 101 g/L (laboratory interval 115–165), compared with 128 g/L on 14 April. The 29 October electrolyte panel reports sodium 122 mmol/L (135–145), previously 138 mmol/L, with an explicit laboratory critical flag. Potassium is 4.3 mmol/L (3.5–5.2) and creatinine 74 µmol/L (45–90). The reports were issued after the earlier September correspondence. There is no documented clinical assessment of these new abnormalities in the material provided.'),
       para('My impression is persistent positional dizziness with reduced confidence in walking and communal meal attendance, of undetermined cause. The low sodium and lower haemoglobin warrant prompt medical assessment in their own right. It would be premature to attribute Jane\'s symptoms to either result, to atrial fibrillation or to a particular medicine without examination and reconciliation.'),
       plan('Please arrange same-day in-person medical assessment of Jane and review the laboratory critical alert. The assessing team should determine the appropriate setting and repeat blood tests; a follow-up appointment alone should not substitute for this assessment.',
            'Reconcile the medication administration record with the current chart, including the amlodipine reduction. Review pulse, lying/standing blood pressure where safe, current weight and renal function when assessing treatment. No empiric dose change is issued in this letter.',
            'Assess the fall in haemoglobin and ask about possible bleeding, particularly while Jane is prescribed apixaban. Decisions about anticoagulation should follow the treating team\'s assessment; INR is not being requested as routine monitoring of apixaban.',
            'Please explain the recorded medication change to Jane in plain language and provide a written list, involving Emily with Jane\'s permission. Arrange cardiology follow-up after the immediate assessment, with earlier review if symptoms persist or the assessment identifies a cardiovascular concern.'),
       para('I would appreciate the assessment findings and updated medication list so that the next cardiology review can address the outstanding symptoms with a reconciled record.')]),
    'audiology-correspondence':dict(author='Olivia Reed',role='Audiologist',organisation='Example Community Audiology',subject='Brief hearing-aid service review',encounter='New in-person bedside hearing-aid service check',
      additions=['New bedside service check on '+day,'Left wax guard visibly obstructed; replaced. Right wax guard clear.','Listening check satisfactory after servicing; Jane reported clearer speech.','No audiogram, vestibular assessment or programming change performed. Existing 18 November review remains booked.'],
      blocks=[
       para('I saw Jane at Sydney Aged Care today for a brief hearing-aid service check after she reported that speech sounded muffled through the left aid. Her daughter Emily attended with Jane\'s agreement. This was a troubleshooting visit.'),
       para('Jane has used hearing aids since September 2025 and would like to follow conversation more easily at meals. No new audiogram was obtained today.'),
       table(['Check','Finding / action'],[['External device inspection','Left wax guard obstructed; replaced. Right wax guard clear.'],['Listening check','Both aids sounded clear after servicing.'],['Patient response','Jane reported clearer speech with the left aid.'],['Programming / audiometry','No programming change or threshold testing undertaken.']],[155,354]),
       para('The immediate concern was consistent with a device maintenance problem. A satisfactory listening check is not a measurement of hearing thresholds and does not exclude a change in hearing. We did not undertake vestibular testing, and the reported dizziness should continue to be assessed by the medical team.'),
       plan('Facility staff can help Jane check that the aids are seated and switched on before meals. Please report any return of muffled sound or discomfort.',
            'Continue the booked hearing-aid review on 18 November, bringing both aids and any observations about communication in daily activities.',
            'Use a quiet setting and written medication information for important discussions, allowing Jane time to respond.')],closing='Kind regards,'),
    'physio':dict(author='Sarah Green',role='Physiotherapist',organisation='Sydney Aged Care',subject='Brief functional mobility and safety review',encounter='New limited in-person bedside functional review',
      additions=['New limited bedside assessment on '+day,'One sit-to-stand completed using armrests with close guarding; no loss of balance observed during that transfer.','Jane reported light-headedness on standing; walking-distance and timed balance tests deferred.','No exercise progression; medical assessment recommended before progression. Existing 12 November appointment remains planned.'],
      blocks=[
       para('I reviewed Jane at the facility today because she has been asking for someone to remain beside her when walking to meals. This was a brief bedside safety review rather than the full therapy session planned for 12 November. Her usual function is walking with her frame to communal meals and eating independently.'),
       para('Jane described feeling light-headed after standing and said she was less confident about walking to breakfast. I observed one sit-to-stand from her usual chair: she used the armrests and completed the transfer with close guarding, without an observed loss of balance during that transfer. When she reported light-headedness in standing, I returned her to sitting and did not proceed with walking-distance or timed balance testing.'),
       table(['Functional task','Observation today'],[['Sit-to-stand','Armrests used; close guarding for one observed transfer.'],['Walking / balance tests','Deferred after reported light-headedness.'],['Usual aid','Frame, as recorded in the established care plan.']],[165,344]),
       para('The assessment is currently limited by light-headedness. No exercise progression was attempted; the immediate focus is safe participation while the medical team assesses the symptoms and recent blood results.'),
       plan('Continue the recorded frame and provide close staff support for transfers and short essential walks according to Jane\'s presentation. Stop the activity and seek nursing assistance if she becomes light-headed.',
            'Defer progression of balance work or walking endurance until the medical team has assessed the dizziness and new results.',
            'Review the assistance plan with nursing staff following medical assessment. Retain the 12 November physiotherapy appointment for reassessment if Jane is able to participate safely.'),
       para('I will reassess Jane\'s mobility and goals after medical review.')],closing='With thanks,'),
    'podiatry-correspondence':dict(author='Daniel Brooks',role='Podiatrist',organisation='Example Community Health',subject='Foot-care and footwear review',encounter='New in-person outreach podiatry review',
      additions=['New outreach review on '+day+'; does not establish attendance at the separate 28 October booking.','Dry heels and small superficial callus beneath the first metatarsal heads; no open lesion or discharge seen.','Gentle nail filing and moisturiser applied to intact heel skin; no invasive debridement and no bleeding observed.','Loose-fitting heel of current slippers noted; supportive footwear recommended.'],
      blocks=[
       para('I reviewed Jane at the Sydney Aged Care outreach clinic today for routine foot care and footwear advice. Jane uses a frame and has recently asked for support when walking.'),
       para('On inspection there was dry skin at both heels and a small amount of superficial callus beneath the first metatarsal heads. No open lesion, discharge or focal redness was seen. The nails required minor smoothing. The heel of Jane\'s current slippers was loose and did not hold the foot securely. No vascular pressure measurements or formal sensory testing were undertaken during this limited visit.'),
       para('I gently filed the nail edges and applied moisturiser to intact heel skin. I did not perform invasive callus debridement, and no bleeding was observed. Her charted apixaban treatment was noted; I have not advised interruption or alteration of any medicine.'),
       para('The impression is routine skin and nail-care needs with footwear that could provide a more secure fit. These findings do not explain Jane\'s dizziness, and I have not diagnosed a vascular, neuropathic or infective foot condition from this review.'),
       plan('Use footwear with a secure heel and fastening, checking comfort and skin after wear. Seek staff help with footwear and essential walking while Jane is unsteady.',
            'Continue regular inspection of the feet and moisturise intact dry heel skin, avoiding application between the toes. Report a new skin break, redness, discharge or pain to the nursing team.',
            'Arrange a routine podiatry review in approximately six weeks, or earlier for a new foot concern. This recommendation is not a confirmed booking.'),
       para('Please let our outreach service know if footwear problems or foot discomfort are limiting Jane\'s participation despite these measures.')],closing='Yours sincerely,')}


def text_of(document):
    pieces=[]
    for b in document['blocks']:
        if b['type'] in ('paragraph','heading'):pieces.append(b['text'])
        elif b['type']=='plan':pieces.append('\n'.join(f'{n}. {text}' for n,text in enumerate(b['items'],1)))
        elif b['type']=='table':pieces.append(' | '.join(b['headers'])+'\n'+'\n'.join(' | '.join(row) for row in b['rows']))
    return '\n\n'.join(pieces)


def document(payload,day):
    """Return clinical text/layout plus a machine-readable account of new fiction."""
    key=payload['demo_item_id'];native=payload.get('native_document') or {}
    if key in encounters(day):
        d=encounters(day)[key].copy();d.update(event_date=day,document_date=day,received_date=day,new_encounter=True)
        d['title']=d['author']+' - '+d['role']+' letter';d['text']=text_of(d);return d
    d=dict(author=payload['author'],role=payload['role'],organisation=native.get('organisation') or payload.get('organisation'),title=title(payload),
           event_date=payload['event_date'],document_date=native.get('date') or payload['event_date'],received_date=native.get('received_date'),text=payload['text'],new_encounter=False,additions=[])
    d['organisation']=(d['organisation'] or '').replace(' (fictional)','')
    if d['organisation'] not in FACILITIES:raise ValueError('No established facility template: '+str(d['organisation']))
    if key=='cardio-diagnosis':
        d['subject']='Atrial fibrillation — assessment and treatment recommendation'
        d['blocks']=[para('Thank you for referring Jane for assessment. Atrial fibrillation is documented in the assessment record. Hypertension and knee osteoarthritis are also recorded. The original dates of onset of those conditions are not established.'),
          para('The recommendation is for apixaban 5 mg twice daily and metoprolol 25 mg twice daily, with prescribing and reconciliation by the usual GP. This is the recommendation at the time of this assessment; a later issued prescription is a separate record.'),
          para('No additional examination measurements, ECG trace or echocardiogram findings accompany this source. They have not been added to this letter.'),
          plan('Please review the medication record and document the issued directions in the GP chart.','Reassess symptoms and treatment tolerance at subsequent medical review.')]
        # Clarify the historical recommendation without copying its retrospective
        # reference to a prescription that was issued four days later.
        d['text']=text_of(d);d['editorial_note']='Historical recommendation restated; retrospective 6 May prescribing statement removed from the 2 May source edition. No new historical examination findings added.'
    if key=='med-chart':
        d['title']='Medication chart';d['medications']=[['Apixaban','5 mg twice daily'],['Metoprolol','25 mg twice daily'],['Amlodipine','2.5 mg once daily'],['Rosuvastatin','5 mg at night']]
    if key=='med-chart':d['display_text']=d['text'][d['text'].index('Amlodipine was reduced'):]
    if key in ('smith-consult','bennett-consult'):d['title']=payload['author']+' - GP note'
    if key in ('nurse-history','nurse-clinical'):d['title']=payload['author']+' - Nursing note'
    return d
