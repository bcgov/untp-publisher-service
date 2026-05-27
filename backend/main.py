import uvicorn
import asyncio
from app.plugins import TractionController
from config import settings

if __name__ == "__main__":
    asyncio.run(TractionController().provision())
    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=8000,
        # reload=True,
        workers=4,
    )
