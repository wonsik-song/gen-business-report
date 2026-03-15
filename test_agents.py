from dotenv import load_dotenv

from agents.planner import PlannerAgent
from agents.evaluator import EvaluatorAgent


# Load .env
load_dotenv()


def run_tests():
    # This test validates import/initialization only.
    # Runtime execution needs GOOGLE_API_KEY/GEMINI_API_KEY and ADK runtime.
    print("Testing LlmAgent-based imports and initialization...")

    try:
        planner = PlannerAgent(model_name="gemini-3.1-pro-preview")
        assert planner is not None
        print("✅ Planner initialized.")

        evaluator = EvaluatorAgent(model_name="gemini-3.1-pro-preview")
        assert evaluator is not None
        print("✅ Evaluator initialized.")

        print("\nAll agent modules imported and initialized successfully!")
    except Exception as e:
        print(f"❌ Initialization failed: {e}")


if __name__ == "__main__":
    run_tests()
