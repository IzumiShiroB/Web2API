import uuid
import json
import re
import asyncio
import os
import signal
import sys
import time
from typing import Optional, List, Dict, Any, Union, AsyncGenerator
from datetime import datetime
from dataclasses import dataclass, field
from collections import deque

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, ConfigDict

from logger import logger
from server_state import set_server_running, set_server_stopped, check_shutdown_requested
from platforms import get_platform, BasePlatform


app = FastAPI(title="AI Web Proxy API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def shutdown_server():
    logger.log_info("system", "Shutting down server...")
    import threading
    import sys
    
    def do_exit():
        time.sleep(0.1)
        sys.exit(0)
    
    threading.Thread(target=do_exit, daemon=True).start()

class ToolCallFunction(BaseModel):
    name: str
    arguments: str


class ToolCall(BaseModel):
    id: str
    type: str = "function"
    function: ToolCallFunction


class ContentPart(BaseModel):
    type: str = "text"
    text: Optional[str] = None
    image_url: Optional[dict] = None


class Message(BaseModel):
    model_config = ConfigDict(json_encoders={})
    role: str
    content: Optional[Union[str, List[ContentPart]]] = None
    name: Optional[str] = None
    tool_calls: Optional[List[ToolCall]] = None
    tool_call_id: Optional[str] = None


class ChatCompletionRequest(BaseModel):
    model: str = "local"
    messages: List[Message]
    temperature: Optional[float] = 1.0
    top_p: Optional[float] = 1.0
    n: Optional[int] = 1
    stream: Optional[bool] = False
    stop: Optional[Union[str, List[str]]] = None
    max_tokens: Optional[int] = None
    presence_penalty: Optional[float] = 0.0
    frequency_penalty: Optional[float] = 0.0
    user: Optional[str] = None
    tools: Optional[List[Dict[str, Any]]] = None
    tool_choice: Optional[Union[str, Dict[str, Any]]] = None


class ChatCompletionChoice(BaseModel):
    index: int
    message: Message
    finish_reason: str = "stop"


class Usage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class ChatCompletionResponse(BaseModel):
    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: List[ChatCompletionChoice]
    usage: Usage


class DeltaMessage(BaseModel):
    role: Optional[str] = None
    content: Optional[str] = None
    tool_calls: Optional[List[Dict[str, Any]]] = None


class StreamChoice(BaseModel):
    index: int
    delta: DeltaMessage
    finish_reason: Optional[str] = None


class ChatCompletionChunk(BaseModel):
    id: str
    object: str = "chat.completion.chunk"
    created: int
    model: str
    choices: List[StreamChoice]


@dataclass(order=True)
class QueuedRequest:
    timestamp: float
    request_id: str = field(compare=False)
    platform_name: str = field(compare=False)
    request_data: ChatCompletionRequest = field(compare=False)
    conversation_id: str = field(compare=False)
    future: asyncio.Future = field(compare=False)


class RequestQueue:
    def __init__(self):
        self._queue: asyncio.PriorityQueue[QueuedRequest] = asyncio.PriorityQueue()
        self._processing = False
        self._worker_task: Optional[asyncio.Task] = None
    
    async def enqueue(self, request_id: str, platform_name: str, 
                      request_data: ChatCompletionRequest,
                      conversation_id: str) -> asyncio.Future:
        future = asyncio.get_event_loop().create_future()
        queued_req = QueuedRequest(
            timestamp=datetime.now().timestamp(),
            request_id=request_id,
            platform_name=platform_name,
            request_data=request_data,
            conversation_id=conversation_id,
            future=future
        )
        await self._queue.put(queued_req)
        logger.log_info(platform_name, f"Request {request_id} enqueued at position {self._queue.qsize()}")
        
        if not self._processing:
            self._worker_task = asyncio.create_task(self._process_queue())
        
        return future
    
    async def _process_queue(self):
        self._processing = True
        
        async def process_single_request(queued_req):
            """并行处理单个请求"""
            try:
                logger.log_info(queued_req.platform_name, 
                              f"Processing queued request {queued_req.request_id}")
                
                result = await self._execute_request(queued_req)
                if not queued_req.future.done():
                    queued_req.future.set_result(result)
            except Exception as e:
                if not queued_req.future.done():
                    queued_req.future.set_exception(e)
        
        # 并行处理队列中的请求（最多同时处理3个）
        tasks = []
        max_concurrent = 3
        
        while not self._queue.empty() or tasks:
            # 启动新任务，直到达到最大并发数
            while len(tasks) < max_concurrent and not self._queue.empty():
                try:
                    queued_req = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                    task = asyncio.create_task(process_single_request(queued_req))
                    tasks.append(task)
                except asyncio.TimeoutError:
                    break
            
            # 等待至少一个任务完成
            if tasks:
                done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                tasks = list(pending)
                
                # 处理已完成的任务
                for task in done:
                    try:
                        await task
                    except Exception as e:
                        logger.log_error("queue", "task_error", str(e))
            else:
                await asyncio.sleep(0.1)
        
        self._processing = False
    
    async def _execute_request(self, queued_req: QueuedRequest):
        platform_name = queued_req.platform_name
        request = queued_req.request_data
        request_id = queued_req.request_id
        conversation_id = queued_req.conversation_id
        
        platform = await get_or_create_platform(platform_name)
        
        async with platform_locks[platform_name]:
            if is_new_conversation(request.messages):
                logger.log_info(platform_name, f"New conversation detected")
                await platform.start_new_conversation()
            
            api_request_json = build_api_request_json(request)
            
            logger.log_forwarded_to_web(
                platform=platform_name,
                conversation_id=conversation_id,
                forwarded_content=api_request_json,
            )
            
            response_text = await platform.send_message(api_request_json)
            logger.log_web_response(
                platform=platform_name,
                conversation_id=conversation_id,
                web_response=response_text,
            )
            
            return response_text


request_queue = RequestQueue()


platform_instances: Dict[str, BasePlatform] = {}
platform_locks: Dict[str, asyncio.Lock] = {}
init_locks: Dict[str, asyncio.Lock] = {}


def get_platform_name_from_model(model: str) -> str:
    selected = os.environ.get("SELECTED_PLATFORM", "").strip()
    if selected:
        return selected
    
    model_lower = model.lower()
    if model_lower == "local":
        return "deepseek"
    if "doubao" in model_lower:
        return "doubao"
    if "deepseek" in model_lower:
        return "deepseek"
    return "deepseek"


async def get_or_create_platform(platform_name: str) -> BasePlatform:
    if platform_name not in init_locks:
        init_locks[platform_name] = asyncio.Lock()
    
    async with init_locks[platform_name]:
        if platform_name not in platform_instances:
            platform = get_platform(platform_name)
            platform.set_browser_closed_callback(shutdown_server)
            await platform.init_browser(headless=False)
            logged_in = await platform.wait_for_login()
            if not logged_in:
                raise HTTPException(status_code=503, detail="Login required or timeout")
            platform_instances[platform_name] = platform
            if platform_name not in platform_locks:
                platform_locks[platform_name] = asyncio.Lock()
        return platform_instances[platform_name]


SYSTEM_PROMPT = """你是一个API兼容的助手。必须严格按以下JSON格式输出！

【重要】不遵守格式将导致系统错误！

【你的能力】
- 你可以调用工具来执行操作（如文件操作、发送消息、执行命令等）
- 当需要执行实际操作时，必须调用工具，而不是在文本中描述
- 你没有权限直接访问文件系统或执行命令，必须通过工具调用

格式一（不需要工具）：
{
  "id": "chatcmpl-xxx",
  "object": "chat.completion",
  "created": 1234567890,
  "model": "local",
  "choices": [{
    "index": 0,
    "message": {
      "role": "assistant",
      "content": "回复内容",
      "tool_calls": null
    },
    "finish_reason": "stop"
  }],
  "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
}

格式二（需要工具）：
{
  "id": "chatcmpl-xxx",
  "object": "chat.completion",
  "created": 1234567890,
  "model": "local",
  "choices": [{
    "index": 0,
    "message": {
      "role": "assistant",
      "content": null,
      "tool_calls": [{
        "id": "call_xxx",
        "type": "function",
        "function": {
          "name": "工具名",
          "arguments": {"参数": "值"}
        }
      }]
    },
    "finish_reason": "tool_calls"
  }],
  "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
}

【记住】
- 调用工具 → content必须是null，finish_reason必须是"tool_calls"
- 不调用工具 → tool_calls必须是null，finish_reason必须是"stop"
- 绝对禁止在content里放工具调用信息！
- 用户要求操作文件/目录/执行命令时，必须用格式二！
- 你没有权限直接访问文件系统，必须通过工具！
- 禁止使用[[reply_to_current]]等标记！

【强制规则】
- 如果你在content中提到要调用工具（如"调用message工具"、"使用sessions_send"等），必须使用格式二！
- 内容中提及工具调用但实际使用格式一，属于严重错误！
- 工具调用决定必须与JSON格式完全一致！

回复语言：使用用户使用的语言。"""


EMOJI_MAP = {
    "happy": "😊",
    "sad": "😢",
    "angry": "😠",
    "surprised": "😲",
    "confused": "😕",
    "color": "💕",
    "cpu": "🤔",
    "fool": "😅",
    "givemoney": "💰",
    "like": "👍",
    "see": "👀",
    "shy": "😳",
    "work": "💼",
    "reply": "💬",
    "meow": "🐱",
    "baka": "😤",
    "morning": "🌅",
    "sleep": "😴",
    "sigh": "😔",
}


def convert_emoji_tags(text: str) -> str:
    def replace_tag(match):
        tag = match.group(1)
        return EMOJI_MAP.get(tag, "")
    return re.sub(r'&&(\w+)&&', replace_tag, text)


def fix_invalid_json_escapes(json_str: str) -> str:
    return json_str.replace('\\', '\\\\')


def fix_literal_newlines_in_strings(json_str: str) -> str:
    result = []
    i = 0
    in_string = False
    while i < len(json_str):
        c = json_str[i]
        if c == '\\' and in_string and i + 1 < len(json_str):
            result.append(c)
            result.append(json_str[i + 1])
            i += 2
            continue
        if c == '"':
            in_string = not in_string
            result.append(c)
        elif in_string and c == '\n':
            result.append('\\n')
        elif in_string and c == '\r':
            result.append('\\r')
        elif in_string and c == '\t':
            result.append('\\t')
        else:
            result.append(c)
        i += 1
    return ''.join(result)


def extract_openai_response(text: str) -> dict:
    def fix_malformed_arguments(json_str: str) -> str:
        result = json_str
        max_iterations = 100
        iteration = 0
        last_fix_pos = 0
        
        while iteration < max_iterations:
            iteration += 1
            idx = result.find('"arguments":', last_fix_pos)
            if idx < 0:
                break
            
            i = idx + len('"arguments":')
            while i < len(result) and result[i] == ' ':
                i += 1
            
            if i >= len(result):
                break
            
            if result[i] == '"':
                quote_start = i
                
                i += 1
                while i < len(result) and result[i] == ' ':
                    i += 1
                
                if i >= len(result) or result[i] != '{':
                    last_fix_pos = idx + 1
                    continue
                
                obj_start = i
                
                brace_count = 0
                in_string = False
                escape_next = False
                obj_end = -1
                
                for j in range(obj_start, len(result)):
                    c = result[j]
                    if escape_next:
                        escape_next = False
                    elif c == '\\':
                        escape_next = True
                    elif c == '"':
                        in_string = not in_string
                    elif not in_string:
                        if c == '{':
                            brace_count += 1
                        elif c == '}':
                            brace_count -= 1
                            if brace_count == 0:
                                obj_end = j + 1
                                break
                
                if obj_end <= obj_start:
                    last_fix_pos = idx + 1
                    continue
                
                end_quote = obj_end
                while end_quote < len(result) and result[end_quote] in ' }':
                    end_quote += 1
                
                if end_quote >= len(result) or result[end_quote] != '"':
                    last_fix_pos = idx + 1
                    continue
                
                raw_content = result[quote_start+1:end_quote]
                
                fixed_parts = []
                k = 0
                while k < len(raw_content):
                    if raw_content[k] == '\\' and k + 1 < len(raw_content):
                        next_char = raw_content[k + 1]
                        if next_char in '"\\/bfnrtu':
                            fixed_parts.append(raw_content[k:k+2])
                            k += 2
                        else:
                            fixed_parts.append('\\\\')
                            k += 1
                    else:
                        fixed_parts.append(raw_content[k])
                        k += 1
                
                fixed = ''.join(fixed_parts)
                
                try:
                    parsed = json.loads(fixed)
                    escaped = json.dumps(parsed, ensure_ascii=False)
                    result = result[:quote_start] + escaped + result[end_quote+1:]
                    last_fix_pos = quote_start + len(escaped)
                except json.JSONDecodeError:
                    last_fix_pos = idx + 1
            elif result[i] == '{':
                last_fix_pos = idx + 1
            else:
                last_fix_pos = idx + 1
        
        return result
    
    text = strip_markdown_json(text)
    
    data = None
    
    if '"id"' in text and '"chatcmpl' in text:
        # 先尝试直接解析（最简单最准确）
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            # 直接解析失败，再用brace提取方法
            def find_json_by_braces(s):
                # Find the first '{' that is NOT inside a string value
                in_string = False
                escape_next = False
                brace_start = -1
                
                for i, char in enumerate(s):
                    if escape_next:
                        escape_next = False
                        continue
                    if char == '\\':
                        escape_next = True
                        continue
                    if char == '"':
                        in_string = not in_string
                        continue
                    if in_string:
                        continue
                    if char == '{':
                        brace_start = i
                        break
                
                if brace_start == -1:
                    return None
                
                brace_count = 0
                escape_next = False
                in_string = False
                for i in range(brace_start, len(s)):
                    char = s[i]
                    if escape_next:
                        escape_next = False
                        continue
                    if char == '\\':
                        escape_next = True
                        continue
                    if char == '"':
                        in_string = not in_string
                        continue
                    if in_string:
                        continue
                    if char == '{':
                        brace_count += 1
                    elif char == '}':
                        brace_count -= 1
                        if brace_count == 0:
                            return s[brace_start:i+1]
                return None
            
            json_str = find_json_by_braces(text)
            if json_str:
                fixed_json_str = fix_malformed_arguments(json_str)
                try:
                    data = json.loads(fixed_json_str)
                except json.JSONDecodeError as e:
                    fixed_json_str = fix_invalid_json_escapes(fixed_json_str)
                    try:
                        data = json.loads(fixed_json_str)
                    except json.JSONDecodeError:
                        pass
    
    if data is None:
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            fixed_text = fix_literal_newlines_in_strings(text)
            try:
                data = json.loads(fixed_text)
            except json.JSONDecodeError:
                fixed_text = fix_invalid_json_escapes(fixed_text)
                try:
                    data = json.loads(fixed_text)
                except json.JSONDecodeError:
                    fixed_text = fix_malformed_arguments(fixed_text)
                    try:
                        data = json.loads(fixed_text)
                    except json.JSONDecodeError:
                        tool_call = try_parse_tool_call_from_text(text)
                        if tool_call:
                            return tool_call
                        return {"content": text, "tool_calls": None, "finish_reason": "stop"}
    
    return validate_and_fix_response(data)


def extract_text_from_content(content) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                texts.append(part.get("text", ""))
            elif hasattr(part, "type") and part.type == "text":
                texts.append(part.text or "")
        return "\n".join(texts)
    return str(content)


def build_prompt_from_messages(messages: List[Message], tools: Optional[List[Dict[str, Any]]] = None) -> str:
    parts = [f"System: {SYSTEM_PROMPT}"]
    
    if tools:
        tools_desc = "\n\n## Available Tools\n\nYou have access to the following tools. When you need to use a tool, respond with a tool_calls JSON structure:\n\n"
        for tool in tools:
            func = tool.get("function", {})
            name = func.get("name", "unknown")
            desc = func.get("description", "")
            params = func.get("parameters", {})
            tools_desc += f"### {name}\n{desc}\nParameters: {json.dumps(params, ensure_ascii=False)}\n\n"
        parts.append(tools_desc)
    
    for msg in messages:
        role = msg.role
        content = extract_text_from_content(msg.content)
        if role == "system":
            parts.append(f"System: {content}")
        elif role == "user":
            parts.append(f"User: {content}")
        elif role == "assistant":
            if msg.tool_calls:
                tool_calls_json = json.dumps([{
                    "id": tc.id,
                    "type": tc.type,
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments
                    }
                } for tc in msg.tool_calls], ensure_ascii=False)
                parts.append(f"Assistant: {{\"role\": \"assistant\", \"content\": null, \"tool_calls\": {tool_calls_json}}}")
            elif content:
                parts.append(f"Assistant: {content}")
        elif role == "tool":
            call_id = msg.tool_call_id or msg.name or 'unknown'
            if call_id == 'unknown':
                if msg.content and 'call_' in msg.content:
                    import re
                    match = re.search(r'call_[a-zA-Z0-9]+', msg.content)
                    if match:
                        call_id = match.group()
            parts.append(f"Tool Result (call_id: {call_id}): {content}")
    return "\n\n".join(parts)


def extract_content_from_json_response(content: str) -> str:
    """Extract actual content from a JSON-formatted API response"""
    if not isinstance(content, str):
        return content
    
    # Check if content looks like a JSON response
    if '"chat.completion"' in content or '"object":' in content:
        try:
            # Try to parse as JSON
            start_idx = content.find('{')
            end_idx = content.rfind('}')
            if start_idx >= 0 and end_idx > start_idx:
                json_str = content[start_idx:end_idx+1]
                parsed = json.loads(json_str)
                
                # Extract content from choices[0].message.content
                choices = parsed.get('choices', [])
                if choices and len(choices) > 0:
                    message = choices[0].get('message', {})
                    actual_content = message.get('content')
                    if actual_content:
                        return actual_content
                    
                    # If no content but has tool_calls, return empty string
                    tool_calls = message.get('tool_calls')
                    if tool_calls:
                        return ""
        except (json.JSONDecodeError, KeyError):
            pass
    
    return content


def fix_tool_call_arguments(tool_calls: list) -> list:
    """Fix improperly escaped arguments in tool calls"""
    fixed_calls = []
    for tc in tool_calls:
        if tc.get('type') == 'function':
            func = tc.get('function', {})
            args = func.get('arguments', '')
            
            if isinstance(args, str):
                if args.startswith('{') and args.endswith('}'):
                    try:
                        parsed = json.loads(args)
                        func['arguments'] = json.dumps(parsed, ensure_ascii=False)
                    except json.JSONDecodeError:
                        fixed_args = extract_and_fix_json_string(args)
                        if fixed_args:
                            try:
                                parsed = json.loads(fixed_args)
                                func['arguments'] = json.dumps(parsed, ensure_ascii=False)
                            except json.JSONDecodeError:
                                func['arguments'] = args
                        else:
                            func['arguments'] = args
                elif '"' in args and not args.startswith('"'):
                    try:
                        parsed = json.loads(args)
                        func['arguments'] = json.dumps(parsed, ensure_ascii=False)
                    except json.JSONDecodeError:
                        fixed_args = extract_and_fix_json_string(args)
                        if fixed_args:
                            try:
                                parsed = json.loads(fixed_args)
                                func['arguments'] = json.dumps(parsed, ensure_ascii=False)
                            except json.JSONDecodeError:
                                func['arguments'] = args
                        else:
                            func['arguments'] = args
                else:
                    func['arguments'] = args
            elif isinstance(args, dict):
                func['arguments'] = json.dumps(args, ensure_ascii=False)
            
            fixed_calls.append(tc)
        else:
            fixed_calls.append(tc)
    
    return fixed_calls


def extract_and_fix_json_string(s: str) -> str:
    """Extract and fix JSON string from malformed arguments string"""
    if not s:
        return None
    
    brace_count = 0
    in_string = False
    escape_next = False
    result = []
    
    for i, c in enumerate(s):
        if escape_next:
            result.append(c)
            escape_next = False
        elif c == '\\':
            result.append(c)
            escape_next = True
        elif c == '"':
            in_string = not in_string
            result.append(c)
        elif not in_string:
            if c == '{':
                brace_count += 1
                result.append(c)
            elif c == '}':
                brace_count -= 1
                result.append(c)
                if brace_count == 0:
                    break
            else:
                result.append(c)
        else:
            result.append(c)
    
    extracted = ''.join(result)
    if not extracted:
        return None
    
    fixed = extracted.replace('\\', '\\\\')
    return fixed


def validate_and_fix_response(response: dict) -> dict:
    """Validate and fix OpenAI API response format"""
    if not isinstance(response, dict):
        return {"content": str(response), "tool_calls": None, "finish_reason": "stop"}
    
    if "choices" in response:
        choices = response.get("choices", [])
        if not choices:
            return {"content": "", "tool_calls": None, "finish_reason": "stop"}
        
        choice = choices[0] if isinstance(choices[0], dict) else {}
        message = choice.get("message", {})
        
        content = message.get("content")
        tool_calls = message.get("tool_calls")
        finish_reason = choice.get("finish_reason", "stop")
        
        if tool_calls:
            tool_calls = fix_tool_call_arguments(tool_calls)
            for tc in tool_calls:
                if tc.get("type") == "function":
                    func = tc.get("function", {})
                    if "name" not in func:
                        func["name"] = "unknown"
                    if "arguments" not in func:
                        func["arguments"] = "{}"
                    if "id" not in tc:
                        tc["id"] = f"call_{uuid.uuid4().hex[:12]}"
            
            if content is not None and tool_calls:
                content = None
            
            finish_reason = "tool_calls"
        else:
            if content is None:
                content = ""
            tool_calls = None
            finish_reason = "stop"
        
        return {
            "content": content,
            "tool_calls": tool_calls,
            "finish_reason": finish_reason
        }
    
    if "content" in response or "tool_calls" in response:
        tool_calls = response.get("tool_calls")
        if tool_calls:
            tool_calls = fix_tool_call_arguments(tool_calls)
        return {
            "content": response.get("content"),
            "tool_calls": tool_calls,
            "finish_reason": "tool_calls" if tool_calls else "stop"
        }
    
    return {"content": str(response), "tool_calls": None, "finish_reason": "stop"}


def strip_markdown_json(text: str) -> str:
    """Remove markdown code blocks and extract embedded JSON from text"""
    text = text.strip()
    
    text = re.sub(r'\[\[reply_to_current\]\]', '', text)
    text = re.sub(r'\[\[.*?\]\]', '', text)
    
    json_patterns = [
        r'```json\s*(\{.*?\})\s*```',
        r'```\s*(\{.*?\})\s*```',
        r'json\s*(\{.*?\})\s*$',
    ]
    
    for pattern in json_patterns:
        match = re.search(pattern, text, re.DOTALL)
        if match:
            text = match.group(1)
            break
    
    if text.startswith('```'):
        lines = text.split('\n')
        if len(lines) > 1:
            if lines[0].startswith('```json') or lines[0].startswith('```'):
                lines = lines[1:]
            if lines and lines[-1].strip() == '```':
                lines = lines[:-1]
            text = '\n'.join(lines)
    
    if '"id"' in text and '"chatcmpl' in text:
        def find_json_start(s):
            idx = s.find('"id"')
            if idx == -1:
                return -1
            brace_count = 0
            for i in range(idx, -1, -1):
                if s[i] == '}':
                    brace_count += 1
                elif s[i] == '{':
                    brace_count -= 1
                    if brace_count < 0:
                        return i
            return -1
        
        start = find_json_start(text)
        if start >= 0:
            brace_count = 0
            end = start
            for i in range(start, len(text)):
                if text[i] == '{':
                    brace_count += 1
                elif text[i] == '}':
                    brace_count -= 1
                    if brace_count == 0:
                        end = i + 1
                        break
            if end > start:
                text = text[start:end]
    
    return text.strip()


def try_parse_tool_call_from_text(text: str) -> Optional[dict]:
    """Try to extract tool call from text that might not be proper JSON"""
    tool_patterns = [
        r'"name"\s*:\s*"([^"]+)"',
        r'"function"\s*:\s*\{[^}]*"name"\s*:\s*"([^"]+)"',
        r'function["\']?\s*:\s*\{[^}]*name["\']?\s*:\s*["\']([^"\']+)["\']',
    ]
    
    tool_name = None
    for pattern in tool_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            tool_name = match.group(1)
            break
    
    if not tool_name:
        return None
    
    args_match = re.search(r'"arguments"\s*:\s*(".*?"|\{.*?\})', text, re.DOTALL)
    args = "{}"
    if args_match:
        args_str = args_match.group(1)
        try:
            if args_str.startswith('"'):
                args = json.loads(args_str)
            else:
                args = args_str
        except:
            args = "{}"
    
    return {
        "content": None,
        "tool_calls": [{
            "id": f"call_{uuid.uuid4().hex[:12]}",
            "type": "function",
            "function": {
                "name": tool_name,
                "arguments": args if isinstance(args, str) else json.dumps(args, ensure_ascii=False)
            }
        }],
        "finish_reason": "tool_calls"
    }


def build_api_request_json(request: ChatCompletionRequest) -> str:
    request_dict = request.model_dump(exclude_none=True)
    
    user_system_prompt = ""
    processed_messages = []
    for msg in request.messages:
        if msg.role == "system":
            user_system_prompt = extract_text_from_content(msg.content) if msg.content else ""
            continue
        
        if msg.role == "tool":
            call_id = msg.tool_call_id or "unknown"
            tool_content = extract_text_from_content(msg.content)
            tool_result_text = f"role: tool | call_id: {call_id} | result:\n{tool_content}"
            msg_dict = {
                "role": "user",
                "content": tool_result_text,
                "tool_calls": None,
                "tool_call_id": None,
                "name": None,
            }
        else:
            msg_dict = {
                "role": msg.role,
                "tool_calls": [{
                    "id": tc.id,
                    "type": tc.type,
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments
                    }
                } for tc in msg.tool_calls] if msg.tool_calls else None,
                "tool_call_id": msg.tool_call_id,
                "name": msg.name,
            }
            
            if msg.content is None:
                msg_dict["content"] = None
            elif isinstance(msg.content, str):
                msg_dict["content"] = extract_content_from_json_response(msg.content)
            elif isinstance(msg.content, list):
                processed_content = []
                for part in msg.content:
                    if isinstance(part, dict):
                        if part.get("type") == "text":
                            text_content = part.get("text", "")
                            extracted = extract_content_from_json_response(text_content)
                            processed_content.append({"type": "text", "text": extracted})
                        elif part.get("type") == "image_url":
                            processed_content.append({"type": "image_url", "image_url": part.get("image_url", {})})
                        else:
                            processed_content.append(part)
                    elif hasattr(part, "type"):
                        if part.type == "text":
                            text_content = part.text or ""
                            extracted = extract_content_from_json_response(text_content)
                            processed_content.append({"type": "text", "text": extracted})
                        elif part.type == "image_url":
                            processed_content.append({"type": "image_url", "image_url": getattr(part, "image_url", {})})
                msg_dict["content"] = processed_content
            else:
                msg_dict["content"] = str(msg.content)
        
        # 在用户消息结尾添加工具调用提示（避免遗忘）
        if msg.role == "user" and msg_dict.get("content"):
            tool_reminder = """

---
【系统提示 - 工具调用提醒】
如果需要执行实际操作（如文件操作、发送消息、执行命令等），请使用工具调用格式（格式二），而不是在文本中描述。
记住：你只能通过工具来执行操作，没有直接权限。"""
            
            if isinstance(msg_dict["content"], str):
                msg_dict["content"] += tool_reminder
            elif isinstance(msg_dict["content"], list):
                # 对于多部分内容，在最后一个文本部分添加提示
                for i in range(len(msg_dict["content"]) - 1, -1, -1):
                    if isinstance(msg_dict["content"][i], dict) and msg_dict["content"][i].get("type") == "text":
                        msg_dict["content"][i]["text"] += tool_reminder
                        break
        
        processed_messages.append(msg_dict)
    
    request_dict["messages"] = processed_messages
    
    combined_system_prompt = SYSTEM_PROMPT
    if user_system_prompt:
        combined_system_prompt = """# PERSONA INSTRUCTION - HIGHEST PRIORITY
You MUST maintain the persona defined below in ALL responses. The persona's tone, style, and personality should be reflected in the "content" field of your JSON response.

""" + user_system_prompt + """

========================================
JSON RESPONSE FORMAT (Required for API compatibility)
========================================
""" + SYSTEM_PROMPT
    
    full_request = {
        "system_prompt": combined_system_prompt,
        "api_request": request_dict,
    }
    
    return json.dumps(full_request, ensure_ascii=False, indent=2)


def is_new_conversation(messages: List[Message]) -> bool:
    user_messages = [m for m in messages if m.role == "user"]
    return len(user_messages) <= 1


def get_last_user_message(messages: List[Message]) -> str:
    for msg in reversed(messages):
        if msg.role == "user":
            return extract_text_from_content(msg.content)
    return ""


@app.post("/v1/chat/completions")
@app.post("/chat/completions")
async def chat_completions(request: ChatCompletionRequest):
    request_id = f"chatcmpl-{uuid.uuid4().hex[:8]}"
    platform_name = get_platform_name_from_model(request.model)
    
    api_request = request.model_dump()
    conversation_id = logger.start_conversation(
        platform=platform_name,
        request_id=request_id,
        api_request=api_request,
    )
    
    try:
        if platform_name not in platform_locks:
            platform_locks[platform_name] = asyncio.Lock()
        
        has_tool_history = any(
            (msg.tool_calls is not None and len(msg.tool_calls) > 0) or msg.role == 'tool'
            for msg in request.messages
        )
        
        user_msg_count = len([m for m in request.messages if m.role == "user"])
        logger.log_info(platform_name, f"Request {request_id}: user_messages={user_msg_count}, has_tool_history={has_tool_history}")
        
        future = await request_queue.enqueue(request_id, platform_name, request, conversation_id)
        
        try:
            response_text = await asyncio.wait_for(future, timeout=180)
        except asyncio.TimeoutError:
            raise HTTPException(status_code=504, detail="Request timeout")
        
        extracted = extract_openai_response(response_text)
        content = extracted.get("content") or ""
        if content:
            content = convert_emoji_tags(content).strip()
        
        if request.stream:
            if extracted.get("tool_calls"):
                tool_calls = []
                for idx, tc in enumerate(extracted["tool_calls"]):
                    args = tc["function"]["arguments"]
                    if isinstance(args, dict):
                        args = json.dumps(args, ensure_ascii=False)
                    elif not isinstance(args, str):
                        args = str(args)
                    tool_calls.append({
                        "index": idx,
                        "id": tc.get("id", f"call_{uuid.uuid4().hex[:8]}"),
                        "type": tc.get("type", "function"),
                        "function": {
                            "name": tc["function"]["name"],
                            "arguments": args
                        }
                    })
                
                response_dict_for_log = {
                    "id": request_id,
                    "object": "chat.completion",
                    "created": int(datetime.now().timestamp()),
                    "model": request.model,
                    "choices": [{
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": tool_calls
                        },
                        "finish_reason": "tool_calls"
                    }],
                    "usage": {
                        "prompt_tokens": 0,
                        "completion_tokens": 0,
                        "total_tokens": 0,
                    }
                }
                
                logger.log_api_response(
                    platform=platform_name,
                    conversation_id=conversation_id,
                    api_response=response_dict_for_log,
                )
                
                async def generate_tool_call_stream():
                    first_chunk = {
                        "id": request_id,
                        "object": "chat.completion.chunk",
                        "created": int(datetime.now().timestamp()),
                        "model": request.model,
                        "choices": [{
                            "index": 0,
                            "delta": {
                                "role": "assistant",
                                "content": None,
                                "tool_calls": tool_calls
                            },
                            "finish_reason": None
                        }]
                    }
                    yield f"data: {json.dumps(first_chunk, ensure_ascii=False)}\n\n"
                    
                    final_chunk = {
                        "id": request_id,
                        "object": "chat.completion.chunk",
                        "created": int(datetime.now().timestamp()),
                        "model": request.model,
                        "choices": [{
                            "index": 0,
                            "delta": {},
                            "finish_reason": "tool_calls"
                        }]
                    }
                    yield f"data: {json.dumps(final_chunk, ensure_ascii=False)}\n\n"
                    yield "data: [DONE]\n\n"
                
                return StreamingResponse(
                    generate_tool_call_stream(),
                    media_type="text/event-stream",
                )
            
            async def generate_stream():
                first_chunk = {
                    "id": request_id,
                    "object": "chat.completion.chunk",
                    "created": int(datetime.now().timestamp()),
                    "model": request.model,
                    "choices": [{
                        "index": 0,
                        "delta": {"role": "assistant"},
                        "finish_reason": None
                    }]
                }
                yield f"data: {json.dumps(first_chunk, ensure_ascii=False)}\n\n"
                
                chunk_size = 10
                words = content.split()
                for i in range(0, len(words), chunk_size):
                    chunk_words = words[i:i+chunk_size]
                    chunk_text = " ".join(chunk_words)
                    if i + chunk_size < len(words):
                        chunk_text += " "
                    
                    chunk = {
                        "id": request_id,
                        "object": "chat.completion.chunk",
                        "created": int(datetime.now().timestamp()),
                        "model": request.model,
                        "choices": [{
                            "index": 0,
                            "delta": {"content": chunk_text},
                            "finish_reason": None
                        }]
                    }
                    yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
                    await asyncio.sleep(0.01)
                
                final_chunk = {
                    "id": request_id,
                    "object": "chat.completion.chunk",
                    "created": int(datetime.now().timestamp()),
                    "model": request.model,
                    "choices": [{
                        "index": 0,
                        "delta": {},
                        "finish_reason": "stop"
                    }]
                }
                yield f"data: {json.dumps(final_chunk, ensure_ascii=False)}\n\n"
                yield "data: [DONE]\n\n"
            
            logger.log_api_response(
                platform=platform_name,
                conversation_id=conversation_id,
                api_response={
                    "id": request_id,
                    "object": "chat.completion",
                    "created": int(datetime.now().timestamp()),
                    "model": request.model,
                    "choices": [{
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": content,
                            "tool_calls": None
                        },
                        "finish_reason": "stop"
                    }],
                    "usage": {
                        "prompt_tokens": 0,
                        "completion_tokens": len(content) // 4,
                        "total_tokens": len(content) // 4,
                    }
                },
            )
            
            return StreamingResponse(
                generate_stream(),
                media_type="text/event-stream",
            )
        
        if content == "<None>":
            for msg in request.messages:
                if msg.role == "user":
                    user_content = extract_text_from_content(msg.content)
                    if "Generate a concise title for the following user query:" in user_content:
                        lines = user_content.split("\n")
                        if len(lines) > 1:
                            content = lines[-1].strip()
                    else:
                        content = user_content.strip()
                    break
            if content == "<None>":
                content = "Untitled"
        
        if content:
            content = convert_emoji_tags(content).strip()
        
        message = Message(role="assistant", content=content)
        finish_reason = extracted.get("finish_reason", "stop")
        
        if "tool_calls" in extracted and extracted["tool_calls"]:
            tool_calls = []
            for tc in extracted["tool_calls"]:
                args = tc["function"]["arguments"]
                if isinstance(args, dict):
                    args = json.dumps(args, ensure_ascii=False)
                elif not isinstance(args, str):
                    args = str(args)
                tool_calls.append(ToolCall(
                    id=tc.get("id", f"call_{uuid.uuid4().hex[:8]}"),
                    type=tc.get("type", "function"),
                    function=ToolCallFunction(
                        name=tc["function"]["name"],
                        arguments=args
                    )
                ))
            message.tool_calls = tool_calls
            message.content = None
            finish_reason = "tool_calls"
        
        content_len = len(extracted.get("content") or "")
        response = ChatCompletionResponse(
            id=request_id,
            created=int(datetime.now().timestamp()),
            model=request.model,
            choices=[
                ChatCompletionChoice(
                    index=0,
                    message=message,
                    finish_reason=finish_reason,
                )
            ],
            usage=Usage(
                prompt_tokens=0,
                completion_tokens=content_len // 4,
                total_tokens=content_len // 4,
            ),
        )
        
        response_dict = response.model_dump(exclude_none=True)
        
        # Ensure message has all required fields for OpenAI compatibility
        message_dict = response_dict["choices"][0]["message"]
        if "content" not in message_dict:
            message_dict["content"] = None
        if "tool_calls" not in message_dict:
            message_dict["tool_calls"] = None
        
        if finish_reason == "tool_calls":
            message_dict["content"] = None
        
        try:
            logger.log_api_response(
                platform=platform_name,
                conversation_id=conversation_id,
                api_response=response_dict,
            )
        except Exception as log_e:
            import traceback
            logger.main_logger.warning(f"[{platform_name}] Log error (non-critical): {str(log_e)}")
        
        return JSONResponse(content=response_dict)
                
    except Exception as e:
        import traceback
        error_detail = f"{str(e)}\n{traceback.format_exc()}"
        logger.log_error(platform_name, conversation_id, error_detail)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/v1/models")
@app.get("/models")
async def list_models():
    return {
        "object": "list",
        "data": [
            {
                "id": "local",
                "object": "model",
                "created": int(datetime.now().timestamp()),
                "owned_by": "local",
            }
        ],
    }


@app.get("/health")
async def health():
    return {"status": "ok"}


async def check_launcher_shutdown():
    while True:
        try:
            if check_shutdown_requested():
                logger.log_info("system", "Shutdown requested by launcher, stopping server...")
                shutdown_server()
                break
        except Exception as e:
            logger.log_error("system", "shutdown_check_error", str(e))
        await asyncio.sleep(0.1)


@app.on_event("startup")
async def startup():
    logger.log_info("system", "API Server starting up...")
    
    selected_platform = os.environ.get("SELECTED_PLATFORM", "").strip()
    set_server_running(selected_platform or "default", os.getpid())
    
    if selected_platform:
        logger.log_info("system", f"Pre-initializing platform: {selected_platform}")
        try:
            await get_or_create_platform(selected_platform)
            logger.log_info("system", f"Platform {selected_platform} initialized successfully")
        except Exception as e:
            logger.log_error("system", "", f"Failed to initialize platform {selected_platform}: {e}")
    
    asyncio.create_task(check_launcher_shutdown())


@app.on_event("shutdown")
async def shutdown():
    for platform in platform_instances.values():
        await platform.close()
    set_server_stopped()
    logger.log_info("system", "API Server shut down")
