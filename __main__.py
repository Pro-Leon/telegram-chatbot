"""Entry point for the web dashboard."""

import uvicorn

from chatbotv2.dashboard.app import app

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=1010, log_level="info")
