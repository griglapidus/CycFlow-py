"""
Пример: живой график из CycFlow TCP-стрима.

Требует: matplotlib. Запуск:
    pip install cyclib[plot]
    python live_plot.py
"""
import asyncio
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation

import cycflow


WINDOW = 2000  # сколько последних точек показывать


async def pump(queue: asyncio.Queue) -> None:
    """Фоновая задача: тянет батчи и складывает в очередь."""
    async for batch in cycflow.stream("127.0.0.1", 5000, "SensorStream"):
        await queue.put(batch)


def main() -> None:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    queue: asyncio.Queue = asyncio.Queue()
    loop.create_task(pump(queue))

    history = np.empty(0, dtype=np.float32)

    fig, ax = plt.subplots()
    (line,) = ax.plot([], [], lw=1)
    ax.set_xlim(0, WINDOW)
    ax.set_ylim(-1, 15)
    ax.set_title("Voltage (live)")

    def update(_frame):
        nonlocal history
        # Сливаем всё, что пришло между кадрами.
        while not queue.empty():
            batch = queue.get_nowait()
            history = np.concatenate([history, batch["Voltage"]])
        if len(history) > WINDOW:
            history = history[-WINDOW:]
        line.set_data(np.arange(len(history)), history)
        return (line,)

    # «Прокачиваем» asyncio между кадрами matplotlib.
    def step_loop(_frame):
        loop.call_soon(loop.stop)
        loop.run_forever()
        return update(_frame)

    _anim = FuncAnimation(fig, step_loop, interval=50, blit=True)
    plt.show()


if __name__ == "__main__":
    main()
