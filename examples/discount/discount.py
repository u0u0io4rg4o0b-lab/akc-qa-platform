"""A tiny real local subject, not a remote service or mock."""
import json
import sys


def discount(cents, percent):
    if cents < 0 or not 0 <= percent <= 100:
        raise ValueError("invalid input")
    return cents * (100 - percent) // 100


if __name__ == "__main__":
    try:
        print(json.dumps({"cents": discount(int(sys.argv[1]), int(sys.argv[2]))}))
    except (ValueError, IndexError):
        print(json.dumps({"error": "invalid input"}))
        sys.exit(2)
