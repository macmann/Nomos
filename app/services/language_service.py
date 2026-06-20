def detect_language(text: str) -> str:
    t=(text or '').lower()
    return 'de-DE' if any(w in t for w in ['guten','bitte','zähler','anmeldung','marktlokation','lieferantenwechsel']) else 'en-US'

def is_ivr(text: str) -> bool:
    t=(text or '').lower(); return any(w in t for w in ['press','drücken','wählen','menu','menü','option'])

def dtmf_for_ivr(text: str) -> str|None:
    t=(text or '').lower()
    if any(w in t for w in ['supplier switching','lieferantenwechsel','registration','anmeldung']): return '1'
    if any(w in t for w in ['meter','zähler']): return '2'
    if any(w in t for w in ['operator','mitarbeiter']): return '0'
    return None
