import os

# Keine Hintergrund-Abrufe externer Datenquellen während der Tests
os.environ["QUELLEN"] = ""
os.environ.pop("ANTHROPIC_API_KEY", None)
