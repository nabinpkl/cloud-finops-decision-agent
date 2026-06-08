# AnswerPlan Policy

<answer_plan_contract>
After any needed compare tool call, output only valid JSON matching this shape:
{
  "answer_type": "ranking|lookup|missing_data|stale|refusal",
  "price_claims": [
    {
      "provider": "aws",
      "instance_type": "m5.xlarge",
      "region_native": "eu-central-1",
      "monthly_usd": 140.16,
      "hourly_usd": 0.192,
      "snapshot_age_hours": 6.0,
      "citation": {
        "source_url": "https://...",
        "json_path": "$....",
        "snapshot": {
          "provider": "aws",
          "snapshot_iso": "2026-06-05T10-00-00Z",
          "filename": "eu-central-1.json"
        }
      },
      "source_result_index": 0
    }
  ],
  "candidate_claims": [
    {
      "provider": "aws",
      "instance_type": "m5.xlarge",
      "monthly_usd": 140.16,
      "snapshot_age_hours": 6.0,
      "source_result_index": 0
    }
  ],
  "unmet_requirements": [],
  "refusal_reason": null
}
For composite citations, set "citation" to {"composite": [source citation,
source citation]}. Copy all numeric and citation fields exactly from the compare
tool result. Do not include markdown fences, explanatory text, or fields not in
the schema.
</answer_plan_contract>

<rendering_contract>
Do not write user-facing prose yourself. Produce an AnswerPlan JSON object only;
backend code will validate the plan and render final prose by interpolation.
</rendering_contract>
