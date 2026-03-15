from pydantic import BaseModel, Field
from typing import Dict, List, Optional

class CategoryScores(BaseModel):
    logic: int = Field(description="Score for logical flow and consistency (0-100)")
    feasibility: int = Field(description="Score for technical feasibility (0-100)")
    ux_flow: int = Field(description="Score for user experience and interface flow (0-100)")
    business: int = Field(description="Score for business value and market fit (0-100)")

class EvaluationResult(BaseModel):
    status: str = Field(description="Status of the evaluation: SUCCESS, PARTIAL_ERROR, or FAILED")
    total_score: Optional[int] = Field(None, description="Average or total score of all categories")
    category_scores: Optional[CategoryScores] = Field(None, description="Detailed scores for each category")
    summary: str = Field(description="A comprehensive summary of the evaluation")
    strengths: List[str] = Field(description="List of strong points identified in the document")
    missing_points: List[str] = Field(description="List of missing points or areas for improvement")
    raw_text: Optional[str] = Field(None, description="Raw model response in case of fallback")
