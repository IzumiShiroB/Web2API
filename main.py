import asyncio
import argparse
import uvicorn
import os

from api_server import app, platform_instances, get_or_create_platform
from logger import logger
from platforms import PLATFORMS


def main():
    parser = argparse.ArgumentParser(description="AI Web Proxy API Server")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="Host to bind")
    parser.add_argument("--port", type=int, default=23456, help="Port to bind")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload")
    parser.add_argument("--platform", type=str, default="", help="Platform to use (deepseek, minimax)")
    args = parser.parse_args()
    
    if args.platform:
        os.environ["SELECTED_PLATFORM"] = args.platform
    
    platforms_list = "\n".join([f"║    - {name.capitalize()}" for name in PLATFORMS.keys()])
    
    print(f"""
╔════════════════════════════════════════════════════════════╗
║              AI Web Proxy API Server                       ║
╠════════════════════════════════════════════════════════════╣
║  API Endpoint: http://{args.host}:{args.port}/chat/completions    ║
║  Models List:  http://{args.host}:{args.port}/models              ║
║  Health Check: http://{args.host}:{args.port}/health              ║
╠════════════════════════════════════════════════════════════╣
║  Supported Platforms:                                      ║
{platforms_list}
╠════════════════════════════════════════════════════════════╣
║  OpenAI Compatible API                                     ║
║  Example Request:                                          ║
║    POST /chat/completions                                  ║
║    {{"model": "local", "messages": [...]}}              ║
╚════════════════════════════════════════════════════════════╝
    """)
    
    uvicorn.run(
        "api_server:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )


if __name__ == "__main__":
    main()
