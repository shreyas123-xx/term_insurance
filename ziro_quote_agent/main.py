"""CLI: python -m ziro_quote_agent.main --age 30 --cover 10000000 --term 30 [--smoker yes|no|?] [--planner llm]"""
import argparse
from .agent import Agent, LLMPlanner
from .core import ApprovalGate, FileCache, HEALTH_QS, Profile

def main():
    a = argparse.ArgumentParser(); a.add_argument("--name", default="Asha Rao")
    a.add_argument("--age", type=int, default=30); a.add_argument("--cover", type=int, default=10_000_000); a.add_argument("--term", type=int, default=30)
    a.add_argument("--planner", choices=["rule", "llm"], default="rule"); a.add_argument("--approval", choices=["interactive", "approve", "deny"], default="interactive")
    for q in HEALTH_QS: a.add_argument(f"--{q.replace('_','-')}", choices=["yes", "no", "?"], default="no")
    x = a.parse_args(); m = {"yes": True, "no": False, "?": None}
    prof = Profile(x.name, x.age, x.cover, x.term, {q: m[getattr(x, q)] for q in HEALTH_QS})
    Agent(prof, ApprovalGate(x.approval), LLMPlanner() if x.planner == "llm" else None, cache=FileCache(".cache")).run()
if __name__ == "__main__": main()
