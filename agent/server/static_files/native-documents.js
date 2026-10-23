// Native text editions share the existing document typography. All stored text is escaped.
(() => {
  const esc = value => String(value ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const date = value => /^\d{4}-\d{2}-\d{2}$/.test(value||'') ? new Intl.DateTimeFormat('en-AU',{day:'numeric',month:'long',year:'numeric',timeZone:'UTC'}).format(new Date(value+'T12:00:00Z')) : 'Not recorded';
  const known = value => value === null || value === undefined || value === '' ? 'Not recorded' : esc(value);
  async function read(url, signal) {const r=await fetch(url,{signal,cache:'no-store'});if(!r.ok)throw Error('This record could not be opened. Please try again.');return r.json();}
  function release(doc) {
    if(doc.contribution) return `Added by ${esc(doc.contribution.name)} · ${esc(doc.contribution.role)} · ${date(doc.contribution.date)}`;
    const r=doc.release;
    return r?.released_by ? `Released for viewing by ${esc(r.released_by)}${r.date?' · '+date(r.date):''}` : 'Release information not recorded';
  }
  function prescription(row) {
    const fields=[['Strength',row.strength],['Formulation',row.formulation],['Dose per administration',row.dose_per_administration],
      ['Route',row.route],['Frequency',row.frequency],['Timing',row.timing],['As needed (PRN)',row.prn===true?'Yes':row.prn===false?'No — regular medicine':null],
      ['Indication',row.indication],['Maximum daily dose',row.maximum_daily_dose],['Minimum interval',row.minimum_interval],
      ['Quantity per dispensing',row.quantity_per_dispensing],['Date prescribed',date(row.prescribed_date)],
      ['Medication start date',date(row.medication_start_date)],['Prescription expiry',date(row.expiry_date)],
      ['Prescriber',row.prescriber],['Prescriber organisation',row.prescriber_organisation],['Repeats originally authorised',row.original_repeats],
      ['Remaining repeats',row.repeat_label||'Remaining repeats not recorded'],['Medication supply',row.supply_label||'Supply remaining not recorded'],
      ['Dispensing date',date(row.dispensing_date)]];
    return `<section class="native-prescription rx-${esc(row.expiry?.state||'unknown')}" id="rx-${esc(row.id)}"><header><h3>${esc(row.medicine)} ${esc(row.strength||'')}</h3><p><span class="rx-badge rx-${esc(row.expiry?.state||'unknown')}">${esc(row.expiry?.label||'Expiry not recorded')}</span> <span class="docs-category">${esc(row.treatment_status||'Treatment status not recorded')}</span></p></header><p><strong>Original directions:</strong> ${known(row.original_directions)}</p>${row.plain_directions?`<p>${esc(row.plain_directions)}</p>`:''}<dl class="native-facts">${fields.map(([label,value])=>`<div><dt>${label}</dt><dd>${known(value)}</dd></div>`).join('')}</dl>${row.repeat_update?`<p class="docs-release">Repeat balance recorded ${date(row.repeat_update.date)} by ${esc(row.repeat_update.contributor)}. <a href="/artifact/${encodeURIComponent(row.repeat_update.source)}">View recorded update</a></p>`:''}<a href="${esc(row.source_url||'#')}">View supporting prescription</a></section>`;
  }
  function documentPage(meta) {
    const doc=meta.native;
    const patient=doc.patient;
    return `<div class="native-toolbar"><a href="/documents">‹ Back to Documents</a><a href="/api/artifact/${encodeURIComponent(meta.short)}" download>Download original</a></div><article class="docs-letter native-letter"><header class="docs-letter-heading">${doc.fictional?'<span class="docs-sample-label">Fictional demo</span>':''}<p class="docs-organisation">${esc(doc.organisation||'')}</p><h2>${esc(doc.title)}</h2>${doc.extracted?'<p class="docs-release">Text extracted from the PDF. Layout may differ; the original remains available above.</p>':`<p>${esc(doc.author||'')}${doc.specialty?' · '+esc(doc.specialty):''}</p><p>Document date: ${date(doc.date)}</p>${doc.consultation_date?`<p>Consultation / event date: ${date(doc.consultation_date)}</p>`:''}`}</header>${patient?`<dl class="docs-letter-address"><div><dt>Patient</dt><dd>${esc(patient.name)} · DOB ${date(patient.dob)}<br>${esc(patient.address||'')}</dd></div>${doc.recipient?`<div><dt>To</dt><dd>${esc(doc.recipient)}</dd></div>`:''}</dl>`:''}<p class="docs-release">${release(doc)}</p>${doc.sections.map(section=>`<section class="native-section"><h3>${esc(section.heading)}</h3><p>${esc(section.text)}</p></section>`).join('')}${doc.medications?.length?`<section><h3>Recorded medication directions</h3><div class="docs-table-wrap"><table class="docs-lab-results"><thead><tr><th>Medicine</th><th>Original directions</th></tr></thead><tbody>${doc.medications.map(m=>`<tr><th>${esc(m.medicine)}</th><td>${esc(m.directions)}</td></tr>`).join('')}</tbody></table></div></section>`:''}${doc.results?.length?`<section><h3>Results</h3><div class="docs-table-wrap"><table class="docs-lab-results"><thead><tr><th>Test</th><th>Result</th><th>Units</th><th>Reference interval</th><th>Flag</th></tr></thead><tbody>${doc.results.map(r=>`<tr><th>${esc(r.test)}</th><td>${known(r.result)}</td><td>${known(r.unit)}</td><td>${known(r.reference_interval)}</td><td>${known(r.flag)}</td></tr>`).join('')}</tbody></table></div></section>`:''}${doc.prescriptions?.length?`<section><h3>Prescriptions</h3>${doc.prescriptions.map(prescription).join('')}</section>`:''}${meta.demo?.related?.length?`<section><h3>Related records</h3>${meta.demo.related.map(r=>`<p><a href="/artifact/${encodeURIComponent(r.artifact)}">${esc(r.title)}</a></p>`).join('')}</section>`:''}<footer><details><summary>Source and provenance details</summary><p>${doc.fictional?'Simulated demonstration release information; names do not authenticate clinical identity. ':''}${esc(doc.edition||'')}.</p><p>Added to the record: ${esc(meta.ingested_ts||'Not recorded')}</p>${meta.demo?.original_wording_artifact?`<p><a href="/api/artifact/${encodeURIComponent(meta.demo.original_wording_artifact)}">Original stored text</a></p>`:''}</details></footer></article>`;
  }
  class NativeDocument extends HTMLElement {
    connectedCallback(){this.load();}
    disconnectedCallback(){this.controller?.abort();}
    async load(){this.controller?.abort();const controller=this.controller=new AbortController();this.innerHTML='<p role="status">Opening document…</p>';try{const meta=await read('/api/artifact/'+encodeURIComponent(this.getAttribute('source'))+'/meta',controller.signal);if(!controller.signal.aborted)this.innerHTML=meta.native?documentPage(meta):'<p>No readable text is available. <a href="/documents">Back to Documents</a></p>';}catch(e){if(!controller.signal.aborted)this.innerHTML=`<p role="alert">${esc(e.message)}</p><a href="/documents">Back to Documents</a>`;}}
  }
  class Prescriptions extends HTMLElement {
    connectedCallback(){this.load();}
    disconnectedCallback(){this.controller?.abort();}
    async load(){this.controller=new AbortController();try{const data=await read('/api/prescriptions',this.controller.signal);if(this.controller.signal.aborted)return;const subject=this.getAttribute('subject');const rows=data.prescriptions.filter(r=>!subject||r.medicine_id===subject);if(this.hasAttribute('compact')){const row=rows.find(r=>r.treatment_status==='current');this.innerHTML=row?`<p><a href="${esc(row.source_url)}" class="rx-badge rx-${esc(row.expiry.state)}">${esc(row.expiry.label)}</a></p><p class="docs-category">${esc(row.repeat_label)}</p>`:'<span class="rx-badge rx-unknown">Expiry not recorded</span>';}else{this.innerHTML=rows.length?`<section class="native-prescriptions"><h2>Prescription information</h2>${rows.map(prescription).join('')}</section>`:(subject?.startsWith('med:')?'<p><span class="rx-badge rx-unknown">Expiry not recorded</span> · Prescription details not recorded. Remaining repeats not recorded. Supply remaining not recorded.</p>':'');}}catch(e){if(!this.controller.signal.aborted)this.innerHTML=`<p role="alert">${esc(e.message)}</p>`;}}
  }
  customElements.define('health-native-document',NativeDocument);
  customElements.define('health-prescriptions',Prescriptions);
  // Exposed only for the existing JavaScriptCore verification harness.
  globalThis.healthDocumentRendering={documentPage,prescription,release};
})();
