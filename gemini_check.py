"""
Check that your Gemini key works and find a valid model id. One tiny call; no harness code.

    python3 gemini_check.py                         # lists model ids your key can use
    python3 gemini_check.py --model <id>            # also makes ONE test call with that id

Needs GEMINI_API_KEY in your Terminal (set it with `read -s` as before; never paste it in chat).
"""
import argparse
import os
import sys


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=None)
    args = ap.parse_args()
    if not (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")):
        print("GEMINI_API_KEY is not set in this Terminal window.")
        sys.exit(1)
    from google import genai
    from llm_clients import GeminiClient
    client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))
    try:
        ids = []
        for m in client.models.list():
            actions = getattr(m, "supported_actions", None) or []
            if not actions or "generateContent" in actions:
                ids.append((m.name or "").replace("models/", ""))
        ids = sorted(ids)
    except Exception as e:
        print("Could not list models:", repr(e)[:300])
        ids = []
    print("Models your key can use for text generation (gemini*):")
    for i in ids:
        if i.startswith("gemini"):
            print("  ", i)
    if args.model:
        from llm_clients import ChatMessage
        out = GeminiClient(model=args.model).chat([ChatMessage("user", "Reply with exactly: OK")])
        print(f"\nTest call to {args.model}: {out!r}")


if __name__ == "__main__":
    main()
