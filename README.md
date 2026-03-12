# Web2API

将 Web 端 AI 模型转换为 OpenAI API 格式的代理服务。

## 项目简介

Web2API 通过 Playwright 浏览器自动化技术，将 DeepSeek、豆包等 Web 端 AI 平台的响应转换为标准 OpenAI API 格式，支持 OpenAI SDK 直接调用。

## 功能特性

- **OpenAI 格式兼容**：完全兼容 OpenAI Chat Completion API 格式
- **工具调用**：支持 Function Calling / Tool Calling
- **多轮对话**：支持上下文连续对话
- **图片输入**：支持 base64 图片自动转为剪贴板粘贴
- **系统提示词**：支持自定义系统提示词和人格设定
- **GUI 启动器**：提供图形界面管理服务启停

## 支持环境

目前项目仅支持Windows环境。

## 支持平台

| 平台     | 状态   |
| -------- | ------ |
| DeepSeek | 稳定   |
| 豆包     | 开发中 |

## 快速开始

### 安装依赖

```bash
pip install -r requirements.txt
```

### 启动服务

```bash
python launcher.py
```

或使用批处理脚本：

```bash
start.bat
```

服务启动后，API 服务默认运行在 `http://localhost:23456`

### API 端点

| 端点                     | 方法 | 说明         |
| ------------------------ | ---- | ------------ |
| `/v1/models`           | GET  | 获取模型列表 |
| `/v1/chat/completions` | POST | 聊天补全     |
| `/health`              | GET  | 健康检查     |

### 调用示例

```python
from openai import OpenAI

client = OpenAI(
    api_key="任意字符串",
    base_url="http://localhost:23456/v1"
)

response = client.chat.completions.create(
    model="deepseek",
    messages=[
        {"role": "user", "content": "你好，请介绍一下自己"}
    ]
)

print(response.choices[0].message.content)
```

## 技术栈

- FastAPI - API 服务框架
- Playwright - 浏览器自动化
- Pydantic - 数据模型验证
- Uvicorn - ASGI 服务器
- Tkinter - GUI 启动器

## 项目结构

```
w2a/
├── main.py              # 入口文件
├── api_server.py        # 核心 API 服务器
├── launcher.py          # GUI 启动器
├── server_state.py      # 进程间状态同步
├── logger.py            # 日志系统
├── platforms/           # 平台实现
│   ├── base.py
│   ├── deepseek.py
│   └── doubao.py
└── browser_data/        # 浏览器持久化数据
```

## 免责声明

**本项目仅供学习交流使用，禁止用于任何商业目的。**

1. 本项目仅作为技术研究和学习之用，旨在帮助开发者理解 API 代理和浏览器自动化的原理。
2. 使用本项目时，请务必遵守相关平台的服务条款和用户协议。
3. 使用者的任何行为与本项目开发者无关，使用者需自行承担一切后果和责任。
4. 本项目开发者不对任何因使用本项目导致的直接或间接损失负责。
5. 请合理合法地使用本项目，不要对目标平台造成不必要的负载或影响其正常服务。

## 许可证

本项目仅供学习交流，**禁止用于商业用途**。
