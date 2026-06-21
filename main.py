"""
AtlasCare — Entry point.
Starts the FastAPI application via uvicorn.
"""
import uvicorn
from app.core import create_app

app = create_app()

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,   # hot-reload in dev; remove in production
        log_level="info",
    )
