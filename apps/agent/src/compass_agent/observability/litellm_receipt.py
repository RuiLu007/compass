"""Request-bound SDK scalar receipts; no global callbacks or output capture."""


def complete_usage(response):
    usage = getattr(response, "usage", None)
    if usage is None:
        return False
    value = usage if isinstance(usage, dict) else usage.model_dump()
    if any(
        type(value.get(k)) is not int or value[k] < 0
        for k in ("prompt_tokens", "completion_tokens")
    ):
        return False
    # An explicitly incomplete cache category cannot support a complete estimate.
    for key in ("prompt_tokens_details", "completion_tokens_details"):
        details = value.get(key)
        if isinstance(details, dict):
            for name, count in details.items():
                if "cache" in name and (type(count) is not int or count < 0):
                    return False
    return True


def callbacks(observation):
    async def success(kwargs, response_obj, start_time, end_time):
        try:
            if complete_usage(response_obj):
                observation.accept_cost(kwargs.get("response_cost"))
        except Exception:
            return

    async def failure(kwargs, response_obj, start_time, end_time):
        return

    return {"success_callback": [success], "failure_callback": [failure]}


def estimate(observation, sdk, response, model):
    if observation.cost is not None:
        return
    try:
        if complete_usage(response):
            observation.accept_cost(sdk.completion_cost(completion_response=response, model=model))
    except Exception:
        # Missing prices and unsupported usage categories remain unknown.
        return
