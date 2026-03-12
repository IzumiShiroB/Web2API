import json
import os
from datetime import datetime, timedelta
from typing import Optional, Any, Dict, List
from pathlib import Path
import logging


class ConversationLogger:
    def __init__(self, log_dir: str = "logs", max_days: int = 10):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.max_days = max_days
        self.platform_loggers: Dict[str, logging.Logger] = {}
        self._setup_main_logger()
        self._cleanup_old_logs()
    
    def _setup_main_logger(self):
        self.main_logger = logging.getLogger("api_proxy")
        self.main_logger.setLevel(logging.DEBUG)
        
        if not self.main_logger.handlers:
            console_handler = logging.StreamHandler()
            console_handler.setLevel(logging.INFO)
            console_format = logging.Formatter(
                "%(asctime)s | %(levelname)s | %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S"
            )
            console_handler.setFormatter(console_format)
            self.main_logger.addHandler(console_handler)
    
    def _get_platform_logger(self, platform: str) -> logging.Logger:
        if platform not in self.platform_loggers:
            platform_dir = self.log_dir / platform.lower()
            platform_dir.mkdir(parents=True, exist_ok=True)
            
            logger = logging.getLogger(f"{platform}_logger")
            logger.setLevel(logging.DEBUG)
            
            file_handler = logging.FileHandler(
                platform_dir / f"{datetime.now().strftime('%Y%m%d')}.log",
                encoding="utf-8"
            )
            file_handler.setLevel(logging.DEBUG)
            file_format = logging.Formatter(
                "%(asctime)s | %(levelname)s | %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S"
            )
            file_handler.setFormatter(file_format)
            logger.addHandler(file_handler)
            
            self.platform_loggers[platform] = logger
        return self.platform_loggers[platform]
    
    def _cleanup_old_logs(self):
        cutoff_date = datetime.now() - timedelta(days=self.max_days)
        
        for platform_dir in self.log_dir.iterdir():
            if platform_dir.is_dir():
                for log_file in platform_dir.glob("*.jsonl"):
                    try:
                        file_date_str = log_file.stem
                        file_date = datetime.strptime(file_date_str, "%Y%m%d")
                        if file_date < cutoff_date:
                            log_file.unlink()
                            self.main_logger.info(f"Cleaned up old log: {log_file}")
                    except ValueError:
                        pass
    
    def _get_conversation_log_file(self, platform: str) -> Path:
        platform_dir = self.log_dir / platform.lower()
        platform_dir.mkdir(parents=True, exist_ok=True)
        return platform_dir / f"{datetime.now().strftime('%Y%m%d')}.jsonl"
    
    def start_conversation(
        self,
        platform: str,
        request_id: str,
        api_request: dict,
    ) -> str:
        conversation_id = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{request_id}"
        
        log_entry = {
            "conversation_id": conversation_id,
            "timestamp": datetime.now().isoformat(),
            "event": "api_request_received",
            "platform": platform,
            "request_id": request_id,
            "api_request": api_request,
        }
        
        self._write_conversation_log(platform, log_entry)
        self.main_logger.info(f"[{platform}] Request {request_id}: {json.dumps(api_request, ensure_ascii=False)[:200]}")
        
        return conversation_id
    
    def log_forwarded_to_web(
        self,
        platform: str,
        conversation_id: str,
        forwarded_content: str,
    ):
        log_entry = {
            "conversation_id": conversation_id,
            "timestamp": datetime.now().isoformat(),
            "event": "forwarded_to_web",
            "platform": platform,
            "forwarded_content": forwarded_content,
        }
        
        self._write_conversation_log(platform, log_entry)
        self.main_logger.debug(f"[{platform}] Forwarded to web: {forwarded_content[:200]}...")
    
    def log_web_response(
        self,
        platform: str,
        conversation_id: str,
        web_response: str,
    ):
        log_entry = {
            "conversation_id": conversation_id,
            "timestamp": datetime.now().isoformat(),
            "event": "web_response_received",
            "platform": platform,
            "web_response": web_response,
        }
        
        self._write_conversation_log(platform, log_entry)
        self.main_logger.debug(f"[{platform}] Web response: {web_response[:200]}...")
    
    def log_api_response(
        self,
        platform: str,
        conversation_id: str,
        api_response: dict,
    ):
        log_entry = {
            "conversation_id": conversation_id,
            "timestamp": datetime.now().isoformat(),
            "event": "api_response_sent",
            "platform": platform,
            "api_response": api_response,
        }
        
        self._write_conversation_log(platform, log_entry)
        self.main_logger.info(f"[{platform}] Response sent: {json.dumps(api_response, ensure_ascii=False)[:200]}")
    
    def log_error(
        self,
        platform: str,
        conversation_id: str,
        error: str,
        details: Optional[dict] = None,
    ):
        log_entry = {
            "conversation_id": conversation_id,
            "timestamp": datetime.now().isoformat(),
            "event": "error",
            "platform": platform,
            "error": error,
            "details": details or {},
        }
        
        self._write_conversation_log(platform, log_entry)
        self.main_logger.error(f"[{platform}] Error: {error}")
    
    def log_debug(
        self,
        platform: str,
        message: str,
    ):
        self.main_logger.debug(f"[{platform}] {message}")
    
    def log_info(
        self,
        platform: str,
        message: str,
    ):
        self.main_logger.info(f"[{platform}] {message}")
    
    def log_browser_action(
        self,
        platform: str,
        action: str,
        details: Optional[dict] = None,
    ):
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "event": "browser_action",
            "platform": platform,
            "action": action,
            "details": details or {},
        }
        self._write_conversation_log(platform, log_entry)
        self.main_logger.debug(f"[{platform}] Browser: {action}")
    
    def _write_conversation_log(self, platform: str, log_entry: dict):
        log_file = self._get_conversation_log_file(platform)
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")
    
    def read_conversation(
        self,
        platform: str,
        conversation_id: str,
        date: Optional[str] = None,
    ) -> List[dict]:
        if date:
            log_file = self.log_dir / platform.lower() / f"{date}.jsonl"
        else:
            parts = conversation_id.split("_")
            if len(parts) >= 1:
                date = parts[0]
                log_file = self.log_dir / platform.lower() / f"{date}.jsonl"
            else:
                return []
        
        if not log_file.exists():
            return []
        
        conversation_events = []
        with open(log_file, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line.strip())
                    if entry.get("conversation_id") == conversation_id:
                        conversation_events.append(entry)
                except json.JSONDecodeError:
                    continue
        
        return conversation_events
    
    def list_conversations(
        self,
        platform: str,
        date: Optional[str] = None,
    ) -> List[str]:
        if date:
            log_files = [self.log_dir / platform.lower() / f"{date}.jsonl"]
        else:
            platform_dir = self.log_dir / platform.lower()
            log_files = list(platform_dir.glob("*.jsonl"))
        
        conversation_ids = set()
        for log_file in log_files:
            if log_file.exists():
                with open(log_file, "r", encoding="utf-8") as f:
                    for line in f:
                        try:
                            entry = json.loads(line.strip())
                            if "conversation_id" in entry:
                                conversation_ids.add(entry["conversation_id"])
                        except json.JSONDecodeError:
                            continue
        
        return sorted(list(conversation_ids), reverse=True)


logger = ConversationLogger()
