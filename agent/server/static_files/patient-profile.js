// Patient details are fetched from the vault; no patient data is stored in the browser.
const escapeText = (value) => String(value ?? '').replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const months = ['January','February','March','April','May','June','July','August','September','October','November','December'];
function dateLabel(value) {
  if (!value) return '';
  if (!/^\d{4}-\d{2}(?:-\d{2})?$/.test(value)) return value;
  const [year,month,day] = value.split('-');
  if (!months[Number(month)-1]) return value;
  return `${day ? Number(day) + ' ' : ''}${months[Number(month)-1]} ${year}`;
}
function initials(name) {
  return name.trim().split(/\s+/).filter(Boolean).slice(0,2).map(word => Array.from(word)[0]).join('').toUpperCase() || 'P';
}
async function requestProfile(init) {
  const response = await fetch('/api/profile', {cache:'no-store', ...init, headers:{accept:'application/json', ...init?.headers}});
  if (!response.ok) {
    let message = 'Could not load the patient profile. Please try again.';
    try { const body = await response.json(); if (typeof body.detail === 'string') message = body.detail; } catch {}
    if (response.status === 404) message = 'Restart Health Record to enable the patient profile, then refresh this page.';
    throw new Error(message);
  }
  return response.json();
}
class PatientProfile extends HTMLElement {
  connectedCallback() {
    this.addEventListener('click', this.onClick);
    this.addEventListener('submit', this.onSubmit);
    this.load();
  }
  disconnectedCallback() {
    this.abort?.abort();
    this.removeEventListener('click', this.onClick);
    this.removeEventListener('submit', this.onSubmit);
    this.dialog?.close();
  }
  async load() {
    this.abort?.abort();
    this.abort = new AbortController();
    this.innerHTML = '<p class="profile-loading" role="status">Opening patient profile…</p>';
    try {
      this.data = await requestProfile({signal:this.abort.signal});
      if (this.isConnected) this.render();
    } catch (error) {
      if (error.name !== 'AbortError' && this.isConnected) this.innerHTML = `<div class="profile-error" role="alert">${escapeText(error.message)} <button class="btn" data-action="reload">Try again</button></div>`;
    }
  }
  onClick = (event) => {
    const button = event.target.closest('[data-action]');
    if (!button || !this.contains(button)) return;
    const action = button.dataset.action;
    if (action === 'reload') this.load();
    if (action === 'edit') this.edit(button.dataset.section);
    if (action === 'jump') this.focusSection(button.dataset.section);
    if (action === 'cancel' && !this.saving) this.dialog.close();
  };
  onSubmit = async (event) => {
    if (!event.target.matches('.profile-edit-form')) return;
    event.preventDefault();
    if (this.saving || !event.target.reportValidity()) return;
    const form = event.target;
    const values = Object.fromEntries(new FormData(form));
    this.saving = true;
    const errorBox = form.querySelector('[role="alert"]');
    errorBox.textContent = '';
    for (const button of form.querySelectorAll('button')) button.disabled = true;
    form.querySelector('[type="submit"]').textContent = 'Saving…';
    try {
      const data = await requestProfile({method:'PUT', headers:{'content-type':'application/json'}, body:JSON.stringify({values,revision:this.editRevision})});
      if (!this.isConnected) return;
      this.data = data;
      this.dialog.close();
      this.render();
      this.querySelector('.profile-feedback').textContent = 'Patient profile saved.';
      this.querySelector('[data-action="edit"]').focus();
    } catch (error) {
      errorBox.textContent = error.message;
      for (const button of form.querySelectorAll('button')) button.disabled = false;
      form.querySelector('[type="submit"]').textContent = 'Save changes';
    } finally { this.saving = false; }
  };
  fieldRows(section) {
    const values = this.data.values;
    const entries = section.fields.filter(field => values[field.key]);
    if (!entries.length) return '<p class="profile-empty">No details added yet.</p>';
    return `<dl class="profile-details">${entries.map(field => `<div><dt>${escapeText(field.label)}</dt><dd>${escapeText(['date','month'].includes(field.type) ? dateLabel(values[field.key]) : values[field.key])}</dd></div>`).join('')}</dl>`;
  }
  section(section) {
    return `<section class="profile-section" aria-labelledby="profile-${section.id}">
      <div class="profile-section-heading"><h2 id="profile-${section.id}">${escapeText(section.title)}</h2><button class="profile-text-button" data-action="edit" data-section="${section.id}" aria-label="Edit ${escapeText(section.title)}">Edit</button></div>
      ${this.fieldRows(section)}
    </section>`;
  }
  clinicalList(items, empty, allergy = false) {
    if (!items.length) return `<p class="profile-empty">${empty}</p>`;
    return `<ul class="profile-clinical-list">${items.map(item => `<li><div><a href="/record/${encodeURIComponent(item.id)}">${escapeText(item.name)}</a>${item.conflicted ? '<span class="profile-tag">Conflicting entries</span>' : ''}${item.stale ? '<span class="profile-tag">Needs confirming</span>' : ''}${item.status && !['active','conflicted','stale'].includes(item.status) ? `<span class="profile-tag">Status: ${escapeText(item.status)}</span>` : ''}${allergy ? `<p>${escapeText(item.reaction || 'Reaction not recorded')}${item.severity ? ' · ' + escapeText(item.severity) : ''}</p>` : item.dose ? `<p>${escapeText(item.dose)}</p>` : ''}</div><a class="profile-detail-link" href="/record/${encodeURIComponent(item.id)}" aria-label="View sources for ${escapeText(item.name)}">View record <span aria-hidden="true">↗</span></a></li>`).join('')}</ul>`;
  }
  render() {
    const {values:v, clinical:c, sections, updated_at} = this.data;
    const name = v.full_name || 'Your patient profile';
    const identity = [v.date_of_birth && dateLabel(v.date_of_birth), this.data.age != null && `${this.data.age} years`, v.pronouns, v.gender || v.sex_at_birth].filter(Boolean);
    const meds = c.medications.length;
    const conditions = c.problems.length;
    this.innerHTML = `
      <div class="profile-feedback" role="status" aria-live="polite"></div>
      <header class="profile-hero">
        <div class="profile-avatar" aria-hidden="true">${escapeText(initials(v.full_name || ''))}</div>
        <div class="profile-identity"><p class="profile-eyebrow">PATIENT PROFILE</p><h2>${escapeText(name)}</h2>${v.preferred_name ? `<p class="profile-preferred">Goes by <strong>${escapeText(v.preferred_name)}</strong></p>` : '<p class="profile-preferred">Your details, together in one place.</p>'}${identity.length ? `<p class="profile-demographics">${identity.map(escapeText).join('<span aria-hidden="true"> · </span>')}</p>` : ''}</div>
        <button class="btn btn-primary profile-edit-button" data-action="edit">Edit profile</button>
      </header>
      <nav class="profile-quicklinks" aria-label="Patient record shortcuts"><a href="/record">Medications <span>${meds}</span></a><a href="/record">Health conditions <span>${conditions}</span></a><a href="/review">Waiting for review <span>${this.data.review_count}</span></a><a href="/summary">Appointment sheet <span aria-hidden="true">↗</span></a></nav>
      <section class="profile-section profile-allergies" aria-labelledby="profile-allergy-heading">
        <div class="profile-section-heading"><div><p class="profile-eyebrow">AT A GLANCE</p><h2 id="profile-allergy-heading">Allergies & adverse reactions</h2></div><a href="/record">View clinical record ↗</a></div>
        ${this.clinicalList(c.allergies, 'No confirmed allergy entries. Allergy status has not been established.', true)}
        ${v.allergy_notes ? `<div class="profile-patient-note"><span class="profile-tag">Patient-entered</span><p>${escapeText(v.allergy_notes)}</p></div>` : ''}
        <button class="profile-text-button" data-action="edit" data-section="notes">${v.allergy_notes ? 'Edit patient-entered notes' : 'Add allergy or reaction notes'}</button>
      </section>
      <div class="profile-grid">${sections.filter(section => !['notes'].includes(section.id)).map(section => this.section(section)).join('')}</div>
      <div class="profile-grid profile-clinical-grid"><section class="profile-section"><div class="profile-section-heading"><h2>Medications</h2><a href="/record">View all ↗</a></div>${this.clinicalList(c.medications,'No medications confirmed yet.')}</section><section class="profile-section"><div class="profile-section-heading"><h2>Health conditions</h2><a href="/record">View all ↗</a></div>${this.clinicalList(c.problems,'No health conditions confirmed yet.')}</section></div>
      ${v.important_notes ? `<section class="profile-section profile-other-notes"><div class="profile-section-heading"><h2>Other information</h2><button class="profile-text-button" data-action="edit" data-section="notes">Edit</button></div><span class="profile-tag">Patient-entered</span><p>${escapeText(v.important_notes)}</p></section>` : ''}
      <p class="profile-footer">${updated_at ? 'Profile updated ' + escapeText(dateLabel(updated_at.slice(0,10))) + '. ' : 'Start with Edit profile to add your details. '}Clinical entries link to their sources.</p>
      <dialog class="profile-dialog" aria-labelledby="profile-editor-heading"></dialog>`;
    this.dialog = this.querySelector('dialog');
  }
  edit(sectionId) {
    this.editRevision = this.data.revision;
    this.dialog.innerHTML = `<form class="profile-edit-form"><header class="profile-dialog-heading"><div><p class="profile-eyebrow">PATIENT DETAILS</p><h2 id="profile-editor-heading">Edit patient profile</h2><p>All fields are optional. Save when you’re finished.</p></div><button class="profile-close" type="button" data-action="cancel" aria-label="Close profile editor">×</button></header><nav class="profile-editor-nav" aria-label="Profile sections">${this.data.sections.map(section => `<button type="button" data-action="jump" data-section="${section.id}">${escapeText(section.title)}</button>`).join('')}</nav><div class="profile-dialog-body">${this.data.sections.map(section => `<fieldset id="edit-${section.id}"><legend>${escapeText(section.title)}</legend><p>${escapeText(section.description)}</p><div class="profile-form-grid">${section.fields.map(field => this.input(field)).join('')}</div></fieldset>`).join('')}</div><footer class="profile-dialog-footer"><p role="alert"></p><div><button type="button" class="btn" data-action="cancel">Cancel</button><button type="submit" class="btn btn-primary">Save changes</button></div></footer></form>`;
    this.dialog.oncancel = (event) => { if (this.saving) event.preventDefault(); };
    this.dialog.showModal();
    if (sectionId) this.focusSection(sectionId);
    else this.dialog.querySelector('input')?.focus();
  }
  focusSection(sectionId) {
    const section = this.dialog.querySelector(`#edit-${sectionId}`);
    section?.scrollIntoView({block:'start'});
    section?.querySelector('input,select,textarea')?.focus({preventScroll:true});
  }

  input(field) {
    const value = this.data.values[field.key];
    const attrs = `id="patient-${field.key}" name="${field.key}"${field.autocomplete ? ` autocomplete="${field.autocomplete}"` : ''}`;
    let control;
    if (field.type === 'textarea') control = `<textarea ${attrs} rows="3" maxlength="${field.max_length}">${escapeText(value)}</textarea>`;
    else if (field.type === 'select') control = `<select ${attrs}><option value="">Not specified</option>${field.options.map(option => `<option${option === value ? ' selected' : ''}>${escapeText(option)}</option>`).join('')}</select>`;
    else control = `<input ${attrs} type="${field.type}" value="${escapeText(value)}" maxlength="${field.max_length}">`;
    return `<div class="profile-form-field${field.type === 'textarea' ? ' profile-wide-field' : ''}"><label for="patient-${field.key}">${escapeText(field.label)}</label>${control}</div>`;
  }
}
customElements.define('patient-profile', PatientProfile);
