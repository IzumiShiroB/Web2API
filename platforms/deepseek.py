import asyncio
from datetime import datetime
from typing import AsyncGenerator

from .base import BasePlatform
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from logger import logger


class DeepSeekPlatform(BasePlatform):
    name = "deepseek"
    url = "https://chat.deepseek.com/"
    MAX_REQUESTS_PER_CONVERSATION = 5
    
    def __init__(self, data_dir: str = "browser_data"):
        super().__init__(data_dir)
        self._response_queue = asyncio.Queue()
        self._is_streaming = False
        self._request_count = 0
    
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
        
        if self._request_count >= self.MAX_REQUESTS_PER_CONVERSATION:
            logger.log_browser_action(self.name, "auto_new_conversation", {
                "request_count": self._request_count,
                "max_requests": self.MAX_REQUESTS_PER_CONVERSATION
            })
            
            new_conv_success = await self.start_new_conversation()
            
            if new_conv_success:
                logger.log_browser_action(self.name, "waiting_for_new_conversation_ready", {})
                await asyncio.sleep(3)
                
                for attempt in range(10):
                    input_el = await self._get_input_element()
                    if input_el:
                        is_visible = await input_el.is_visible()
                        if is_visible:
                            break
                    await asyncio.sleep(0.3)
                else:
                    logger.log_browser_action(self.name, "input_not_ready_after_new_conversation", {})
                    raise Exception("Input element not ready after new conversation")
            else:
                logger.log_browser_action(self.name, "new_conversation_failed_continuing", {})
                await asyncio.sleep(2)
            
            self._request_count = 0
        
        self._request_count += 1
        logger.log_browser_action(self.name, "request_count_incremented", {
            "current_count": self._request_count,
            "max_requests": self.MAX_REQUESTS_PER_CONVERSATION
        })
        
        message_count_before = await self.get_current_messages_count()
        
        input_el = await self._get_input_element()
        if not input_el:
            screenshot_path = self.data_dir / f"debug_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
            await self.page.screenshot(path=str(screenshot_path))
            logger.log_error(self.name, "input_not_found", "Cannot find input element", {
                "screenshot": str(screenshot_path)
            })
            raise Exception("Cannot find input element")
        
        tag_name = await input_el.evaluate("el => el.tagName.toLowerCase()")
        
        fill_timeout = max(60000, len(message) * 2)
        
        if tag_name == "textarea":
            await input_el.fill(message, timeout=fill_timeout)
        else:
            await input_el.click()
            await asyncio.sleep(0.2)
            await self.page.keyboard.type(message, delay=10)
        
        logger.log_browser_action(self.name, "message_typed", {"message_length": len(message)})
        
        await asyncio.sleep(0.3)
        
        send_btn = await self._get_send_button()
        if send_btn:
            await send_btn.click()
        else:
            await self.page.keyboard.press("Enter")
        
        logger.log_browser_action(self.name, "message_sent", {})
        
        response = await self._wait_for_response(message_count_before, after_send=True)
        return response
    
    async def start_new_conversation(self):
        await self.ensure_browser()
        
        logger.log_browser_action(self.name, "start_new_conversation_attempt", {})
        
        new_chat_selectors = [
            'button:has-text("新对话")',
            'button:has-text("开启新对话")',
            'button:has-text("New Chat")',
            'div[role="button"]:has-text("新对话")',
            'div[role="button"]:has-text("New Chat")',
            '[class*="new-chat"] button',
            '[class*="newChat"] button',
            '[class*="new-chat"] div',
            '[class*="newChat"] div',
            'button[class*="primary"]',
            'div[class*="header"] button:first-child',
            'div[class*="sidebar"] button:first-child',
            'div[class*="nav"] button:first-child',
            'nav button:first-child',
            'header button:first-child',
            'button[aria-label*="new"]',
            'button[aria-label*="New"]',
            'a[href*="new"]',
            'svg[class*="plus"]',
            'svg[class*="Plus"]',
            'button:has(svg[class*="plus"])',
            'button:has(svg[class*="Plus"])',
            '[data-testid*="new-chat"]',
            'div:has-text("新对话"):not([class*="message"])',
            'div:has-text("New Chat"):not([class*="message"])',
        ]
        
        for selector in new_chat_selectors:
            try:
                el = await self.page.query_selector(selector)
                if el:
                    is_visible = await el.is_visible()
                    if is_visible:
                        await el.click()
                        await asyncio.sleep(2)
                        logger.log_browser_action(self.name, "new_conversation_started", {"selector": selector})
                        self.conversation_history = []
                        self.last_message_count = 0
                        self._request_count = 0
                        return True
            except Exception as e:
                logger.log_browser_action(self.name, "new_conversation_selector_failed", {
                    "selector": selector,
                    "error": str(e)
                })
                continue
        
        logger.log_browser_action(self.name, "new_conversation_not_found", {"tried_selectors": len(new_chat_selectors)})
        
        try:
            page_html = await self.page.content()
            logger.log_browser_action(self.name, "page_html_sample", {
                "html_length": len(page_html),
                "sample": page_html[:2000]
            })
        except Exception as e:
            logger.log_browser_action(self.name, "page_html_failed", {"error": str(e)})
        
        try:
            await self.page.goto(self.url)
            await asyncio.sleep(3)
            logger.log_browser_action(self.name, "page_reloaded", {})
            self.conversation_history = []
            self.last_message_count = 0
            self._request_count = 0
            return True
        except Exception as e:
            logger.log_error(self.name, "page_reload_failed", str(e))
            return False
    
    async def get_current_messages_count(self) -> int:
        try:
            message_selectors = [
                '[class*="message"]:not([class*="markdown"])',
                '[class*="chat-message"]',
                '[class*="conversation"][class*="item"]',
                '[data-testid*="message"]',
                'div[class*="message"]',
                'div[class*="chat"]',
                'div[class*="conversation"]',
                'div[role="listitem"]',
                'div[class*="markdown"]',  # 直接统计markdown元素
            ]
            counts = []
            for selector in message_selectors:
                try:
                    elements = await self.page.query_selector_all(selector)
                    if elements:
                        counts.append(len(elements))
                except Exception:
                    continue
            
            # 返回最常见的计数，避免单个选择器失效
            if counts:
                from collections import Counter
                counter = Counter(counts)
                return counter.most_common(1)[0][0]
            return 0
        except Exception:
            return 0
    
    def _is_browser_closed_error(self, error: Exception) -> bool:
        error_str = str(error).lower()
        return any([
            "target closed" in error_str,
            "browser has been closed" in error_str,
            "context has been closed" in error_str,
            "page has been closed" in error_str,
            "disconnected" in error_str,
        ])
    
    async def _wait_for_response(self, message_count_before: int = 0, timeout: int = 120, after_send: bool = False) -> str:
        await asyncio.sleep(2)
        
        start_time = datetime.now()
        last_response = ""
        stable_count = 0
        message_count_stable = 0
        last_message_count = message_count_before
        
        response_selectors = [
            '.markdown-body',
            '.message-content',
            '.prose',
            '[class*="response"]',
            '[class*="assistant"]',
            '[class*="answer"]',
            'div[class*="markdown"]',
        ]
        
        initial_response_count = 0
        initial_last_response = ""
        for selector in response_selectors:
            try:
                elements = await self.page.query_selector_all(selector)
                if elements and len(elements) > initial_response_count:
                    initial_response_count = len(elements)
                    last_el = elements[-1]
                    initial_last_response = await last_el.inner_text()
            except Exception:
                pass
        
        logger.log_browser_action(self.name, "response_wait_start", {
            "message_count_before": message_count_before,
            "initial_response_count": initial_response_count,
            "after_send": after_send,
            "timeout": timeout
        })
        
        while (datetime.now() - start_time).seconds < timeout:
            try:
                if not await self.is_browser_alive():
                    logger.log_error(self.name, "browser_died_during_response", "Browser is no longer alive")
                    raise Exception("Browser crashed during response generation")
                
                current_message_count = await self.get_current_messages_count()
                
                logger.log_browser_action(self.name, "message_count_check", {
                    "current": current_message_count,
                    "before": message_count_before,
                    "elapsed": (datetime.now() - start_time).seconds
                })
                
                # 如果消息计数长时间不变，可能是检测失败，尝试直接提取文本
                if current_message_count <= message_count_before:
                    if (datetime.now() - start_time).seconds > 30:  # 30秒后强制尝试提取
                        logger.log_browser_action(self.name, "force_extract_after_timeout", {
                            "elapsed": (datetime.now() - start_time).seconds
                        })
                        # 直接尝试提取最新响应，不等待消息计数变化
                        try:
                            elements = await self.page.query_selector_all('div[class*="markdown"]')
                            if elements:
                                last_element = elements[-1]
                                text = await last_element.inner_text()
                                if text and len(text) > 50:  # 有足够长的文本
                                    return text
                        except Exception:
                            pass
                    
                    await asyncio.sleep(0.3)
                    continue
                
                if current_message_count == last_message_count:
                    message_count_stable += 1
                else:
                    message_count_stable = 0
                    last_message_count = current_message_count
                
                if message_count_stable < 1:
                    await asyncio.sleep(0.3)
                    continue
                
                for selector in response_selectors:
                    try:
                        elements = await self.page.query_selector_all(selector)
                        if elements and len(elements) > 0:
                            current_element_count = len(elements)
                            
                            logger.log_browser_action(self.name, "elements_found", {
                                "selector": selector,
                                "count": current_element_count,
                                "initial_response_count": initial_response_count
                            })
                            
                            last_element = elements[-1]
                            text = await last_element.inner_text()
                            
                            if not text:
                                continue
                            
                            text = text.replace('：', ':').replace('，', ',').replace('"', '"').replace('"', '"').replace('【', '[').replace('】', ']')
                            
                            if current_element_count <= initial_response_count:
                                if text != initial_last_response and len(text) > len(initial_last_response):
                                    logger.log_browser_action(self.name, "content_changed_same_element", {
                                        "old_length": len(initial_last_response),
                                        "new_length": len(text)
                                    })
                                else:
                                    if text == last_response:
                                        stable_count += 1
                                        logger.log_browser_action(self.name, "response_stable", {
                                            "stable_count": stable_count,
                                            "text_length": len(text)
                                        })
                                    continue
                            
                            logger.log_browser_action(self.name, "text_extracted", {
                                "selector": selector,
                                "text_length": len(text),
                                "text_preview": text[:100] if len(text) > 100 else text
                            })
                            
                            if text and '"tool_calls"' in text:
                                if text.rstrip().endswith('}') or text.rstrip().endswith('```'):
                                    open_braces = text.count('{')
                                    close_braces = text.count('}')
                                    if open_braces == close_braces:
                                        logger.log_browser_action(self.name, "tool_call_response_detected", {
                                            "response_length": len(text),
                                            "braces_balanced": True
                                        })
                                        return text
                                    else:
                                        logger.log_browser_action(self.name, "tool_call_response_incomplete", {
                                            "response_length": len(text),
                                            "open_braces": open_braces,
                                            "close_braces": close_braces
                                        })
                            
                            if text and len(text) > len(last_response):
                                last_response = text
                                stable_count = 0
                            elif text and text == last_response:
                                stable_count += 1
                                logger.log_browser_action(self.name, "response_stable", {
                                    "stable_count": stable_count,
                                    "text_length": len(text)
                                })
                    except Exception as e:
                        if self._is_browser_closed_error(e):
                            logger.log_error(self.name, "browser_crashed_during_response", str(e))
                            raise Exception("Browser crashed during response generation")
                        logger.log_browser_action(self.name, "selector_error", {
                            "selector": selector,
                            "error": str(e)
                        })
                        continue
                
                if last_response and stable_count >= 2:
                    logger.log_browser_action(self.name, "response_complete", {
                        "response_length": len(last_response),
                        "message_count": current_message_count
                    })
                    return last_response
                
                await asyncio.sleep(0.5)
                
            except Exception as e:
                if self._is_browser_closed_error(e):
                    logger.log_error(self.name, "browser_crashed_outer", str(e))
                    raise Exception("Browser crashed during response generation")
                logger.log_error(self.name, "response_wait_error", str(e))
                await asyncio.sleep(0.5)
        
        if last_response:
            return last_response
        
        raise Exception("Response timeout")
    
    async def send_message_stream(self, message: str) -> AsyncGenerator[str, None]:
        await self.ensure_browser()
        
        if self._request_count >= self.MAX_REQUESTS_PER_CONVERSATION:
            logger.log_browser_action(self.name, "auto_new_conversation_stream", {
                "request_count": self._request_count,
                "max_requests": self.MAX_REQUESTS_PER_CONVERSATION
            })
            
            new_conv_success = await self.start_new_conversation()
            
            if new_conv_success:
                logger.log_browser_action(self.name, "waiting_for_new_conversation_ready_stream", {})
                await asyncio.sleep(3)
                
                for attempt in range(10):
                    input_el = await self._get_input_element()
                    if input_el:
                        is_visible = await input_el.is_visible()
                        if is_visible:
                            break
                    await asyncio.sleep(0.5)
                else:
                    logger.log_browser_action(self.name, "input_not_ready_after_new_conversation_stream", {})
                    raise Exception("Input element not ready after new conversation")
            else:
                logger.log_browser_action(self.name, "new_conversation_failed_continuing_stream", {})
                await asyncio.sleep(2)
            
            self._request_count = 0
        
        self._request_count += 1
        logger.log_browser_action(self.name, "request_count_incremented_stream", {
            "current_count": self._request_count,
            "max_requests": self.MAX_REQUESTS_PER_CONVERSATION
        })
        
        message_count_before = await self.get_current_messages_count()
        
        input_el = await self._get_input_element()
        if not input_el:
            logger.log_error(self.name, "input_not_found", "Cannot find input element")
            raise Exception("Cannot find input element")
        
        tag_name = await input_el.evaluate("el => el.tagName.toLowerCase()")
        
        fill_timeout = max(60000, len(message) * 2)
        
        if tag_name == "textarea":
            await input_el.fill(message, timeout=fill_timeout)
        else:
            await input_el.click()
            await asyncio.sleep(0.2)
            await self.page.keyboard.type(message, delay=10)
        
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
                if not await self.is_browser_alive():
                    logger.log_error(self.name, "browser_died_during_stream", "Browser is no longer alive")
                    return
                
                response_selectors = [
                    '.markdown-body',
                    '.message-content',
                    '[data-testid="response"]',
                    '.prose',
                ]
                
                for selector in response_selectors:
                    elements = await self.page.query_selector_all(selector)
                    if elements and len(elements) > message_count_before:
                        last_element = elements[-1]
                        text = await last_element.inner_text()
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
                if self._is_browser_closed_error(e):
                    logger.log_error(self.name, "browser_crashed_during_stream", str(e))
                    return
                logger.logger.error(f"[{self.name}] Stream loop error: {e}")
                await asyncio.sleep(0.3)
        
        if last_text:
            yield last_text
