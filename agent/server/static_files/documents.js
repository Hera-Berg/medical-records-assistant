// Illustrative correspondence is a bundled example library, never vault data.
(() => {
  const esc = value => String(value ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const monthNames = ['January','February','March','April','May','June','July','August','September','October','November','December'];
  const dateText = value => {
    if (!value || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return 'Date not recorded';
    const [year,month,day] = value.split('-');
    return `${Number(day)} ${monthNames[Number(month)-1]} ${year}`;
  };
  const documentIcon = '<svg aria-hidden="true" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M6 3h8l4 4v14H6zM14 3v5h5M9 12h6M9 16h6"/></svg>';
  async function read(url,signal) {
    const response=await fetch(url,{signal,cache:'no-store',headers:{accept:'application/json'}});
    if(!response.ok) throw Error('Documents could not be loaded. Restart the app if you have just updated it, then try again.');
    return response.json();
  }
  class HealthDocuments extends HTMLElement {
    connectedCallback() {
      this.source='examples';this.query='';this.category='';this.sort='newest';
      this.addEventListener('click',this.onClick);
      this.addEventListener('input',this.onInput);
      this.addEventListener('change',this.onChange);
      this.load();
    }
    disconnectedCallback() {
      this.controller?.abort();this.dialog?.close();
      this.removeEventListener('click',this.onClick);
      this.removeEventListener('input',this.onInput);
      this.removeEventListener('change',this.onChange);
    }
    async load() {
      this.controller?.abort();
      const controller=this.controller=new AbortController();
      this.innerHTML='<p role="status">Opening documents…</p>';
      const [examples,uploads]=await Promise.allSettled([read('/document-examples.json',controller.signal),read('/api/documents',controller.signal)]);
      if(!this.isConnected||controller!==this.controller||controller.signal.aborted)return;
      this.examples=examples.status==='fulfilled'?examples.value:null;
      this.uploads=uploads.status==='fulfilled'?uploads.value:null;
      this.render();
    }
    onClick = event => {
      const button=event.target.closest('button');
      if(!button||!this.contains(button))return;
      if(button.dataset.source) {
        this.source=button.dataset.source;this.query='';this.category='';this.render();
        this.querySelector(`[data-source="${this.source}"]`)?.focus();
      }
      if(button.dataset.document)this.open(button.dataset.document);
      if(button.dataset.action==='close')this.dialog.close();
      if(button.dataset.action==='reload')this.load();
      if(button.dataset.action==='download')this.download();
    };
    onInput = event => {
      if(event.target.name==='document-search'){this.query=event.target.value;this.renderResults();}
    };
    onChange = event => {
      if(event.target.name==='document-category')this.category=event.target.value;
      if(event.target.name==='document-sort')this.sort=event.target.value;
      this.renderResults();
    };
    items() {return (this.source==='examples'?this.examples:this.uploads)?.documents||[];}
    filtered() {
      const query=this.query.trim().toLocaleLowerCase();
      return this.items().filter(doc=>(!this.category||doc.category===this.category)&&(!query||[doc.title,doc.category,doc.professional,doc.organisation,doc.role,doc.date,dateText(doc.date||doc.document_date||doc.added_date),doc.document_date,doc.added_date,doc.patient,doc.recipient].join(' ').toLocaleLowerCase().includes(query)))
        .sort((a,b)=>{const order=(b.date||b.document_date||b.added_date||'').localeCompare(a.date||a.document_date||a.added_date||'')||a.title.localeCompare(b.title);return this.sort==='oldest'?-order:order;});
    }
    render() {
      const categories=[...new Set(this.items().map(doc=>doc.category))].sort();
      this.innerHTML=`<section class="docs-library" aria-label="Medical documents"><div class="docs-library-heading"><div><p class="docs-eyebrow">LETTERS & REPORTS</p><p>Your medical correspondence, organised by date.</p></div><div class="docs-heading-actions"><a class="btn" href="/files">Browse record folder</a><a class="btn btn-primary" href="/add">+ Add a document</a></div></div>
      <div class="docs-source-switch" role="group" aria-label="Choose document collection"><button type="button" data-source="examples" aria-pressed="${this.source==='examples'}">Example documents <span>${this.examples?.documents.length??'—'}</span></button><button type="button" data-source="uploads" aria-pressed="${this.source==='uploads'}">My uploads <span>${this.uploads?.total??'—'}</span></button></div>
      ${this.source==='examples'?'<div class="docs-example-notice"><strong>Illustrative documents · Jane Doe</strong><span>Sample letters and results addressed to Dr James Smith. These examples are separate from your patient record.</span></div>':'<p class="docs-upload-note">Original documents you have added to your record. Dates are labelled as document dates or dates added; sharing details are shown only when recorded.</p>'}
      <div class="docs-tools"><label class="docs-search"><span>Search documents</span><input type="search" name="document-search" placeholder="Search by title, professional or date" value="${esc(this.query)}"></label><label><span>Document type</span><select name="document-category"><option value="">All types</option>${categories.map(category=>`<option${category===this.category?' selected':''}>${esc(category)}</option>`).join('')}</select></label><label><span>Sort by</span><select name="document-sort"><option value="newest"${this.sort==='newest'?' selected':''}>Newest first</option><option value="oldest"${this.sort==='oldest'?' selected':''}>Oldest first</option></select></label></div>
      <p class="docs-count" role="status" aria-live="polite"></p><div class="docs-results"></div><dialog class="docs-dialog" aria-labelledby="docs-preview-title"></dialog></section>`;
      this.dialog=this.querySelector('dialog');this.renderResults();
    }
    renderResults() {
      const collection=this.source==='examples'?this.examples:this.uploads;
      const results=this.querySelector('.docs-results');
      if(!collection) {
        results.innerHTML='<div class="docs-load-error" role="alert">This document collection could not be loaded. If you have just updated the app, restart it and refresh.<button class="btn" type="button" data-action="reload">Try again</button></div>';
        this.querySelector('.docs-count').textContent='';return;
      }
      const docs=this.filtered();
      this.querySelector('.docs-count').textContent=`${docs.length} of ${this.items().length} ${this.source==='examples'?'example ':''}${this.items().length===1?'document':'documents'}`;
      if(!docs.length) {
        results.innerHTML=this.items().length?'<p class="docs-empty">No documents match your search. Try another title, professional or date.</p>':'<div class="docs-empty"><h2>No uploads yet</h2><p>Add a letter, report or photograph to start your own document list.</p><a class="btn" href="/add">Add a document</a></div>';return;
      }
      results.innerHTML=`<div class="docs-table-wrap"><table class="docs-table"><thead><tr><th scope="col">Document</th><th scope="col">From</th><th scope="col">Date</th><th scope="col"><span class="docs-sr-only">Open document</span></th></tr></thead><tbody>${docs.map(doc=>this.row(doc)).join('')}</tbody></table></div>`;
    }
    row(doc) {
      const example=this.source==='examples';
      const title=example?`<button type="button" class="docs-title" data-document="${esc(doc.id)}">${esc(doc.title)}</button>`:`<a class="docs-title" href="/artifact/${encodeURIComponent(doc.id)}">${esc(doc.title)}</a>`;
      return `<tr><td><div class="docs-document-cell"><span class="docs-file-icon">${documentIcon}</span><div>${title}<p class="docs-category">${esc(doc.category)}${example?' <span class="docs-sample-label">Example</span>':''}</p>${example?`<p class="docs-addressing">To ${esc(doc.recipient)} · Re ${esc(doc.patient)}</p>`:''}<p class="docs-release">${example?esc(doc.release_note):'Added to your record'}</p></div></div></td><td><p class="docs-sender">${example?esc(doc.professional):'Uploaded document'}</p>${example?`<p class="docs-profession">${esc(doc.role)}</p><p class="docs-organisation">${esc(doc.organisation)}</p>`:''}</td><td class="docs-date"><time datetime="${esc(example?doc.date:doc.document_date||doc.added_date||'')}">${esc(dateText(example?doc.date:doc.document_date||doc.added_date))}</time>${example?'':`<span>${doc.document_date?'Document dated':'Added to record'}</span>`}</td><td>${example?`<button type="button" class="docs-open" data-document="${esc(doc.id)}" aria-label="Open ${esc(doc.title)}, ${esc(dateText(doc.date))}">View <span aria-hidden="true">↗</span></button>`:`<a class="docs-open" href="/artifact/${encodeURIComponent(doc.id)}">Open <span aria-hidden="true">↗</span></a>`}</td></tr>`;
    }
    open(id) {
      const doc=this.examples?.documents.find(item=>item.id===id);if(!doc)return;
      this.selected=doc;
      this.dialog.innerHTML=`<div class="docs-preview-toolbar"><span class="docs-sample-label">Illustrative example</span><button type="button" data-action="close" class="docs-close" aria-label="Close document">×</button></div><article class="docs-letter"><header class="docs-letter-heading"><p class="docs-organisation">${esc(doc.organisation)}</p><h2 id="docs-preview-title">${esc(doc.title)}</h2><p>${esc(doc.professional)} · ${esc(doc.role)}</p><p><time datetime="${esc(doc.date)}">${esc(dateText(doc.date))}</time></p></header><dl class="docs-letter-address"><div><dt>To</dt><dd>${esc(doc.recipient)}</dd></div><div><dt>Re</dt><dd>${esc(doc.patient)}</dd></div></dl>${doc.sections.map(section=>`<section><h3>${esc(section.heading)}</h3><p>${esc(section.text)}</p></section>`).join('')}${doc.results.length?`<section><h3>Results</h3><div class="docs-table-wrap"><table class="docs-lab-results"><thead><tr><th scope="col">Test</th><th scope="col">Result</th><th scope="col">Units</th></tr></thead><tbody>${doc.results.map(result=>`<tr><th scope="row">${esc(result.test)}</th><td>${esc(result.result)}</td><td>${esc(result.unit)}</td></tr>`).join('')}</tbody></table></div></section>`:''}<footer><p>Kind regards,</p><p><strong>${esc(doc.professional)}</strong><br>${esc(doc.role)}<br>${esc(doc.organisation)}</p><p class="docs-release">${esc(doc.release_note)}</p><p class="docs-example-footer">Illustrative document for a fictional patient. This example is not part of your clinical record.</p></footer></article><div class="docs-preview-actions"><button type="button" class="btn" data-action="download">Download example (.txt)</button><button type="button" class="btn btn-primary" data-action="close">Close</button></div>`;
      this.dialog.showModal();this.dialog.scrollTop=0;this.dialog.querySelector('[data-action="close"]')?.focus();
    }
    download() {
      const doc=this.selected;if(!doc)return;
      const text=['ILLUSTRATIVE EXAMPLE — FICTIONAL PATIENT',doc.organisation,doc.title,doc.professional+' — '+doc.role,dateText(doc.date),'To: '+doc.recipient,'Re: '+doc.patient,'',...doc.sections.flatMap(section=>[section.heading,section.text,'']),...doc.results.map(result=>`${result.test}: ${result.result} ${result.unit}`),'',doc.release_note,'This example is separate from the patient record.'].join('\n');
      const url=URL.createObjectURL(new Blob([text],{type:'text/plain;charset=utf-8'}));
      const link=document.createElement('a');link.href=url;link.download=`example-${doc.id}.txt`;document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
    }
  }
  customElements.define('health-documents',HealthDocuments);
})();
