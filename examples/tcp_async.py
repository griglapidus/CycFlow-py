"""
Asynchronous TCP streaming via asyncio.
"""
import asyncio
import cycflow


async def main() -> None:
    total = 0
    async for batch in cycflow.stream(
        host="127.0.0.1",
        port=5000,
        buffer_name="SensorStream",
        buffer_capacity=20_000,
        batch_capacity=500,
    ):
        total += len(batch)
        v = batch["Voltage"]
        print(f"got={len(batch):4d}  total={total:7d}  "
              f"V={v.mean():7.3f} ± {v.std():5.3f}")
        if total > 10_000:
            break


if __name__ == "__main__":
    asyncio.run(main())
