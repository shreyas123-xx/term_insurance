"""Domain core: validated inputs, extraction, evidence ledger, cache, approval gate."""
from __future__ import annotations
import datetime as dt, hashlib, json, re, time
from dataclasses import dataclass
from pathlib import Path

HEALTH_QS = {
    "smoker": "Used tobacco/nicotine in the last 12 months?",
    "diabetes": "Diagnosed with diabetes?",
    "hypertension": "Diagnosed with hypertension?",
    "heart_condition": "Any heart condition?",
    "prior_surgery": "Any major surgery in the last 5 years?",
}

class ToolError(Exception): pass
class TransientError(ToolError): pass          # safe to retry
class ExtractionError(ToolError): pass         # never guess; mark uncertain

def utcnow() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()

def digest(obj, n=10) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()[:n]

# ---------- 1. INPUTS -------------------------------------------------------
@dataclass(frozen=True)
class Profile:
    name: str; age: int; cover: int; term: int
    health: dict          # question -> True / False / None (None = NOT ANSWERED)

    def answers(self) -> dict:
        return {k: self.health.get(k) for k in HEALTH_QS}     # missing key == unanswered

    def unanswered(self) -> list:
        return [k for k, v in self.answers().items() if v is None]

    @property
    def app_id(self) -> str:                                   # stable ID from canonical content
        return "APP-" + digest([self.name, self.age, self.cover, self.term, self.answers()], 8).upper()

    def validate(self) -> list:
        e = []
        if not 18 <= self.age <= 65: e.append(f"age {self.age} outside 18-65")
        if not 2_500_000 <= self.cover <= 100_000_000: e.append("cover must be ₹25L-₹10Cr")
        if not 5 <= self.term <= 40: e.append("term must be 5-40 years")
        if self.age + self.term > 85: e.append("age + term exceeds 85")
        for k, v in self.health.items():
            if k not in HEALTH_QS: e.append(f"unknown health question '{k}'")
            elif v not in (True, False, None): e.append(f"'{k}' must be true/false/null")
        return e

# ---------- 2. EXTRACTION (regex over page text; failure => uncertain) -----
def _text(html): return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))
def _int(s): return int(s.replace(",", ""))

def extract(html: str) -> dict:
    t = _text(html)
    m = re.search(r"₹\s*([\d,]+)\s*/\s*month", t)
    if m:                                           # TC2: monthly teaser -> annual
        monthly = _int(m.group(1)); premium, basis = monthly * 12, f"monthly ₹{monthly:,} × 12 (converted)"
    else:
        m = re.search(r"Annual Premium:\s*₹\s*([\d,]+)", t) or re.search(r"₹\s*([\d,]+)\s*per year", t)
        if not m: raise ExtractionError("no premium pattern found on page")
        premium, basis = _int(m.group(1)), "annual, as published"
    c = re.search(r"[Cc]laim (?:[Ss]ettlement )?[Rr]atio:?\s*([\d.]+)\s*%\s*(?:\(\s*(FY\s*[\d\-]+)\s*\))?", t)
    return {
        "premium_annual": premium, "premium_basis": basis, "premium_raw": m.group(0),
        "claim_ratio": float(c.group(1)) if c else None,
        "claim_year": c.group(2) if c and c.group(2) else None,   # TC3: never assume a year
        "exclusions": re.findall(r'<li class="exc">(.*?)</li>', html),
        "riders": re.findall(r'<li class="rider">(.*?)</li>', html),
        "provisional": "Indicative" in t,
    }

# ---------- Innovation: TTL cache ------------------------------------------
class FileCache:
    def __init__(self, path, ttl=900): self.dir, self.ttl = Path(path), ttl; self.dir.mkdir(parents=True, exist_ok=True)
    def get(self, key):
        f = self.dir / f"{digest(key, 16)}.json"
        if f.exists():
            d = json.loads(f.read_text())
            if time.time() - d["t"] < self.ttl: return d["v"]
    def set(self, key, v): (self.dir / f"{digest(key, 16)}.json").write_text(json.dumps({"t": time.time(), "v": v}))

# ---------- 4. HUMAN-IN-THE-LOOP APPROVAL GATE -----------------------------
class ApprovalGate:
    """mode: 'interactive' (real human) | 'approve' / 'deny' (labelled simulated approver for tests)."""
    def __init__(self, mode="interactive"): self.mode, self.store = mode, {}

    def request(self, proposal: dict) -> dict:
        print("\n  ┌─ HUMAN APPROVAL REQUIRED " + "─" * 40)
        print(f"  │ Destination : {proposal['destination']}")
        print(f"  │ Insurer     : {proposal['insurer']}   Application: {proposal['app_id']}")
        for k, v in proposal["fields"].items():
            shown = "⚠ PENDING HUMAN ANSWER (left blank)" if v is None else v
            print(f"  │   {k:<16}= {shown}")
        print("  └" + "─" * 66)
        if self.mode == "interactive": ok = input("  Approve this form fill? [y/N] ").strip().lower() == "y"
        else: ok = self.mode == "approve"; print(f"  [simulated approver: {'APPROVE' if ok else 'DENY'}]")
        rec = {"approved": ok, "by": "human" if self.mode == "interactive" else f"simulated-{self.mode}",
               "at": utcnow(), "approved_payload_hash": digest(proposal["fields"])}
        self.store[proposal["idempotency_key"]] = rec
        return rec
