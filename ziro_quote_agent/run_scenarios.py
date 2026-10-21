"""TC1-TC4 executable scenarios. Each stores input, expected, actual, checks and evidence in results/TCx.json."""
import json, tempfile
from pathlib import Path
from .agent import Agent
from .core import ApprovalGate, FileCache, Profile
from .mock_web import MockPortal

OK = dict(smoker=False, diabetes=False, hypertension=False, heart_condition=False, prior_surgery=False)

def run(tc, title, expected, profile, faults=None, extra=None):
    print(f"\n{'█'*72}\n{tc}: {title}\nEXPECTED: {expected}\n{'█'*72}")
    portal = MockPortal(faults); ag = Agent(profile, ApprovalGate("approve"), portal=portal, cache=FileCache(tempfile.mkdtemp()))
    s = ag.run(); return ag, s, portal

def main():
    res = {}; out = Path("results"); out.mkdir(exist_ok=True)
    # TC1
    p = Profile("Asha Rao", 30, 10_000_000, 30, OK)
    ag, s, pt = run("TC1", "Same age and cover", "premiums for identical profile compared across all insurers; form filled once despite a transient portal fault", p, {"submit_transient": 1})
    dup = ag.tb.submit_form(s.decision["draft_target"])                    # retry must not execute twice
    ch = {"3 insurers compared": len(s.quotes) == 3, "same app_id on every record": {r["app_id"] for r in s.ledger} == {p.app_id},
          "cheapest chosen": s.decision["recommended"] == min(s.ledger, key=lambda r: r["premium_annual"])["insurer"],
          "form executed exactly once": pt.executions == 1 and dup["status"] == "already_submitted",
          "verified": all(v["status"] == "VERIFIED" for v in s.verification.values())}
    res["TC1"] = (p, ch, s)
    # TC2
    ag, s, pt = run("TC2", "Monthly teaser", "monthly ₹/month teaser converted to annual (×12) and labelled as converted", p)
    r = s.quotes["healthfirst"]; ch = {"basis says converted": "converted" in r["premium_basis"],
          "annual == monthly×12": r["premium_annual"] == int(r["premium_raw"].split("₹")[1].split("/")[0].replace(",", "")) * 12}
    res["TC2"] = (p, ch, s)
    # TC3
    ag, s, pt = run("TC3", "Claim ratio", "every claim ratio labelled with its year; undisclosed year flagged, never guessed", p)
    ch = {"year on securelife": "FY 2023-24" in s.quotes["securelife"]["claim_label"], "year on healthfirst": "FY 2022-23" in s.quotes["healthfirst"]["claim_label"],
          "undisclosed year flagged": "year not disclosed" in s.quotes["trustshield"]["claim_label"]}
    res["TC3"] = (p, ch, s)
    # TC4
    p4 = Profile("Asha Rao", 30, 10_000_000, 30, {**OK, "smoker": None})
    ag, s, pt = run("TC4", "Health question unanswered", "UNCERTAIN state, no recommendation, field left blank for human, nothing invented", p4)
    t = s.decision["draft_target"]; form = pt.forms[next(iter(pt.forms))]["fields"]
    ch = {"all quotes UNCERTAIN": all(r["status"] == "UNCERTAIN" for r in s.quotes.values()), "no recommendation": s.decision["recommended"] is None,
          "smoker field stayed null": form["smoker"] is None, "flagged pending human": s.verification[t]["pending_human_fields"] == ["smoker"]}
    res["TC4"] = (p4, ch, s)
    print("\n" + "=" * 72 + "\nSCENARIO SUMMARY")
    for tc, (p, ch, s) in res.items():
        ok = all(ch.values()); print(f"{tc}: {'PASS' if ok else 'FAIL'}  " + " | ".join(f"{'✓' if v else '✗'} {k}" for k, v in ch.items()))
        (out / f"{tc}.json").write_text(json.dumps({"input": p.__dict__, "app_id": p.app_id, "checks": ch, "passed": ok, "decision": s.decision,
              "premium_records": s.ledger, "verification": s.verification, "log": s.log}, indent=2, default=str))
if __name__ == "__main__": main()
