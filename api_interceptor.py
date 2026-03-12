"""
API Interceptor - 监听OpenClaw与DeepSeek官方API的通信
作为中间人代理，记录所有请求和响应
"""
import asyncio
import json
import time
from datetime import datetime
from typing import Optional
import aiohttp
from aiohttp import web
import aiofiles
from pathlib import Path


class APILogger:
    """记录所有API通信，包含精确时间戳"""
    
    def __init__(self, log_dir: str = "logs/interceptor"):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.log_file = self.log_dir / f"{self.session_id}.jsonl"
        
    async def log_event(self, event_type: str, direction: str, data: dict):
        """记录API事件"""
        event = {
            "timestamp": datetime.now().isoformat(),
            "unix_time": time.time(),
            "event_type": event_type,
            "direction": direction,
            "data": data
        }
        
        async with aiofiles.open(self.log_file, 'a', encoding='utf-8') as f:
            await f.write(json.dumps(event, ensure_ascii=False) + '\n')
            
    def log_sync(self, event_type: str, direction: str, data: dict):
        """同步记录（用于非异步上下文）"""
        event = {
            "timestamp": datetime.now().isoformat(),
            "unix_time": time.time(),
            "event_type": event_type,
            "direction": direction,
            "data": data
        }
        
        with open(self.log_file, 'a', encoding='utf-8') as f:
            f.write(json.dumps(event, ensure_ascii=False) + '\n')


class APIInterceptor:
    """
    API拦截器 - 监听OpenClaw与DeepSeek官方API的通信
    
    工作流程:
    1. OpenClaw发送请求到拦截器 (端口23456)
    2. 拦截器记录请求并转发到DeepSeek官方API
    3. DeepSeek返回响应
    4. 拦截器记录响应并返回给OpenClaw
    """
    
    def __init__(self, 
                 listen_host: str = "127.0.0.1",
                 listen_port: int = 23456,
                 deepseek_api_key: Optional[str] = None,
                 deepseek_base_url: str = "https://api.deepseek.com"):
        self.listen_host = listen_host
        self.listen_port = listen_port
        self.deepseek_api_key = deepseek_api_key
        self.deepseek_base_url = deepseek_base_url
        self.logger = APILogger()
        self.app = web.Application()
        self.setup_routes()
        
    def setup_routes(self):
        """设置HTTP路由"""
        self.app.router.add_post('/v1/chat/completions', self.handle_chat_completions)
        self.app.router.add_post('/chat/completions', self.handle_chat_completions)
        self.app.router.add_get('/v1/models', self.handle_models)
        self.app.router.add_get('/models', self.handle_models)
        self.app.router.add_get('/health', self.handle_health)
        
    async def handle_health(self, request: web.Request):
        """健康检查"""
        return web.json_response({
            "status": "ok", 
            "interceptor": True,
            "timestamp": datetime.now().isoformat()
        })
        
    async def handle_models(self, request: web.Request):
        """返回可用模型列表"""
        # 转发到DeepSeek API获取真实模型列表
        if self.deepseek_api_key:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        f"{self.deepseek_base_url}/v1/models",
                        headers={"Authorization": f"Bearer {self.deepseek_api_key}"}
                    ) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            return web.json_response(data)
            except:
                pass
                
        # 返回默认模型列表
        models = {
            "object": "list",
            "data": [
                {
                    "id": "deepseek-chat",
                    "object": "model",
                    "created": 1677610602,
                    "owned_by": "deepseek"
                },
                {
                    "id": "deepseek-reasoner",
                    "object": "model",
                    "created": 1677610602,
                    "owned_by": "deepseek"
                }
            ]
        }
        return web.json_response(models)
        
    async def handle_chat_completions(self, request: web.Request):
        """
        处理聊天补全请求 - 核心拦截点
        """
        request_id = f"req_{int(time.time() * 1000)}_{id(request)}"
        start_time = time.time()
        
        # 1. 读取并记录来自OpenClaw的请求
        try:
            body = await request.json()
        except:
            body = {}
            
        headers = dict(request.headers)
        # 移除敏感信息
        safe_headers = {k: v for k, v in headers.items() 
                       if k.lower() not in ['authorization', 'x-api-key', 'cookie']}
        
        await self.logger.log_event(
            "openclaw_request",
            "OpenClaw -> Interceptor",
            {
                "request_id": request_id,
                "timestamp_start": start_time,
                "method": request.method,
                "path": str(request.path),
                "query": str(request.query_string),
                "headers": safe_headers,
                "body": body,
                "messages_count": len(body.get('messages', [])),
                "model": body.get('model'),
                "stream": body.get('stream', False),
                "has_tools": 'tools' in body,
                "tools_count": len(body.get('tools', []))
            }
        )
        
        # 2. 转发到DeepSeek官方API
        if not self.deepseek_api_key:
            await self.logger.log_event(
                "error",
                "Interceptor",
                {"request_id": request_id, "error": "DeepSeek API key not configured"}
            )
            return web.json_response(
                {"error": "DeepSeek API key not configured"},
                status=500
            )
            
        deepseek_headers = {
            "Authorization": f"Bearer {self.deepseek_api_key}",
            "Content-Type": "application/json"
        }
        
        deepseek_url = f"{self.deepseek_base_url}/v1/chat/completions"
        
        try:
            request_to_ds_start = time.time()
            
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    deepseek_url,
                    headers=deepseek_headers,
                    json=body
                ) as response:
                    
                    # 记录发送到DeepSeek的请求
                    await self.logger.log_event(
                        "forward_request",
                        "Interceptor -> DeepSeek",
                        {
                            "request_id": request_id,
                            "url": deepseek_url,
                            "latency_to_ds_ms": (time.time() - request_to_ds_start) * 1000,
                            "body_summary": {
                                "model": body.get('model'),
                                "messages_count": len(body.get('messages', [])),
                                "stream": body.get('stream', False)
                            }
                        }
                    )
                    
                    response_status = response.status
                    response_headers = dict(response.headers)
                    
                    if body.get('stream', False):
                        # 处理流式响应
                        return await self.handle_streaming_response(
                            request_id, response, response_status, response_headers, start_time
                        )
                    else:
                        # 处理非流式响应
                        response_from_ds_start = time.time()
                        response_body = await response.json()
                        
                        # 记录DeepSeek返回的响应
                        await self.logger.log_event(
                            "deepseek_response",
                            "DeepSeek -> Interceptor",
                            {
                                "request_id": request_id,
                                "status": response_status,
                                "latency_from_ds_ms": (time.time() - response_from_ds_start) * 1000,
                                "headers": {k: v for k, v in response_headers.items() 
                                          if k.lower() not in ['authorization']},
                                "body_summary": self._summarize_response(response_body),
                                "full_body": response_body
                            }
                        )
                        
                        # 记录返回给OpenClaw的响应
                        total_latency = (time.time() - start_time) * 1000
                        await self.logger.log_event(
                            "openclaw_response",
                            "Interceptor -> OpenClaw",
                            {
                                "request_id": request_id,
                                "status": response_status,
                                "total_latency_ms": total_latency,
                                "body_summary": self._summarize_response(response_body)
                            }
                        )
                        
                        return web.json_response(
                            response_body,
                            status=response_status,
                            headers=response_headers
                        )
                        
        except Exception as e:
            await self.logger.log_event(
                "error",
                "Interceptor",
                {
                    "request_id": request_id,
                    "error": str(e),
                    "error_type": type(e).__name__,
                    "traceback": str(e.__traceback__)
                }
            )
            return web.json_response(
                {"error": f"API request failed: {str(e)}"},
                status=502
            )
    
    def _summarize_response(self, body: dict) -> dict:
        """提取响应的关键信息"""
        if not isinstance(body, dict):
            return {"error": "Invalid response format"}
            
        summary = {
            "id": body.get('id'),
            "object": body.get('object'),
            "model": body.get('model'),
            "choices_count": len(body.get('choices', [])),
        }
        
        if body.get('choices'):
            choice = body['choices'][0]
            message = choice.get('message', {})
            summary['finish_reason'] = choice.get('finish_reason')
            summary['has_content'] = message.get('content') is not None
            summary['has_tool_calls'] = message.get('tool_calls') is not None
            if message.get('tool_calls'):
                summary['tool_calls_count'] = len(message['tool_calls'])
                summary['tool_names'] = [
                    tc.get('function', {}).get('name') 
                    for tc in message['tool_calls']
                ]
                
        if body.get('usage'):
            summary['usage'] = body['usage']
            
        return summary
            
    async def handle_streaming_response(self, request_id: str, response, status: int, 
                                       headers: dict, start_time: float):
        """处理流式响应"""
        
        async def stream_generator():
            chunks = []
            chunk_count = 0
            
            async for chunk in response.content:
                chunk_time = time.time()
                chunk_text = chunk.decode('utf-8')
                chunks.append(chunk_text)
                chunk_count += 1
                
                # 解析SSE格式
                if chunk_text.startswith('data: '):
                    data_content = chunk_text[6:].strip()
                    if data_content and data_content != '[DONE]':
                        try:
                            data_json = json.loads(data_content)
                            await self.logger.log_event(
                                "stream_chunk",
                                "DeepSeek -> Interceptor",
                                {
                                    "request_id": request_id,
                                    "chunk_number": chunk_count,
                                    "chunk_time": chunk_time,
                                    "data": data_json
                                }
                            )
                        except:
                            pass
                
                yield chunk
                
            # 记录流完成
            total_latency = (time.time() - start_time) * 1000
            await self.logger.log_event(
                "stream_complete",
                "Interceptor -> OpenClaw",
                {
                    "request_id": request_id,
                    "total_chunks": chunk_count,
                    "total_latency_ms": total_latency,
                    "complete_response": ''.join(chunks)
                }
            )
            
        return web.Response(
            body=stream_generator(),
            status=status,
            headers=headers,
            content_type='text/event-stream'
        )
        
    def run(self):
        """启动拦截器服务器"""
        print(f"""
╔══════════════════════════════════════════════════════════════════╗
║                    API Interceptor Server                        ║
╠══════════════════════════════════════════════════════════════════╣
║  监听地址: http://{self.listen_host}:{self.listen_port}                           ║
║  日志目录: {self.logger.log_dir}                              ║
║  会话ID:  {self.logger.session_id}                              ║
╠══════════════════════════════════════════════════════════════════╣
║  转发目标: {self.deepseek_base_url}                            ║
╠══════════════════════════════════════════════════════════════════╣
║  使用说明:                                                        ║
║  1. 在OpenClaw中设置API地址为: http://127.0.0.1:23456            ║
║  2. 所有通信将被记录到 logs/interceptor/ 目录                     ║
║  3. 查看日志了解完整的请求/响应流程                                ║
╚══════════════════════════════════════════════════════════════════╝
        """)
        
        web.run_app(
            self.app,
            host=self.listen_host,
            port=self.listen_port
        )


if __name__ == "__main__":
    import os
    
    # 从环境变量获取DeepSeek API密钥
    api_key = os.getenv("DEEPSEEK_API_KEY")
    
    if not api_key:
        print("警告: 未设置 DEEPSEEK_API_KEY 环境变量")
        print("请设置: $env:DEEPSEEK_API_KEY='your_api_key_here'")
        print("")
    
    interceptor = APIInterceptor(
        deepseek_api_key=api_key,
        deepseek_base_url="https://api.deepseek.com"
    )
    
    interceptor.run()
