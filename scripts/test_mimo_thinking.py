#!/usr/bin/env python3
"""Independent test script for Xiaomi MiMo (mimo-v2.5-pro) thoughts and function calling.

Usage:
    uv run python scripts/test_mimo_thinking.py
    uv run python scripts/test_mimo_thinking.py --scenario tools
    uv run python scripts/test_mimo_thinking.py --scenario stream
    uv run python scripts/test_mimo_thinking.py --scenario multi-turn
    uv run python scripts/test_mimo_thinking.py --dump-raw
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI

# Ensure UTF-8 output encoding on Windows consoles
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def get_client() -> tuple[OpenAI, str]:
    load_dotenv()
    api_key = os.getenv("MIMO_API_KEY")
    if not api_key:
        print("[ERROR] MIMO_API_KEY is not set in environment or .env file.")
        sys.exit(1)

    base_url = os.getenv("MIMO_BASE_URL", "https://token-plan-ams.xiaomimimo.com/v1")
    model = os.getenv("MIMO_MODEL", "mimo-v2.5-pro")

    print("=" * 80)
    print(f" MiMo Client Config: BaseURL={base_url} | Model={model}")
    print("=" * 80)

    client = OpenAI(
        api_key=api_key,
        base_url=base_url,
    )
    return client, model


# Sample tool definitions
SAMPLE_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_current_weather",
            "description": "Get the current weather for a given location",
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {
                        "type": "string",
                        "description": (
                            "The city and state/country, e.g. San Francisco, CA or Hanoi, Vietnam"
                        ),
                    },
                    "unit": {
                        "type": "string",
                        "enum": ["celsius", "fahrenheit"],
                        "description": "Temperature unit",
                    },
                },
                "required": ["location"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calculate_flight_budget",
            "description": (
                "Calculate travel cost based on origin, destination and number of passengers"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "origin": {"type": "string"},
                    "destination": {"type": "string"},
                    "passengers": {"type": "integer"},
                    "class_tier": {"type": "string", "enum": ["economy", "business"]},
                },
                "required": ["origin", "destination", "passengers"],
            },
        },
    },
]


def inspect_message_fields(message: Any, raw_response: Any = None) -> None:
    """Inspect all potential thought/reasoning attributes on an OpenAI message object."""
    print("\n--- [Inspecting Message Object] ---")

    # 1. Standard attributes
    role = getattr(message, "role", None)
    content = getattr(message, "content", None)
    tool_calls = getattr(message, "tool_calls", None)

    print(f"• role: {role}")
    print(f"• content (len={len(content) if content else 0}):\n  {content!r}")

    # 2. Reasoning / Thinking fields (OpenAI / DeepSeek / MiMo standards)
    reasoning_content = getattr(message, "reasoning_content", None)
    reasoning = getattr(message, "reasoning", None)
    thought = getattr(message, "thought", None)
    thoughts = getattr(message, "thoughts", None)

    # Check extra attributes in dict representation
    extra_fields: dict[str, Any] = {}
    if hasattr(message, "model_dump"):
        dump = message.model_dump()
        for k, v in dump.items():
            if k not in {"role", "content", "tool_calls", "function_call", "refusal"}:
                extra_fields[k] = v

    print(f"• reasoning_content: {reasoning_content!r}")
    if reasoning is not None:
        print(f"• reasoning: {reasoning!r}")
    if thought is not None:
        print(f"• thought: {thought!r}")
    if thoughts is not None:
        print(f"• thoughts: {thoughts!r}")
    if extra_fields:
        extra_json = json.dumps(extra_fields, ensure_ascii=False, indent=2)
        print(f"• other extra message fields: {extra_json}")

    # 3. Content-based thought tags inspection (<think>, <thought>, ```thought)
    if content:
        for tag in ["<think>", "<thought>", "<reasoning>", "[THOUGHT]", "```thought"]:
            if tag in content:
                print(f"  [!] Found inline thought tag '{tag}' inside content!")

    # 4. Tool calls inspection
    if tool_calls:
        print(f"• tool_calls ({len(tool_calls)} call(s)):")
        for i, tc in enumerate(tool_calls):
            fn = tc.function
            print(f"   [{i+1}] ID: {tc.id} | Function: {fn.name} | Arguments: {fn.arguments}")
    else:
        print("• tool_calls: None")


def test_standard_completion_thought(client: OpenAI, model: str, dump_raw: bool = False) -> None:
    """Test 1: Standard complex reasoning prompt to check if thought/reasoning is emitted."""
    print("\n" + "#" * 80)
    print(" TEST 1: Standard Chat Completion (Complex Logic / Math Reasoning)")
    print("#" * 80)

    prompt = (
        "Solve this step-by-step: A farmer has 17 sheep. All but 9 run away. "
        "Then he buys twice the number of remaining sheep. How many sheep does he have now? "
        "Think carefully and show your reasoning."
    )
    print(f"Prompt: {prompt}")

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "You are a helpful and deeply analytical assistant."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
        )

        if dump_raw:
            print("\n[RAW RESPONSE JSON]:")
            print(response.model_dump_json(indent=2))

        msg = response.choices[0].message
        inspect_message_fields(msg, response)

        usage = response.usage
        if usage:
            print(
                f"\n• Usage: prompt_tokens={usage.prompt_tokens}, "
                f"completion_tokens={usage.completion_tokens}, total={usage.total_tokens}"
            )
            if hasattr(usage, "completion_tokens_details"):
                print(f"• completion_tokens_details: {usage.completion_tokens_details}")

    except Exception as exc:
        print(f"[ERROR] Test 1 failed: {exc}")


def test_function_call_thought(client: OpenAI, model: str, dump_raw: bool = False) -> None:
    """Test 2: Function Calling with Tools - inspect if thought is returned alongside tool_calls."""
    print("\n" + "#" * 80)
    print(" TEST 2: Function Calling with Tools (Inspect Thought with Tool Invocation)")
    print("#" * 80)

    user_query = (
        "I need to plan a trip. First check the weather in Tokyo, Japan, "
        "and calculate flight budget for 3 passengers from New York to Tokyo in business class."
    )
    print(f"User Query: {user_query}")

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are an intelligent travel coordinator. "
                        "Use the provided tools to answer user requests."
                    ),
                },
                {"role": "user", "content": user_query},
            ],
            tools=SAMPLE_TOOLS,  # type: ignore[arg-type]
            tool_choice="auto",
            temperature=0.0,
        )

        if dump_raw:
            print("\n[RAW RESPONSE JSON]:")
            print(response.model_dump_json(indent=2))

        msg = response.choices[0].message
        inspect_message_fields(msg, response)

        usage = response.usage
        if usage:
            print(
                f"\n• Usage: prompt_tokens={usage.prompt_tokens}, "
                f"completion_tokens={usage.completion_tokens}, total={usage.total_tokens}"
            )
            if hasattr(usage, "completion_tokens_details"):
                print(f"• completion_tokens_details: {usage.completion_tokens_details}")

    except Exception as exc:
        print(f"[ERROR] Test 2 failed: {exc}")


def test_streaming_function_call_thought(client: OpenAI, model: str) -> None:
    """Test 3: Streaming Function Calling - inspect delta chunks for reasoning_content."""
    print("\n" + "#" * 80)
    print(" TEST 3: Streaming Function Calling (Inspect Chunk Deltas for Thought/Reasoning)")
    print("#" * 80)

    user_query = (
        "What is the weather in Paris, France right now in celsius? Please think step by step."
    )
    print(f"User Query: {user_query}")

    try:
        stream = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "user", "content": user_query},
            ],
            tools=SAMPLE_TOOLS,  # type: ignore[arg-type]
            tool_choice="auto",
            temperature=0.0,
            stream=True,
        )

        collected_content: list[str] = []
        collected_reasoning: list[str] = []
        tool_call_chunks: list[Any] = []
        chunk_count = 0

        print("\nStreaming chunk inspection:")
        for chunk in stream:
            chunk_count += 1
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta

            # Inspect delta attributes
            r_content = getattr(delta, "reasoning_content", None)
            if r_content:
                collected_reasoning.append(r_content)
                print(f"[CHUNK {chunk_count}] delta.reasoning_content: {r_content!r}")

            r_thought = getattr(delta, "thought", None)
            if r_thought:
                print(f"[CHUNK {chunk_count}] delta.thought: {r_thought!r}")

            content = getattr(delta, "content", None)
            if content:
                collected_content.append(content)
                print(f"[CHUNK {chunk_count}] delta.content: {content!r}")

            t_calls = getattr(delta, "tool_calls", None)
            if t_calls:
                tool_call_chunks.append(t_calls)
                for tc in t_calls:
                    fn = getattr(tc, "function", None)
                    print(f"[CHUNK {chunk_count}] delta.tool_calls -> idx={tc.index}, fn={fn}")

        print("\n--- Summary of Streamed Tokens ---")
        print(f"Total chunks: {chunk_count}")
        print(f"Total collected reasoning_content: {''.join(collected_reasoning)!r}")
        print(f"Total collected content: {''.join(collected_content)!r}")
        print(f"Total tool_call delta events: {len(tool_call_chunks)}")

    except Exception as exc:
        print(f"[ERROR] Test 3 failed: {exc}")


def test_multi_turn_tool_execution(client: OpenAI, model: str, dump_raw: bool = False) -> None:
    """Test 4: Multi-turn Tool Call -> Tool Result -> Final Completion with Thought inspection."""
    print("\n" + "#" * 80)
    print(" TEST 4: Multi-turn Tool Calling Execution Loop")
    print("#" * 80)

    messages: list[dict[str, Any]] = [
        {
            "role": "system",
            "content": (
                "You are a helpful travel assistant. Always reason carefully before answering."
            ),
        },
        {
            "role": "user",
            "content": "Check the current weather in Hanoi and advise if I need an umbrella.",
        },
    ]

    print("Step 1: User asks query...")
    try:
        response1 = client.chat.completions.create(
            model=model,
            messages=messages,  # type: ignore[arg-type]
            tools=SAMPLE_TOOLS,  # type: ignore[arg-type]
            tool_choice="auto",
            temperature=0.0,
        )

        msg1 = response1.choices[0].message
        print("\nTurn 1 Assistant Response:")
        inspect_message_fields(msg1, response1)

        if not msg1.tool_calls:
            print("No tool calls generated in turn 1.")
            return

        # Append assistant message
        messages.append(msg1.model_dump())

        # Simulate executing the tool
        for tc in msg1.tool_calls:
            tool_id = tc.id
            fn_name = tc.function.name
            args = json.loads(tc.function.arguments)
            print(f"\nExecuting tool {fn_name}({args})...")

            # Fake tool output
            tool_output = {
                "location": args.get("location", "Hanoi"),
                "temperature": 28,
                "unit": "celsius",
                "condition": "Heavy Rain and Thunderstorms",
                "precipitation_probability": "90%",
            }

            messages.append({
                "role": "tool",
                "tool_call_id": tool_id,
                "name": fn_name,
                "content": json.dumps(tool_output),
            })

        print("\nStep 2: Sending tool execution output back to model...")
        response2 = client.chat.completions.create(
            model=model,
            messages=messages,  # type: ignore[arg-type]
            tools=SAMPLE_TOOLS,  # type: ignore[arg-type]
            temperature=0.0,
        )

        if dump_raw:
            print("\n[RAW TURN 2 RESPONSE JSON]:")
            print(response2.model_dump_json(indent=2))

        msg2 = response2.choices[0].message
        print("\nTurn 2 Final Assistant Response:")
        inspect_message_fields(msg2, response2)

    except Exception as exc:
        print(f"[ERROR] Test 4 failed: {exc}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Test MiMo-v2.5-pro OpenAI function calling and internal thoughts"
    )
    parser.add_argument(
        "--scenario",
        choices=["all", "standard", "tools", "stream", "multi-turn"],
        default="all",
        help="Which test scenario to run (default: all)",
    )
    parser.add_argument(
        "--dump-raw",
        action="store_true",
        help="Print full raw JSON response from API",
    )
    args = parser.parse_args()

    client, model = get_client()

    if args.scenario in ("all", "standard"):
        test_standard_completion_thought(client, model, dump_raw=args.dump_raw)

    if args.scenario in ("all", "tools"):
        test_function_call_thought(client, model, dump_raw=args.dump_raw)

    if args.scenario in ("all", "stream"):
        test_streaming_function_call_thought(client, model)

    if args.scenario in ("all", "multi-turn"):
        test_multi_turn_tool_execution(client, model, dump_raw=args.dump_raw)

    print("\n" + "=" * 80)
    print(" All selected MiMo test scenarios completed.")
    print("=" * 80)


if __name__ == "__main__":
    main()
