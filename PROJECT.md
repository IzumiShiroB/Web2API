# AI Web Proxy API Server

## 项目目标

创建一个API代理服务器（`http://localhost:23456`），将API请求转发到网页端AI模型平台，并将Web端响应转换为标准OpenAI API格式返回给外部应用。

### 核心功能
1. 接收外部应用的API请求
2. 通过Playwright自动化浏览器与Web端AI平台交互
3. 捕获Web端响应并转换为OpenAI兼容格式
4. 支持多平台扩展（DeepSeek、Doubao）
5. 支持工具调用（Tool Calling）
6. 支持多轮对话
7. 支持图片输入（base64转剪贴板粘贴）
8. 进程间状态同步（启动器与服务器）
9. 支持自定义系统提示词和人格设定

## 项目架构

```
w2a/
├── main.py              # 入口文件，启动Uvicorn服务器
├── api_server.py        # 核心API服务器，请求处理和响应格式化
├── launcher.py          # GUI启动器，控制服务器启停
├── server_state.py      # 进程间状态共享模块
├── start.bat            # Windows批处理启动脚本
├── logger.py            # 日志系统，对话追踪和自动清理
├── platforms/
│   ├── base.py          # 平台基类接口定义
│   ├── deepseek.py      # DeepSeek平台实现
│   └── doubao.py        # 豆包平台实现
├── logs/                # 日志目录（按平台和日期组织）
│   ├── deepseek/
│   │   └── YYYYMMDD.jsonl
│   └── doubao/
│       └── YYYYMMDD.jsonl
├── browser_data/        # 浏览器持久化数据（登录状态）
└── server_state.json    # 服务器状态文件（进程间通信）
```

### 技术栈
- **FastAPI**: API服务器框架
- **Playwright**: 浏览器自动化
- **Pydantic**: 数据模型和验证
- **Uvicorn**: ASGI服务器
- **Tkinter**: GUI启动器

## 支持平台

| 平台 | 网址 | 状态 | 特性 |
|------|------|------|------|
| DeepSeek | chat.deepseek.com | 稳定 | 文本、工具调用 |
| Doubao | doubao.com/chat | 稳定 | 文本、图片输入 |

## API端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/v1/models` | GET | 返回模型列表 |
| `/v1/chat/completions` | POST | 聊天补全接口 |
| `/health` | GET | 健康检查 |

### 响应格式
完全兼容OpenAI Chat Completion API格式：
- 普通文本响应：`content`字段包含文本
- 工具调用响应：`content`为`null`，`tool_calls`包含调用信息
- 图片输入：支持base64格式，自动转换为剪贴板粘贴

### 系统提示词
API服务器会自动合并基础系统提示词和用户自定义系统提示词：
- 基础提示词：定义JSON响应格式、环境信息、工具调用规则
- 用户提示词：定义AI人格、语气、行为规范
- 合并后发送到Web端，确保AI行为符合预期

**工具调用提醒机制**：
- 每个用户消息结尾自动添加工具调用提醒
- 使用分隔线明确区分用户对话和系统提示
- 避免模型因长对话遗忘工具调用规则

## 日志系统

### 日志格式
每条日志为JSON Lines格式，包含以下事件类型：

| 事件 | 说明 |
|------|------|
| `api_request_received` | 收到API请求 |
| `forwarded_to_web` | 内容转发到Web端 |
| `web_response_received` | Web端响应 |
| `api_response_sent` | API响应发送 |
| `browser_action` | 浏览器操作记录 |
| `error` | 错误记录 |

### 日志特性
1. **完整记录**：每轮对话记录完整链路
2. **按平台分离**：不同平台使用独立日志目录
3. **按日期组织**：每天一个日志文件
4. **自动清理**：保留最近10天的日志

## 进程间通信

### 状态文件机制
`server_state.json` 用于启动器与API服务器之间的状态同步：

| 字段 | 说明 |
|------|------|
| `running` | 服务器是否运行中 |
| `pid` | 服务器进程ID |
| `platform` | 当前平台名称 |
| `shutdown_requested` | 是否请求关闭 |

### 关闭流程
1. **关闭浏览器** → API服务器检测到 → 写入stopped状态 → 启动器更新UI
2. **关闭启动器** → 写入shutdown_requested → API服务器检测到 → 关闭服务器

## 启动方式

1. **GUI启动器**：双击`start.bat`或运行`launcher.py`
2. **命令行**：`python main.py --platform doubao`
3. **指定平台**：在启动器下拉菜单中选择

## 注意事项

1. **登录状态**：浏览器数据保存在`browser_data/`目录，删除会导致登录状态丢失

2. **并发控制**：请求队列机制确保请求按时间戳顺序处理，支持并行处理（最多3个并发请求）

3. **图片输入**：base64图片先保存为临时文件，再通过剪贴板粘贴到对话框

4. **JSON处理**：发送到Web端时会移除base64图片数据，减少传输量

5. **表情标签**：Web端返回的`&&emoji&&`格式会自动转换为实际表情符号

6. **工具调用解析**：自动修复Web端返回的嵌套JSON arguments格式问题

7. **响应检测**：支持检测响应元素数量变化和内容变化，确保正确获取新响应

8. **强制提取机制**：30秒后强制尝试提取最新响应，避免无限等待超时

9. **自动新建对话**：DeepSeek平台每5次API请求后自动点击"开始新对话"，避免上下文过长导致性能下降

10. **精简系统提示词**：优化JSON示例格式，减少不必要的字段描述，降低token消耗

## 调试指南

### 检查服务器日志
```powershell
Get-Content logs\doubao\YYYYMMDD.jsonl -Tail 20
```

### 检查端口占用
```powershell
netstat -ano | findstr :23456
```

### 检查服务器状态
```powershell
Get-Content server_state.json
```

## 版本历史

详见 [CHANGELOG.md](CHANGELOG.md)

### 当前版本：v0.2.3

**主要变更**：
- 修复浏览器响应超时问题（增强消息计数检测、添加强制提取机制）
- 修复消息队列阻塞问题（从串行改为并行处理）
- 增强系统提示词（工具调用能力说明、强制规则、用户消息提醒）
- 优化响应检测速度（降低阈值、缩短间隔）
- 修复日志错误影响响应问题（添加异常处理）
- 精简系统提示词（优化JSON示例格式，降低token消耗）
- DeepSeek自动新建对话（每5次请求后自动点击"开始新对话"）

**与v0.2.2的差异**：
- **响应检测**：从单一选择器改为多选择器+Counter统计，增加强制提取机制
- **队列处理**：从串行处理改为并行处理（最多3个并发）
- **系统提示词**：新增【你的能力】、【强制规则】部分，用户消息结尾自动添加提醒，精简JSON示例
- **性能优化**：稳定性阈值从>3改为>=2，检测间隔从0.5秒改为0.3秒
- **容错性**：日志错误不再影响响应发送
- **自动新建对话**：DeepSeek平台每5次请求后自动新建对话，避免上下文过长
