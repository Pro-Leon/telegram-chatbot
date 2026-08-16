"""Main module for running ingestion server."""

import uvicorn

from ingestion.main import app

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
