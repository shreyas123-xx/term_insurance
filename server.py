"""Local API for the frontend and the complete mock quote-agent workflow."""
import json, uuid, os
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from ziro_quote_agent.agent import Agent
from ziro_quote_agent.core import ApprovalGate, FileCache, Profile
ROOT=Path(__file__).resolve().parent; SESSIONS={}; HEALTH=("smoker","diabetes","hypertension","heart_condition","prior_surgery")
def profile_from(b): return Profile(b["name"],int(b["age"]),int(b["cover"]),int(b["term"]),{k:b.get("health",{}).get(k) for k in HEALTH})
def view(s): return {"app_id":s.profile.app_id,"quotes":s.quotes,"decision":s.decision,"proposals":s.proposals,"submissions":s.submissions,"verification":s.verification,"rendered":s.rendered,"halted":s.halted,"log":s.log}
class Handler(SimpleHTTPRequestHandler):
    def __init__(self,*a,**kw): super().__init__(*a,directory=str(ROOT/"frontend"),**kw)
    def send_json(self,v,status=200):
        p=json.dumps(v,default=str).encode();self.send_response(status);self.send_header("Content-Type","application/json");self.send_header("Content-Length",str(len(p)));self.end_headers();self.wfile.write(p)
    def do_POST(self):
        try:
            b=json.loads(self.rfile.read(int(self.headers.get("Content-Length",0))))
            if self.path=="/api/compare":
                p=profile_from(b);a=Agent(p,ApprovalGate("approve"),cache=FileCache(ROOT/".cache"));s=a.run();self.send_json({"app_id":p.app_id,"quotes":s.quotes,"decision":s.decision,"mock_survey":True});return
            if self.path=="/api/session":
                p=profile_from(b);a=Agent(p,ApprovalGate("approve"),cache=FileCache(ROOT/".cache"));sid=uuid.uuid4().hex;SESSIONS[sid]=a;self.send_json({"session_id":sid,"app_id":p.app_id});return
            sid,action=self.path.removeprefix("/api/").split("/",1);a=SESSIONS[sid]
            actions={"validate":("validate_inputs",{}),"fetch":("fetch_quote",b),"compare":("compare_quotes",{}),"propose":("propose_form",b),"submit":("submit_form",b),"verify":("verify_submission",b),"render":("render_table",{})}
            if action not in actions: raise KeyError(action)
            tool,args=actions[action];result=a.call(tool,args,"HTTP API request");self.send_json({"result":result,"state":view(a.s)})
        except (KeyError,ValueError,json.JSONDecodeError) as e:self.send_json({"error":str(e)},400)
        except Exception as e:self.send_json({"error":str(e)},500)
if __name__=="__main__": ThreadingHTTPServer(("127.0.0.1",int(os.getenv("ZIRO_PORT","8000"))),Handler).serve_forever()
