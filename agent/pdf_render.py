"""One process-wide guard for PDFium, whose rendering API is not thread-safe.

Both ingestion and on-screen previews use this lock. It does not alter source
bytes, permissions or the reader's processing decisions.
"""
from threading import RLock
PDF_RENDER_LOCK=RLock()
