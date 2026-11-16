import os

# Keine Hintergrund-Abrufe externer Datenquellen während der Tests
os.environ["QUELLEN"] = ""
os.environ.pop("ANTHROPIC_API_KEY", None)
os.environ["HISTORIE"] = "0"

# Feste Uhrzeit für alle Tests: Die Startdaten (seed.json) und die Testfälle enthalten
# konkrete Termine. Ohne festes Datum würden Tests scheitern, sobald diese Termine in
# der Vergangenheit liegen – etwa beim wöchentlichen Neubau des Images.
import time_machine

_UHR = time_machine.travel("2026-11-04T10:00:00+01:00", tick=True)


def pytest_configure(config):
    _UHR.start()


def pytest_unconfigure(config):
    _UHR.stop()
