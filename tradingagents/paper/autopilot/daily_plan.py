"""Subscription-only routing. Paid-credit permission is controlled in ChatGPT.

The operator confirms that other apps cannot spend credits. This module pins that
confirmation to the saved sign-in and rejects API-key routes; it cannot query or
change the account's remote billing settings. Never re-enable that permission
while this schedule is armed.
"""

from . import daily_support as s


def account_binding() -> str:
    from tradingagents.llm_clients import chatgpt_auth

    data = chatgpt_auth._read()
    account, tokens = data.get("account") or {}, data.get("tokens") or {}
    if (
        "chatgpt.tokens.use.direct" not in tokens.get("scopes", [])
        or not all(isinstance(account.get(k), str) and account[k] for k in ("client_id", "subject"))
        or not tokens.get("access_token")
    ):
        raise ValueError("Saved ChatGPT sign-in with plan permission required")
    return s.sha(s.canonical({k: account[k] for k in ("client_id", "subject")}))


def confirm_config(config: dict) -> dict:
    result = dict(
        config,
        model_billing_mode="chatgpt_plan_only",
        chatgpt_credits_disabled_confirmed_at=s.utcnow().isoformat(),
        chatgpt_account_sha256=account_binding(),
    )
    return s.validate_config(result)


def check_binding(config: dict):
    s.validate_config(config)
    if config["model_billing_mode"] != "chatgpt_plan_only":
        raise ValueError("Explicit ChatGPT plan-only mode required")
    if account_binding() != config["chatgpt_account_sha256"]:
        raise ValueError("ChatGPT sign-in changed; reconfirm paid credits are disabled")


def check_route(config: dict, model_config: dict):
    from tradingagents.llm_clients.factory import tier_provider

    check_binding(config)
    for tier in ("deep", "quick"):
        if tier_provider(model_config, tier) != "chatgpt":
            raise ValueError("Plan-only automation refuses API-key providers and fallback")
    for key in ("backend_url", "deep_think_backend_url", "quick_think_backend_url"):
        if model_config.get(key) not in (None, "", "https://api.openai.com/v1"):
            raise ValueError("Plan-only automation requires the official OpenAI endpoint")
