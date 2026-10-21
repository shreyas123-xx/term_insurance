"""Mock insurer quote pages + mock quote-form portal (stands in for public pages)."""
import math
from .core import TransientError

INSURERS = {
    "securelife": dict(name="SecureLife", base=11800, csr=("98.1", "FY 2023-24"), fmt="annual",
        exc=["Suicide within first 12 months", "Death due to war"], rid=[("Critical Illness", 2400), ("Accidental Death", 900)]),
    "healthfirst": dict(name="HealthFirst", base=11300, csr=("97.4", "FY 2022-23"), fmt="monthly",
        exc=["Suicide within first 12 months", "Hazardous sports", "Pre-existing conditions (first 2 yrs)"], rid=[("Waiver of Premium", 700), ("Accidental Death", 1100)]),
    "trustshield": dict(name="TrustShield", base=12100, csr=("98.6", None), fmt="per_year",
        exc=["Suicide within first 12 months"], rid=[("Critical Illness", 2900), ("Income Benefit", 1500)]),
}

def page(key, p):
    i = INSURERS[key]
    prem = round(p.cover / 1e7 * i["base"] * (1 + max(0, p.age - 25) * 0.04) * (1 + (p.term - 20) * 0.004)
                 * (1.6 if p.answers()["smoker"] else 1))
    head = {"annual": f"Annual Premium: ₹{prem:,}", "per_year": f"₹{prem:,} per year (incl. GST)",
            "monthly": f"Starting at just ₹{math.ceil(prem * 1.05 / 12):,}/month*"}[i["fmt"]]
    csr = f"Claim Settlement Ratio: {i['csr'][0]}%" + (f" ({i['csr'][1]})" if i["csr"][1] else "")
    warn = "<p>Indicative non-smoker rate. Smoker status required for final underwriting.</p>" if p.answers()["smoker"] is None else ""
    html = (f"<h1>{i['name']} Term Plan</h1><p>Cover ₹{p.cover:,} for {p.term} years, age {p.age}</p>"
            f"<p>{head}</p>{warn}<p>{csr}</p><ul>" + "".join(f'<li class="exc">{e}</li>' for e in i["exc"]) +
            "</ul><ul>" + "".join(f'<li class="rider">{n} — ₹{c:,}/yr</li>' for n, c in i["rid"]) + "</ul>")
    return f"https://mock.insurers.test/{key}/term-quote?age={p.age}&cover={p.cover}&term={p.term}", html

class MockPortal:
    """Quote-form endpoint. Supports fault injection and idempotency keys (dedupes retries)."""
    def __init__(self, faults=None): self.faults, self.forms, self.by_key, self.executions = dict(faults or {}), {}, {}, 0
    def submit(self, insurer, idem_key, fields):
        if self.faults.get("submit_transient", 0) > 0:
            self.faults["submit_transient"] -= 1; raise TransientError("503 from quote portal")
        if idem_key in self.by_key: return {"form_id": self.by_key[idem_key], "duplicate": True}
        fid = f"FORM-{len(self.forms) + 1:04d}"; self.forms[fid] = {"insurer": insurer, "fields": dict(fields), "status": "DRAFT_QUOTE_FORM"}
        self.by_key[idem_key] = fid; self.executions += 1
        return {"form_id": fid, "duplicate": False}
    def read(self, fid): return self.forms[fid]
