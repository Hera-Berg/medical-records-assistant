export function ClinicianHome() {
  return (
    <section className="clinician-home" aria-labelledby="clinician-welcome">
      <p className="ov-eyebrow">CLINICIAN WORKSPACE</p>
      <div className="ov-welcome">
        <h2 id="clinician-welcome">Review what has changed</h2>
        <p>Since the last consultation, bringing together patient, carer, staff and clinical information, with every summary linked to its original source.</p>
      </div>
      <section className="rounded-xl border border-[color:var(--color-rule)] bg-[color:var(--color-paper)] p-6" aria-labelledby="review-dashboard">
        <h3 id="review-dashboard" className="text-xl font-semibold">Review dashboard</h3>
        <p className="mt-2 text-[color:var(--color-muted)]">Placeholder for the university demonstration. The review dashboard will be added in a later step.</p>
      </section>
      <p className="mt-4 text-[color:var(--color-muted)]">The demo workspace switch changes the view only. It does not sign you in or unlock protected clinical notes.</p>
    </section>
  );
}
