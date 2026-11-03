"""Render clinician changes from the exact dashboard comparison selection.

The existing medication/allergy/condition sections and page budget are reused.
The trusted context is created by the server and pinned in the summary event.
"""
from dataclasses import replace
from datetime import date
from .model import Line, Section, Source
from . import budget
from ..projection.dates import render_date
from ..projection.citations import Citer


def apply(summary, context, artifacts, events):
    citer = Citer(artifacts, events)
    lines, seen = [], set()
    # Event JSON sorts dictionary keys. Keep export selection order independent
    # of dictionary insertion order so restoring a saved sheet is byte-stable.
    for category in ('reported','documents','medications','follow_up','newly_received'):
        rows=context['groups'].get(category,[])
        for row in rows:
            identity = (category if category=='newly_received' else 'change',row['id'])
            if identity in seen:
                continue
            seen.add(identity)
            short = row['source_url'].rsplit('/',1)[-1] if '/artifact/' in row['source_url'] else None
            citation = citer.cite(short or 'ev-'+row['id'],'Original typed note')
            artifact = artifacts.get(short) if short else None
            source = Source(tier=None,when=row['date'],citation=citation,artifact=short,
                            rel='../'+artifact.rel if artifact else '../'+citation.target if citation.target else None, url=row['source_url'])
            clock = ' · '.join(part for part in (
                row.get('date_basis'), row.get('ordering'),
                'Original date '+str(row.get('document_date') or row.get('event_date') or 'unknown') if category=='newly_received' else None,
                'Uploaded '+str(row.get('uploaded_at') or 'date not recorded') if category=='newly_received' else None,
                'Historical content; newly received, not a new clinical change' if category=='newly_received' else None,
                'Unresolved; original date '+row['date'] if row.get('outside_period') else None) if part)
            lines.append(Line(label=('Newly received: ' if category=='newly_received' else '')+row['headline'],
                              value=row['summary'],note=clock or None,sources=(source,)))
    period=context['period']
    note=context['basis']+' '+period['boundary_note']
    changes=Section(key='changes',heading='Changes since '+period['start_date'],subnote=note,
                    lines=tuple(lines),empty_note='No source-backed changes in the selected period.',truncatable=True)
    sections=(changes,*(section for section in summary.sections if section.key!='changes'))
    fitted=budget.fit(sections,question=summary.question,demo=summary.demo)
    sources=set(summary.sources)
    sources.update(s.artifact for line in lines for s in line.sources if s.artifact)
    return replace(summary,sections=fitted.sections,since=date.fromisoformat(period['start_date']),
                   since_ts=period['start_timestamp'],since_reason=period['start_date'],
                   prepared=date.fromisoformat(period['end_timestamp'][:10]),
                   prepared_words=render_date(date.fromisoformat(period['end_timestamp'][:10])),
                   sources=tuple(sorted(sources)),height_mm=fitted.height_mm,overflowed=fitted.overflowed)
