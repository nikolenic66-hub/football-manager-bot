"""Run the Mini App HTTP server; the bot is managed separately by Supervisor."""
import uvicorn


if __name__ == "__main__":
    uvicorn.run("app.server:app", host="0.0.0.0", port=8000, log_level="info")
