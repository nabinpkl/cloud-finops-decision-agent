from __future__ import annotations

from app_config.model_config import model_config


def test_model_config_exposes_main_request_shape():
    main = model_config.main

    assert main.model_env == "MODEL_NAME"
    # No `reasoning` on the main model: DeepSeek tool-calling models emit tool
    # calls as DSML markup inside the reasoning channel when reasoning.effort is
    # set, which OpenRouter never parses back into OpenAI tool_calls.
    assert main.request.extra_body.as_request_body() == {
        "provider": {"require_parameters": True},
    }
    assert main.request.extra_body.reasoning is None
    assert main.request.use_responses_api is False
    assert main.request.disable_streaming is True
    assert main.request.max_tokens == 4000
    assert main.structured_output.schema_name == "AnswerPlan"
    assert main.structured_output.strict is True


def test_model_config_exposes_binary_judge_shape():
    judge = model_config.judge

    assert judge.model_env == "JUDGE_MODEL_NAME"
    assert judge.request.provider.require_parameters is True
    assert judge.request.reasoning.effort == "low"
    assert judge.structured_output.schema_name == "GuardDecision"
    assert judge.structured_output.actions == ["allow", "block"]
    assert judge.structured_output.strict is True
