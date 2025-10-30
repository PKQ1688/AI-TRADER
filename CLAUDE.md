# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

这是一个基于AI的金融交易系统，使用现代Python架构设计。系统通过AI代理分析技术指标并生成交易信号。

## 核心架构

### 模块结构
- **agents/**: AI交易代理，使用Agno框架集成DeepSeek/OpenAI模型
- **data/**: 数据网关，通过CCXT库连接多个交易所
- **indicators/**: 技术指标计算（当前支持MACD）
- **orchestrator/**: 流程编排，负责端到端信号生成
- **config/**: 配置管理，支持环境变量和参数覆盖
- **core/logging/**: 基于Rich的统一日志系统

### 设计模式
- **协议导向**: 数据网关使用Protocol接口，便于扩展新交易所
- **依赖注入**: 模块间通过构造函数注入依赖，降低耦合
- **工具化AI**: AI代理通过工具调用获取技术指标数据

## 常用开发命令

### 环境管理
```bash
# 安装依赖
uv sync

# 激活虚拟环境
source .venv/bin/activate  # Linux/Mac
# 或
.venv\Scripts\activate     # Windows
```

### 代码质量
```bash
# 代码检查
uv run ruff check src

# 代码格式化
uv run ruff format src
```

### 运行示例
```bash
# 运行基础信号示例
uv run python examples/basic_signal.py
```

### 构建
```bash
# 构建包
uv build
```

## 配置管理

### 环境变量
系统使用`.env`文件管理敏感配置：
```
# AI模型配置
DEEPSEEK_API_KEY=your_api_key_here
DEEPSEEK_BASE_URL=https://api.deepseek.com
AI_TRADER_MODEL=deepseek-chat

# 或者使用OpenAI
OPENAI_API_KEY=your_openai_key
OPENAI_BASE_URL=https://api.openai.com
```

### 默认参数
- 交易所: binance
- 交易对: BTC/USDT
- 时间周期: 4h
- K线数量: 200
- AI模型: deepseek-chat

## 关键API

### 核心函数
```python
from ai_trader import load_settings, run_once, create_trading_agent

# 加载配置
settings = load_settings(symbol="BTC/USDT", timeframe="1h")

# 执行一次交易信号生成
result = run_once(settings)

# 创建交易代理（高级用法）
agent = create_trading_agent(settings, gateway)
```

### 数据结构
- `Settings`: 运行时配置快照（dataclass，不可变）
- `RunOutput`: Agno框架的响应结构
- `Dict[str, Any]`: 标准化的信号输出格式

## 开发最佳实践

### 添加新技术指标
1. 在`indicators/`目录创建新模块
2. 实现计算逻辑和工具函数接口
3. 在`agents/trading_agent.py`中注册工具
4. 更新示例代码

### 扩展数据源
1. 在`data/`目录实现新的Gateway类
2. 继承现有Protocol接口
3. 添加配置参数支持
4. 更新测试用例

### 错误处理
- 使用统一的日志系统（`core/logging`）
- 实现优雅的异常处理和重试机制
- 记录详细的错误上下文信息

## 技术栈

- **Python 3.12+**: 现代Python特性支持
- **Agno**: AI代理框架，支持工具调用
- **CCXT**: 多交易所数据接口
- **Rich**: 美化的终端输出
- **Pydantic**: 数据验证和序列化
- **Ruff**: 快速代码检查和格式化

## 项目状态

- ✅ 核心架构完整
- ✅ MACD技术指标实现
- ✅ DeepSeek/OpenAI集成
- ⚠️ 需要添加测试覆盖
- ⚠️ 可扩展更多技术指标
- ⚠️ 可添加回测功能