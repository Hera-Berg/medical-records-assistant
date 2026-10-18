// Overview reads the same reconciled record as the other screens. It writes nothing.
(() => {
  const escape = (value) => String(value ?? '').replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const arrow = '<span aria-hidden="true">›</span>';
  const paths = {
    document: '<path d="M6 3h8l4 4v14H6z"/><path d="M14 3v5h5M9 12h6M9 16h6"/>',
    calendar: '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M7 3v4M17 3v4M3 11h18"/>',
    medicine: '<path d="M8 4a5 5 0 0 1 7 7l-4 4a5 5 0 0 1-7-7zM7 5l7 7"/>',
    note: '<path d="M13 5H4v16h16v-9M11 13l-1 4 4-1 7-7-3-3zM16 8l3 3"/>',
    people: '<circle cx="9" cy="7" r="3"/><path d="M3 21v-4a6 6 0 0 1 12 0v4zM16 4a3 3 0 0 1 0 6M18 14a5 5 0 0 1 3 5v2"/>',
    shield: '<path d="M12 2l8 4v6c0 5-8 10-8 10S4 17 4 12V6zM8 12l3 3 5-6"/>',
    heart: '<path d="M20 5c-3-3-7-1-8 1-1-2-5-4-8-1-4 4 1 9 8 15 7-6 12-11 8-15z"/>',
    clock: '<circle cx="12" cy="12" r="9"/><path d="M12 6v6l4 2"/>',
  };
  const icon = (name) => `<svg aria-hidden="true" viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">${paths[name] || paths.document}</svg>`;
  const entityURL = (id) => `/record/${encodeURIComponent(id)}`;
  function rowURL(row) {
    if (row.subject_id) return entityURL(row.subject_id);
    if (row.citation?.artifact) return `/artifact/${encodeURIComponent(row.citation.artifact)}`;
    return '/timeline';
  }
  async function read(url, signal) {
    const response = await fetch(url, {signal, cache:'no-store', headers:{accept:'application/json'}});
    if (!response.ok) throw new Error('This part of your record could not be loaded.');
    return response.json();
  }
  class HealthOverview extends HTMLElement {
    connectedCallback() {
      this.addEventListener('click', this.onClick);
      this.load();
    }
    disconnectedCallback() {
      this.controller?.abort();
      this.removeEventListener('click', this.onClick);
    }
    onClick = (event) => {
      if (event.target.closest('[data-overview-retry]')) this.load();
    };
    async load() {
      this.controller?.abort();
      const controller = this.controller = new AbortController();
      this.innerHTML = '<p class="ov-loading" role="status">Opening your overview…</p>';
      const [profile, timeline] = await Promise.allSettled([
        read('/api/profile', controller.signal),
        read('/api/timeline?limit=3', controller.signal),
      ]);
      if (!this.isConnected || controller !== this.controller || controller.signal.aborted) return;
      this.profile = profile.status === 'fulfilled' ? profile.value : null;
      this.timeline = timeline.status === 'fulfilled' ? timeline.value : null;
      this.render();
    }
    retry(text) {
      return `<div class="ov-unavailable" role="status"><p>${text}</p><button type="button" class="ov-text-button" data-overview-retry>Try again</button></div>`;
    }
    timelineRows() {
      if (!this.timeline) return this.retry('Your timeline is temporarily unavailable.');
      const rows = this.timeline.rows || [];
      if (!rows.length) return `<div class="ov-empty-state"><span class="ov-icon-circle ov-blue">${icon('clock')}</span><h4>Your story starts here</h4><p>Add a document, photo or personal note. Your recent record entries will appear here.</p><a class="ov-link" href="/add">Add your first record ${arrow}</a></div>`;
      return `<ol class="ov-timeline">${rows.map((row) => {
        const personal = row.marker === 'patient-reported' || row.marker === 'note';
        const type = row.marker === 'artefact' ? 'Document added' : personal ? 'Patient-reported entry' : row.marker === 'lab-issued' ? 'Lab record' : row.marker === 'prescriber-issued' ? 'Clinical record' : 'Record entry';
        return `<li><span class="ov-timeline-dot" aria-hidden="true"></span><span class="ov-icon-circle ${personal ? 'ov-sand' : row.marker === 'lab-issued' ? 'ov-mint' : 'ov-blue'}">${icon(personal ? 'note' : row.marker === 'lab-issued' ? 'heart' : 'document')}</span><div class="ov-event"><p class="ov-event-date">${escape(row.date?.render || 'Date not recorded')}${row.date_label ? ' · ' + escape(row.date_label) : ''}</p><h4><a href="${rowURL(row)}">${type}</a></h4><p class="ov-event-text">${escape(row.text)}</p>${row.reading?.state ? `<p class="ov-event-state">${escape(row.reading.text || row.reading.state)}</p>` : ''}<a class="ov-event-source" href="${rowURL(row)}">${icon('document')}<span>${row.citation?.resolved ? 'View source' : 'View entry'} ${arrow}</span></a></div></li>`;
      }).join('')}</ol>`;
    }
    medications() {
      if (!this.profile) return this.retry('Your medications are temporarily unavailable.');
      const meds = this.profile.clinical?.medications || [];
      if (!meds.length) return '<p class="ov-empty-text">No medications confirmed yet. Add a prescription and review what it says to start your list.</p>';
      return `<div class="ov-medication-wrap"><table class="ov-medications"><caption class="ov-sr-only">Medications from your clinical record</caption><thead class="ov-sr-only"><tr><th scope="col">Medication</th><th scope="col">Dose and status</th></tr></thead><tbody>${meds.slice(0,4).map(med => `<tr><th scope="row"><a href="${entityURL(med.id)}">${icon('medicine')}<span>${escape(med.name)}</span></a></th><td>${med.conflicted ? '<span class="ov-status">Conflicting entries</span>' : escape(med.dose || 'Dose not recorded')}${med.stale ? '<span class="ov-status">Needs confirming</span>' : ''}</td></tr>`).join('')}</tbody></table></div>${meds.length > 4 ? `<p class="ov-more-count">And ${meds.length - 4} more in your medication list.</p>` : ''}`;
    }
    careTeam() {
      if (!this.profile) return this.retry('Your care team details are temporarily unavailable.');
      const p = this.profile.values;
      if (!p.gp_name && !p.gp_practice && !p.pharmacy) return '<p class="ov-empty-text">Keep your usual GP and pharmacy close at hand. Add their details in your patient profile.</p>';
      return `<dl class="ov-care-details">${p.gp_name || p.gp_practice ? `<div><dt>Usual GP</dt><dd>${escape(p.gp_name || 'Name not added')}${p.gp_practice ? `<span>${escape(p.gp_practice)}</span>` : ''}${p.gp_phone ? `<span>${escape(p.gp_phone)}</span>` : ''}</dd></div>` : ''}${p.pharmacy ? `<div><dt>Pharmacy</dt><dd>${escape(p.pharmacy)}</dd></div>` : ''}</dl>`;
    }
    render() {
      const p = this.profile?.values || {};
      const name = p.preferred_name?.trim() || p.full_name?.trim().split(/\s+/)[0] || '';
      const hour = new Date().getHours();
      const greeting = name ? `${hour < 12 ? 'Good morning' : hour < 18 ? 'Good afternoon' : 'Good evening'}, ${name}` : 'Welcome to your health record';
      const initials = (p.full_name || p.preferred_name || '').trim().split(/\s+/).filter(Boolean).slice(0,2).map(word => Array.from(word)[0]).join('').toUpperCase();
      const review = this.profile?.review_count || 0;
      const allergies = this.profile?.clinical?.allergies || [];
      this.innerHTML = `
        <div class="ov-topline"><p class="ov-eyebrow">YOUR PERSONAL HEALTH RECORD</p><a class="ov-profile-switch" href="/profile"><span>Viewing: <strong>My record</strong></span><span class="ov-avatar" aria-hidden="true">${escape(initials) || icon('people')}</span></a></div>
        <header class="ov-welcome"><h2>${escape(greeting)}</h2><p>Your health story, in one place.</p></header>
        ${!this.profile ? `<div class="ov-load-error" role="alert">Your patient details could not be loaded. If you have just updated the app, restart it and refresh. <button type="button" class="ov-text-button" data-overview-retry>Try again</button></div>` : ''}
        <section class="ov-appointment" aria-labelledby="ov-appointment-title"><span class="ov-icon-circle ov-mint">${icon('document')}</span><div><h3 id="ov-appointment-title">Ready for your next appointment?</h3><p>Bring your medications, recent changes and questions together.</p></div><a class="ov-primary-link" href="/summary">Prepare a visit summary ${arrow}</a></section>
        <div class="ov-main-grid"><section class="ov-panel ov-timeline-panel" aria-labelledby="ov-timeline-title"><div class="ov-panel-heading"><h3 id="ov-timeline-title">Your health timeline</h3><a class="ov-link" href="/timeline">View all ${arrow}</a></div>${this.timelineRows()}</section>
        <div class="ov-right-column"><section class="ov-panel" aria-labelledby="ov-care-title"><div class="ov-panel-heading ov-heading-with-icon"><span class="ov-icon-circle ov-blue">${icon('people')}</span><div><h3 id="ov-care-title">Your care team</h3><p>The people who look after you.</p></div></div>${this.careTeam()}<a class="ov-link ov-panel-footer" href="/profile">View patient profile ${arrow}</a></section>
        <section class="ov-panel" aria-labelledby="ov-medications-title"><div class="ov-panel-heading ov-heading-with-icon"><span class="ov-icon-circle ov-mint">${icon('medicine')}</span><div><h3 id="ov-medications-title">Current medications</h3><p>From your confirmed record.</p></div></div>${this.medications()}<a class="ov-link ov-panel-footer" href="/record">View medication list ${arrow}</a></section></div></div>
        <div class="ov-bottom-grid"><section class="ov-panel ov-action-panel"><span class="ov-icon-circle ov-sand">${icon('note')}</span><div><h3>What matters day to day</h3><p>Your routines, symptoms and things you want to remember.</p><a class="ov-link" href="/add">Add a personal note ${arrow}</a></div></section><section class="ov-panel ov-action-panel"><span class="ov-icon-circle ov-blue">${icon('shield')}</span><div><h3>Your record, your control</h3><p>Manage your preferences and where your record is kept.</p><a class="ov-link" href="/settings">Manage settings ${arrow}</a></div></section></div>
        <section class="ov-record-strip" aria-label="Important record information"><div>${icon('heart')}<span><strong>Allergies & reactions</strong><br>${!this.profile ? 'Temporarily unavailable' : allergies.length ? `${allergies.length} confirmed ${allergies.length === 1 ? 'entry' : 'entries'}` : 'Allergy status not established'}${p.allergy_notes ? ' · Patient-entered notes also available' : ''}</span><a class="ov-link" href="/profile">View details ${arrow}</a></div><div>${icon('document')}<span><strong>${review ? `${review} ${review === 1 ? 'item needs' : 'items need'} your review` : 'Keep your record up to date'}</strong><br>${review ? 'Confirm what was read from your documents.' : 'Original documents and review decisions, together.'}</span><a class="ov-link" href="${review ? '/review' : '/documents'}">${review ? 'Review now' : 'Open documents'} ${arrow}</a></div></section>`;
    }
  }
  customElements.define('health-overview', HealthOverview);
})();
