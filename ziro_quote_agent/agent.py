"""Agent runtime: Session state, ToolBox (tools), executor with retry, Rule + LLM planners."""
from __future__ import annotations
import json, os, time
from pathlib import Path
from dataclasses import dataclass, field
from .core import *
from .mock_web import INSURERS, MockPortal, page

C = {"THINK": "\033[36m", "ACT": "\033[33m", "OBS": "\033[32m", "WARN": "\033[31m", "GATE": "\033[35m"}
def say(sess, kind, msg):
    sess.log.append({"t": utcnow(), "kind": kind, "msg": msg})
    print(f"{C.get(kind,'')}{kind:<5}\033[0m {msg}")

@dataclass
class Session:
    profile: Profile
    quotes: dict = field(default_factory=dict)       # insurer -> row (OK / UNCERTAIN / FAILED)
    ledger: list = field(default_factory=list)       # every premium + date + source
    decision: dict | None = None
    proposals: dict = field(default_factory=dict)
    submissions: dict = field(default_factory=dict)
    verification: dict = field(default_factory=dict)
    log: list = field(default_factory=list)
    halted: bool = False; denied: bool = False; rendered: bool = False; validated: bool = False

# ---------- Tools (shared by both planners) --------------------------------
class ToolBox:
    def __init__(self, sess, gate, portal=None, cache=None):
        self.s, self.gate, self.portal, self.cache = sess, gate, portal or MockPortal(), cache

    def validate_inputs(self):
        errs = self.s.profile.validate(); self.s.validated = True
        if errs: self.s.halted = True; return {"valid": False, "errors": errs}
        return {"valid": True, "app_id": self.s.profile.app_id, "unanswered_health": self.s.profile.unanswered()}

    def fetch_quote(self, insurer):
        p = self.s.profile; url, html = page(insurer, p)
        key = [insurer, p.app_id]; hit = self.cache.get(key) if self.cache else None
        if hit: html, at = hit["html"], hit["at"]; say(self.s, "OBS", f"cache HIT for {insurer} (retrieved {at})")
        else: at = utcnow(); self.cache and self.cache.set(key, {"html": html, "at": at})
        sid = f"SRC-{insurer}-{digest(url, 6)}"
        try: d = extract(html)
        except ExtractionError as e:
            self.s.quotes[insurer] = {"insurer": insurer, "status": "FAILED", "reasons": [str(e)], "source_url": url, "source_id": sid}
            return {"status": "FAILED", "reason": str(e)}
        reasons = []
        if self.s.profile.unanswered(): reasons.append(f"health unanswered: {self.s.profile.unanswered()}")
        if d["provisional"]: reasons.append("page says premium is indicative only")
        notes = [] if d["claim_year"] or d["claim_ratio"] is None else ["claim-ratio year NOT disclosed by insurer"]
        row = {"insurer": insurer, "app_id": p.app_id, **d, "claim_label": (f"{d['claim_ratio']}% ({d['claim_year']})" if d["claim_year"]
               else (f"{d['claim_ratio']}% (year not disclosed)" if d["claim_ratio"] else "n/a")),
               "status": "UNCERTAIN" if reasons else "OK", "reasons": reasons, "notes": notes, "source_url": url, "source_id": sid, "retrieved_at": at}
        self.s.quotes[insurer] = row
        self.s.ledger.append({k: row[k] for k in ("app_id", "insurer", "premium_annual", "premium_basis", "premium_raw", "source_id", "source_url", "retrieved_at")})
        return {"insurer": insurer, "annual_premium": d["premium_annual"], "basis": d["premium_basis"], "claim": row["claim_label"], "status": row["status"], "reasons": reasons}

    def compare_quotes(self):
        rows = [r for r in self.s.quotes.values() if r["status"] != "FAILED"]
        ok = sorted([r for r in rows if r["status"] == "OK"], key=lambda r: r["premium_annual"])
        indicative = sorted(rows, key=lambda r: r["premium_annual"])
        unans = self.s.profile.unanswered()
        d = {"rule": "lowest annual premium among quotes with complete health data; ties -> higher claim ratio",
             "recommended": ok[0]["insurer"] if ok and not unans else None,
             "draft_target": (indicative[0]["insurer"] if indicative else None),
             "evidence": {r["insurer"]: r["premium_annual"] for r in rows}, "reasons": []}
        if unans: d["reasons"].append(f"UNCERTAIN: health answers missing {unans}; recommendation withheld, nothing invented")
        if not rows: d["reasons"].append("no usable quotes")
        self.s.decision = d; return d

    def propose_form(self, insurer):
        p = self.s.profile; fields = {"name": p.name, "age": p.age, "cover": p.cover, "term": p.term, **p.answers()}
        idem = digest([p.app_id, insurer, fields], 12)
        prop = {"insurer": insurer, "app_id": p.app_id, "destination": f"https://mock.insurers.test/{insurer}/quote-form",
                "fields": fields, "idempotency_key": idem}
        self.s.proposals[insurer] = prop; return {"proposed": True, "pending_fields": p.unanswered(), "idempotency_key": idem}

    def submit_form(self, insurer):
        prop = self.s.proposals.get(insurer)
        if not prop: return {"error": "no proposal; call propose_form first"}
        if prop["idempotency_key"] in self.s.submissions: return {"status": "already_submitted", **self.s.submissions[prop["idempotency_key"]]}
        approval = self.gate.store.get(prop["idempotency_key"]) or self.gate.request(prop)   # HARD GATE
        if not approval["approved"]: self.s.denied = True; return {"status": "denied_by_human"}
        if approval["approved_payload_hash"] != digest(prop["fields"]): return {"error": "payload changed after approval"}
        res = self.portal.submit(insurer, prop["idempotency_key"], prop["fields"])           # may raise TransientError
        self.s.submissions[prop["idempotency_key"]] = {"insurer": insurer, **res, "approval": approval}
        return {"status": "submitted", **res}

    def verify_submission(self, insurer):
        prop = self.s.proposals[insurer]; sub = self.s.submissions.get(prop["idempotency_key"])
        if not sub: return {"status": "PENDING", "detail": "not submitted"}
        actual = self.portal.read(sub["form_id"])["fields"]
        diff = {k: {"intended": v, "actual": actual.get(k)} for k, v in prop["fields"].items() if actual.get(k) != v}
        v = {"status": "VERIFIED" if not diff else "FAILED", "diff": diff, "pending_human_fields": [k for k, v in prop["fields"].items() if v is None]}
        self.s.verification[insurer] = v; return v

    def render_table(self):
        self.s.rendered = True; out = render(self.s); print(out); return {"rendered": True}

    SCHEMAS = {
        "validate_inputs": ("Validate user profile before anything else.", {}),
        "fetch_quote": ("Fetch + extract an insurer quote page (annual premium, claim ratio, exclusions, riders).", {"insurer": list(INSURERS)}),
        "compare_quotes": ("Apply explicit decision rules over fetched quotes; saves reasons.", {}),
        "propose_form": ("Build the quote-form payload for human review. Unanswered health fields stay null.", {"insurer": list(INSURERS)}),
        "submit_form": ("Fill the quote form. Internally blocked until a human approves.", {"insurer": list(INSURERS)}),
        "verify_submission": ("Read back the form and compare actual vs intended.", {"insurer": list(INSURERS)}),
        "render_table": ("Print the final comparison table.", {}),
    }
    def schemas(self):
        return [{"name": n, "description": d, "input_schema": {"type": "object", "properties": {k: {"type": "string", "enum": v} for k, v in a.items()}, "required": list(a)}}
                for n, (d, a) in self.SCHEMAS.items()]

# ---------- 6. Result view --------------------------------------------------
def _tbl(h, rows):
    w = [max(len(str(x)) for x in c) for c in zip(h, *rows)]
    ln = lambda l, m, r: l + m.join("─" * (x + 2) for x in w) + r
    f = lambda r: "│" + "│".join(f" {str(x):<{n}} " for x, n in zip(r, w)) + "│"
    return "\n".join([ln("┌", "┬", "┐"), f(h), ln("├", "┼", "┤"), *map(f, rows), ln("└", "┴", "┘")])

def render(s):
    p = s.profile; rows = sorted(s.quotes.values(), key=lambda r: r.get("premium_annual", 1e18))
    t = _tbl(["Insurer", "Annual premium", "Basis", "Claim ratio", "Status"],
             [[r["insurer"], f"₹{r['premium_annual']:,}" if "premium_annual" in r else "—", r.get("premium_basis", "—"), r.get("claim_label", "—"),
               r["status"] + (" ⚠" if r["status"] != "OK" else "")] for r in rows])
    e = _tbl(["Insurer", "Source ID", "Retrieved (UTC)", "Source URL"], [[r["insurer"], r["source_id"], r.get("retrieved_at", "—"), r["source_url"]] for r in rows])
    out = [f"\n═══ COMPARISON — {p.app_id} · age {p.age} · cover ₹{p.cover:,} · {p.term}y ═══", t, "\nExclusions & riders:"]
    for r in rows: out.append(f"  • {r['insurer']}: excl={'; '.join(r.get('exclusions', [])) or '—'} | riders={'; '.join(r.get('riders', [])) or '—'}")
    out += ["\nEvidence trail:", e]
    d = s.decision or {}; out.append(f"\nDecision: {d.get('recommended') or 'NO RECOMMENDATION'}  (rule: {d.get('rule')})")
    for r in d.get("reasons", []): out.append(f"  ⚠ {r}")
    for ins, v in s.verification.items(): out.append(f"Verification[{ins}]: {v['status']}" + (f" · pending human fields: {v['pending_human_fields']}" if v["pending_human_fields"] else ""))
    return "\n".join(out)

# ---------- Planners & Agent loop -------------------------------------------
class RulePlanner:
    """Offline planner: picks the next tool from state and says why (no network)."""
    def next(self, s):
        if s.halted: return None
        if not s.validated: return "validate_inputs", {}, "Never spend tool calls on unvalidated input."
        for i in INSURERS:
            if i not in s.quotes: return "fetch_quote", {"insurer": i}, f"Need evidence from {i}."
        if not s.decision: return "compare_quotes", {}, "All sources fetched; apply decision rules."
        t = s.decision["draft_target"]
        if not t or s.denied: return ("render_table", {}, "Finish.") if not s.rendered else None
        if t not in s.proposals: return "propose_form", {"insurer": t}, f"Prepare form for {t} for human review."
        if s.proposals[t]["idempotency_key"] not in s.submissions: return "submit_form", {"insurer": t}, "Request human approval, then fill form."
        if t not in s.verification: return "verify_submission", {"insurer": t}, "Check actual state vs intended."
        if not s.rendered: return "render_table", {}, "Present the evidence."
        return None

class LLMPlanner:
    """Gemini tool-calling planner. Needs GEMINI_API_KEY. Safety is enforced inside tools, not the prompt."""
    SYSTEM = ("You are a term-insurance quote agent. Call tools in order: validate_inputs, fetch_quote for each insurer, compare_quotes, "
              "propose_form, submit_form, verify_submission, render_table. NEVER invent health answers or premiums; unanswered = uncertain.")
    def run(self, agent):
        # Load the project-root .env without overriding variables already set in the shell.
        env_file = Path(__file__).resolve().parent.parent / ".env"
        if env_file.is_file():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                name, value = line.split("=", 1)
                name, value = name.strip(), value.strip().strip("\"'")
                if name and name not in os.environ:
                    os.environ[name] = value
        if not os.getenv("GEMINI_API_KEY"):
            raise RuntimeError("Set GEMINI_API_KEY to use the Gemini planner.")
        from google import genai
        from google.genai import types

        c = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
        declarations = [types.FunctionDeclaration(name=s["name"], description=s["description"], parameters=s["input_schema"])
                        for s in agent.tb.schemas()]
        config = types.GenerateContentConfig(
            system_instruction=self.SYSTEM,
            tools=[types.Tool(function_declarations=declarations)],
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )
        contents = [types.Content(role="user", parts=[types.Part(text=f"Quote for profile: {agent.s.profile}")])]
        for _ in range(30):
            r = c.models.generate_content(model=os.getenv("ZIRO_MODEL", "gemini-2.5-flash"), contents=contents, config=config)
            if not r.candidates:
                return
            response = r.candidates[0].content
            contents.append(response)
            uses = [p.function_call for p in response.parts if p.function_call]
            for part in response.parts:
                if part.text: say(agent.s, "THINK", part.text)
            if not uses: return
            results = []
            for call in uses:
                result = agent.call(call.name, dict(call.args or {}), "Gemini-chosen")
                results.append(types.Part(function_response=types.FunctionResponse(name=call.name, response=result)))
            contents.append(types.Content(role="user", parts=results))

class Agent:
    def __init__(self, profile, gate, planner=None, portal=None, cache=None):
        self.s = Session(profile); self.tb = ToolBox(self.s, gate, portal, cache); self.planner = planner or RulePlanner()

    def call(self, tool, args, reason):
        say(self.s, "THINK", reason); say(self.s, "ACT", f"{tool}({args})")
        for attempt in range(1, 4):                      # graceful failure: retry transient errors, same idempotency key
            try: obs = getattr(self.tb, tool)(**args); break
            except TransientError as e: say(self.s, "WARN", f"{tool} transient failure ({e}); retry {attempt}/3"); obs = {"error": str(e)}
            except ToolError as e: say(self.s, "WARN", f"{tool} failed: {e}"); obs = {"error": str(e)}; break
        say(self.s, "OBS", json.dumps(obs, default=str)[:230]); return obs

    def run(self):
        if isinstance(self.planner, LLMPlanner): self.planner.run(self)
        else:
            for _ in range(40):
                step = self.planner.next(self.s)
                if not step: break
                self.call(*step)
        if self.s.halted: say(self.s, "WARN", "Run halted: input validation failed. No tools beyond validation were executed.")
        return self.s
