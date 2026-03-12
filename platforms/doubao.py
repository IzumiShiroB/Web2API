import asyncio
from datetime import datetime
from typing import AsyncGenerator, List, Dict, Any, Optional
import json
import base64
import io
import tempfile
import os

from .base import BasePlatform
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from logger import logger


class DoubaoPlatform(BasePlatform):
    name = "doubao"
    url = "https://www.doubao.com/chat/"
    
    def __init__(self, data_dir: str = "browser_data"):
        super().__init__(data_dir)
        self._response_queue = asyncio.Queue()
        self._is_streaming = False
    
    async def _save_image_to_temp(self, image_data: str) -> Optional[str]:
        try:
            if image_data.startswith("data:image"):
                header, image_data = image_data.split(",", 1)
                if "jpeg" in header or "jpg" in header:
                    suffix = ".jpg"
                elif "png" in header:
                    suffix = ".png"
                elif "gif" in header:
                    suffix = ".gif"
                else:
                    suffix = ".png"
            else:
                suffix = ".png"
            
            image_bytes = base64.b64decode(image_data)
            
            temp_file = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
            temp_file.write(image_bytes)
            temp_file.close()
            
            logger.log_browser_action(self.name, "image_saved_to_temp", {"path": temp_file.name})
            return temp_file.name
            
        except Exception as e:
            logger.log_error(self.name, "image_save_error", str(e), {"error": str(e)})
            return None
    
    async def _upload_image_via_clipboard(self, image_path: str) -> bool:
        try:
            import subprocess
            
            ps_script = f'''
            Add-Type -AssemblyName System.Windows.Forms
            Add-Type -AssemblyName System.Drawing
            
            $image = [System.Drawing.Image]::FromFile("{image_path.replace("\\", "\\\\")}")
            [System.Windows.Forms.Clipboard]::SetImage($image)
            $image.Dispose()
            '''
            
            result = subprocess.run(
                ["powershell", "-Command", ps_script],
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if result.returncode != 0:
                logger.log_error(self.name, "clipboard_error", result.stderr, {"stderr": result.stderr})
                return False
            
            input_el = await self._get_input_element()
            if input_el:
                await input_el.click()
                await asyncio.sleep(0.2)
                await self.page.keyboard.press("Control+v")
                await asyncio.sleep(0.5)
                logger.log_browser_action(self.name, "image_pasted", {"path": image_path})
                return True
            
            return False
            
        except Exception as e:
            logger.log_error(self.name, "paste_error", str(e), {"error": str(e)})
            return False
    
    async def _upload_images(self, images: List[str]):
        for image_data in images:
            temp_path = await self._save_image_to_temp(image_data)
            if temp_path:
                success = await self._upload_image_via_clipboard(temp_path)
                try:
                    os.unlink(temp_path)
                except:
                    pass
                if success:
                    await asyncio.sleep(0.5)
                    logger.log_browser_action(self.name, "image_uploaded", {})
    
    def _extract_images_from_message(self, message: str) -> tuple[str, List[str]]:
        try:
            data = json.loads(message)
            api_request = data.get("api_request", {})
            messages = api_request.get("messages", [])
            
            images = []
            text_parts = []
            
            for msg in messages:
                if msg.get("role") == "user":
                    content = msg.get("content")
                    if isinstance(content, list):
                        for part in content:
                            if isinstance(part, dict):
                                if part.get("type") == "text":
                                    text_parts.append(part.get("text", ""))
                                elif part.get("type") == "image_url":
                                    img_url = part.get("image_url", {})
                                    url = img_url.get("url", "")
                                    if url:
                                        images.append(url)
                    elif isinstance(content, str):
                        text_parts.append(content)
            
            return "\n".join(text_parts), images
            
        except json.JSONDecodeError:
            return message, []
    
    def _remove_images_from_json(self, message: str) -> str:
        try:
            data = json.loads(message)
            api_request = data.get("api_request", {})
            messages = api_request.get("messages", [])
            
            for msg in messages:
                if msg.get("role") == "user":
                    content = msg.get("content")
                    if isinstance(content, list):
                        new_content = []
                        for part in content:
                            if isinstance(part, dict):
                                if part.get("type") == "text":
                                    new_content.append(part)
                        if new_content:
                            msg["content"] = new_content
                        else:
                            msg["content"] = "[图片已上传]"
            
            return json.dumps(data, ensure_ascii=False, indent=2)
            
        except json.JSONDecodeError:
            return message
    
    async def wait_for_login(self, timeout: int = 300):
        start_time = datetime.now()
        while (datetime.now() - start_time).seconds < timeout:
            try:
                input_el = await self._get_input_element()
                if input_el:
                    logger.log_browser_action(self.name, "login_detected", {})
                    return True
                
                login_indicators = [
                    'button:has-text("登录")',
                    'button:has-text("Login")',
                    'button:has-text("注册")',
                    'button:has-text("Sign")',
                ]
                for indicator in login_indicators:
                    el = await self.page.query_selector(indicator)
                    if el:
                        is_visible = await el.is_visible()
                        if is_visible:
                            logger.log_browser_action(self.name, "login_required", {})
                            break
                            
            except Exception as e:
                logger.log_browser_action(self.name, "login_check_error", {"error": str(e)})
            await asyncio.sleep(2)
        
        logger.log_error(self.name, "login_timeout", "Login timeout", {"timeout": timeout})
        return False
    
    async def _get_input_element(self):
        selectors = [
            'textarea[placeholder*="问"]',
            'textarea[placeholder*="输入"]',
            'textarea[placeholder*="Ask"]',
            'textarea[placeholder*="Message"]',
            'div[contenteditable="true"]:not([role="button"])',
            'textarea:not([type])',
            '.chat-input textarea',
            '#chat-input',
            '[data-testid="chat-input"]',
        ]
        for selector in selectors:
            try:
                el = await self.page.query_selector(selector)
                if el:
                    is_visible = await el.is_visible()
                    if is_visible:
                        box = await el.bounding_box()
                        if box and box['width'] > 100:
                            logger.log_browser_action(self.name, "input_found", {"selector": selector})
                            return el
            except Exception:
                continue
        return None
    
    async def _get_send_button(self):
        selectors = [
            'button[type="submit"]',
            'button:has-text("发送")',
            'button[aria-label*="发送"]',
            'button[aria-label*="Send"]',
            'button:has-text("Send")',
        ]
        for selector in selectors:
            el = await self.page.query_selector(selector)
            if el:
                is_visible = await el.is_visible()
                if is_visible:
                    return el
        return None
    
    async def send_message(self, message: str) -> str:
        await self.ensure_browser()
        
        message_count_before = await self.get_current_messages_count()
        
        input_el = await self._get_input_element()
        if not input_el:
            screenshot_path = self.data_dir / f"debug_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
            await self.page.screenshot(path=str(screenshot_path))
            logger.log_error(self.name, "input_not_found", "Cannot find input element", {
                "screenshot": str(screenshot_path)
            })
            raise Exception("Cannot find input element")
        
        _, images = self._extract_images_from_message(message)
        
        if images:
            logger.log_browser_action(self.name, "images_detected", {"count": len(images)})
            await self._upload_images(images)
            await asyncio.sleep(0.5)
            message_to_send = self._remove_images_from_json(message)
        else:
            message_to_send = message
        
        tag_name = await input_el.evaluate("el => el.tagName.toLowerCase()")
        
        if tag_name == "textarea":
            await input_el.fill(message_to_send)
        else:
            await input_el.click()
            await asyncio.sleep(0.2)
            await self.page.keyboard.type(message_to_send, delay=10)
        
        logger.log_browser_action(self.name, "message_typed", {"message_length": len(message_to_send), "images": len(images)})
        
        await asyncio.sleep(0.3)
        
        send_btn = await self._get_send_button()
        if send_btn:
            await send_btn.click()
        else:
            await self.page.keyboard.press("Enter")
        
        logger.log_browser_action(self.name, "message_sent", {})
        
        response = await self._wait_for_response(message_count_before)
        return response
    
    async def start_new_conversation(self):
        await self.ensure_browser()
        new_chat_selectors = [
            'button:has-text("新对话")',
            'button:has-text("New Chat")',
            'button:has-text("新建")',
            '[class*="new-chat"]',
            '[class*="newChat"]',
        ]
        
        for selector in new_chat_selectors:
            try:
                el = await self.page.query_selector(selector)
                if el:
                    is_visible = await el.is_visible()
                    if is_visible:
                        await el.click()
                        await asyncio.sleep(1)
                        logger.log_browser_action(self.name, "new_conversation_started", {})
                        self.conversation_history = []
                        self.last_message_count = 0
                        return True
            except Exception:
                continue
        return False
    
    async def get_current_messages_count(self) -> int:
        try:
            message_selectors = [
                '[class*="message"]:not([class*="markdown"])',
                '[class*="chat-message"]',
                '[class*="conversation"][class*="item"]',
                '[data-testid*="message"]',
            ]
            count = 0
            for selector in message_selectors:
                try:
                    elements = await self.page.query_selector_all(selector)
                    if elements:
                        count = max(count, len(elements))
                except Exception:
                    continue
            return count
        except Exception:
            return 0
    
    async def _wait_for_response(self, message_count_before: int = 0, timeout: int = 120) -> str:
        await asyncio.sleep(2)
        
        start_time = datetime.now()
        last_response = ""
        stable_count = 0
        
        while (datetime.now() - start_time).seconds < timeout:
            try:
                response_selectors = [
                    '.markdown-body',
                    '.message-content',
                    '.prose',
                    '[class*="response"]',
                    '[class*="assistant"]',
                    '[class*="answer"]',
                    'div[class*="markdown"]',
                ]
                
                for selector in response_selectors:
                    try:
                        elements = await self.page.query_selector_all(selector)
                        if elements and len(elements) > 0:
                            if len(elements) <= message_count_before:
                                continue
                            
                            last_element = elements[-1]
                            text = await last_element.inner_text()
                            if text and len(text) > len(last_response):
                                last_response = text
                                stable_count = 0
                            elif text and text == last_response:
                                stable_count += 1
                    except Exception:
                        continue
                
                if last_response and stable_count > 6:
                    logger.log_browser_action(self.name, "response_complete", {
                        "response_length": len(last_response)
                    })
                    return last_response
                
                await asyncio.sleep(0.5)
                
            except Exception as e:
                logger.log_error(self.name, "response_wait_error", str(e))
                await asyncio.sleep(0.5)
        
        if last_response:
            return last_response
        
        raise Exception("Response timeout")
    
    async def send_message_stream(self, message: str) -> AsyncGenerator[str, None]:
        await self.ensure_browser()
        
        message_count_before = await self.get_current_messages_count()
        
        input_el = await self._get_input_element()
        if not input_el:
            logger.log_error(self.name, "input_not_found", "Cannot find input element")
            raise Exception("Cannot find input element")
        
        _, images = self._extract_images_from_message(message)
        
        if images:
            logger.log_browser_action(self.name, "images_detected_stream", {"count": len(images)})
            await self._upload_images(images)
            await asyncio.sleep(0.5)
            message_to_send = self._remove_images_from_json(message)
        else:
            message_to_send = message
        
        tag_name = await input_el.evaluate("el => el.tagName.toLowerCase()")
        
        if tag_name == "textarea":
            await input_el.fill(message_to_send)
        else:
            await input_el.click()
            await asyncio.sleep(0.2)
            await self.page.keyboard.type(message_to_send, delay=10)
        
        await asyncio.sleep(0.3)
        
        send_btn = await self._get_send_button()
        if send_btn:
            await send_btn.click()
        else:
            await self.page.keyboard.press("Enter")
        
        logger.log_browser_action(self.name, "stream_started", {})
        
        last_text = ""
        timeout = 120
        start_time = datetime.now()
        
        await asyncio.sleep(1)
        
        while (datetime.now() - start_time).seconds < timeout:
            try:
                response_selectors = [
                    '.markdown-body',
                    '.message-content',
                    '[data-testid="response"]',
                    '.prose',
                ]
                
                for selector in response_selectors:
                    elements = await self.page.query_selector_all(selector)
                    logger.logger.debug(f"[{self.name}] Selector {selector} found {len(elements)} elements")
                    if elements and len(elements) > message_count_before:
                        last_element = elements[-1]
                        text = await last_element.inner_text()
                        logger.logger.debug(f"[{self.name}] Last element text length: {len(text)}, last_text length: {len(last_text)}")
                        if text and len(text) > len(last_text):
                            new_text = text[len(last_text):]
                            last_text = text
                            yield new_text
                
                stop_indicators = [
                    'button:has-text("重新生成")',
                    'button:has-text("Regenerate")',
                ]
                
                for indicator in stop_indicators:
                    el = await self.page.query_selector(indicator)
                    if el:
                        logger.log_browser_action(self.name, "stream_complete", {
                            "total_length": len(last_text)
                        })
                        return
                
                await asyncio.sleep(0.3)
                
            except Exception as e:
                logger.logger.error(f"[{self.name}] Stream loop error: {e}")
                await asyncio.sleep(0.3)
        
        if last_text:
            yield last_text
