"""Consistent names for authored clinical sources; findings and dates stay separate."""
SPECIALISTS={'Cardiologist','Audiologist','Physiotherapist','Podiatrist'}
TEST_NAMES={'Full blood examination (FBE)':'Full blood examination','Full blood examination & biochemistry':'Full blood examination and biochemistry'}
def title(payload):
    native=payload.get('native_document') or {}
    panel=native.get('ordered_panel') or payload.get('ordered_panel')
    if panel:return TEST_NAMES.get(panel,panel)
    role=payload.get('role') or native.get('specialty')
    author=payload.get('author') or native.get('professional')
    if role in SPECIALISTS and author:return f'{author} - {role} letter'
    return native.get('title') or payload.get('title') or 'Clinical document'
