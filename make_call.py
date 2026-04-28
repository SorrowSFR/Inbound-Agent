import argparse
import asyncio

from outbound_calls import dispatch_outbound_call


async def main():
    parser = argparse.ArgumentParser(description="Make an outbound call via LiveKit Agent.")
    parser.add_argument("--to", required=True, help="The phone number to call (e.g., +91...)")
    parser.add_argument("--name", default="", help="Optional caller/contact name metadata")
    args = parser.parse_args()

    print(f"Initiating call to {args.to.strip()}...")

    try:
        result = await dispatch_outbound_call(
            args.to,
            caller_name=args.name.strip(),
        )
        print("\nCall dispatched successfully.")
        print(f"Dispatch ID: {result['dispatch_id']}")
        print(f"Session Room: {result['room']}")
        print(f"SIP Trunk: {result['sip_trunk_id']}")
        print("-" * 40)
        print("The agent is now joining the room and will dial the number.")
        print("Check your agent terminal for logs.")
    except Exception as exc:
        print(f"\nError dispatching call: {exc}")


if __name__ == "__main__":
    asyncio.run(main())
