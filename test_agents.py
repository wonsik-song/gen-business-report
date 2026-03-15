import os
from dotenv import load_dotenv

# Load .env
load_dotenv()

from agents.planner import PlannerAgent
from agents.evaluator import EvaluatorAgent

def run_tests():
    # Note: Requires OPENAI_API_KEY in .env for actual execution.
    # Currently just initializing to ensure syntax and imports are correct.
    print("Testing Imports and Initialization...")
    
    try:
        planner = PlannerAgent(model_name="gpt-4o-mini", temperature=0.7)
        print("✅ Planner initialized.")
        
        evaluator = EvaluatorAgent(model_name="gpt-4o-mini", temperature=0.2)
        print("✅ Evaluator initialized.")
        
        print("\nAll agent modules imported and initialized successfully!")
    except Exception as e:
        print(f"❌ Initialization failed: {e}")

if __name__ == "__main__":
    run_tests()
