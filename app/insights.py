import os

from anthropic import Anthropic

from app.metrics import ZipMetrics

MODEL = "claude-sonnet-5"


def build_data_summary(metrics_list: list[ZipMetrics]) -> str:
    lines = [
        "Zip | Neighborhoods | Status | Runs | SMS | %Replies | %Leads | %Warm | %Drip | Deals | Total Profit | Score | Tier"
    ]
    for m in metrics_list:
        neighborhoods = ", ".join(n.name for n in m.zip_code.neighborhoods) or "—"
        lines.append(
            f"{m.zip_code.zip_code} | {neighborhoods} | {m.zip_code.status} | "
            f"{m.run_count} | {m.total_sms} | {m.reply_rate*100:.1f}% | {m.lead_rate*100:.1f}% | "
            f"{m.warm_rate*100:.1f}% | {m.drip_rate*100:.1f}% | {m.deal_count} | "
            f"${m.total_profit:.0f} | {m.score:.0f} | {m.tier}"
        )
    return "\n".join(lines)


def generate_insights(metrics_list: list[ZipMetrics]) -> str:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. Set it as an environment variable to enable insights."
        )

    data_summary = build_data_summary(metrics_list)
    client = Anthropic(api_key=api_key)
    message = client.messages.create(
        model=MODEL,
        max_tokens=1500,
        messages=[
            {
                "role": "user",
                "content": (
                    "You are analyzing SMS marketing campaign data by zip code for a real estate "
                    "wholesale business in Allegheny County. Below is a cumulative performance table "
                    "by zip code. Score and Tier are based on %leads (40% weight), %replies (30%), "
                    "%warm (20%), and %drip (10%) - prioritized in that order since leads are the most "
                    "important signal. Zip codes with under 500 SMS sent are tiered 'Not enough data' "
                    "since the sample is too small to grade reliably:\n\n"
                    f"{data_summary}\n\n"
                    "Write a short, focused summary in English with three headings:\n"
                    "1. Zip codes to continue and follow up with (most profitable)\n"
                    "2. Zip codes to stop / consider blacklisting (not returning on investment)\n"
                    "3. Untried zip codes worth trying\n"
                    "Reference specific numbers to justify each recommendation. Be concise."
                ),
            }
        ],
    )
    return "".join(block.text for block in message.content if block.type == "text")
