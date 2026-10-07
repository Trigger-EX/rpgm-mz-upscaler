"""Shared Qt test helpers (imported only by GUI tests, which skip themselves when PySide6 is missing)."""
from PySide6.QtCore import QEventLoop, QTimer


def wait_for(cond, timeout=60000, step=40):
    """Run the Qt event loop until `cond()` is true or `timeout` ms have passed; returns the final value of `cond()`."""
    loop = QEventLoop()
    t = QTimer(); t.setInterval(step)
    waited = [0]

    def tick():
        waited[0] += step
        if cond() or waited[0] > timeout:
            loop.quit()
    t.timeout.connect(tick); t.start()
    loop.exec(); t.stop()
    return cond()
