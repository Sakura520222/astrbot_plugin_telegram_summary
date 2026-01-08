# Telegram 频道消息汇总插件

本插件转自我的另一个项目：[Sakura-Channel-Summary-Assistant](https://github.com/Sakura520222/Sakura-Channel-Summary-Assistant)

一个用于 AstrBot 的 Telegram 频道消息汇总插件，每周一生成指定频道的消息汇总报告，帮助您快速了解频道内的重要动态。

## 功能特性

- **自动周报**：每周一早上 9 点自动生成指定频道的消息汇总
- **AI 总结**：使用 AI 模型提取核心要点并整理重要消息
- **灵活配置**：支持配置多个频道、自定义提示词和 AI 参数
- **多频道支持**：同时监控多个 Telegram 频道
- **命令控制**：支持通过命令手动触发汇总、管理频道和配置参数

## 安装

1. 克隆或下载插件到 AstrBot 的插件目录
2. 安装依赖：
   ```bash
   pip install -r requirements.txt
   ```
3. 在 AstrBot 中启用该插件

## 配置

### 环境变量配置

您可以通过 `.env` 文件配置以下环境变量：

```env
# Telegram 配置
TELEGRAM_API_ID=your_api_id
TELEGRAM_API_HASH=your_api_hash
TELEGRAM_BOT_TOKEN=your_bot_token

# AI 配置
LLM_API_KEY=your_llm_api_key
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-chat

# 频道配置
TARGET_CHANNEL=https://t.me/example1,https://t.me/example2

# 管理员配置
REPORT_ADMIN_IDS=123456789,987654321
```

### AstrBot 配置系统

您也可以通过 AstrBot 的配置系统进行配置，配置项包括：

- `telegram.api_id`: Telegram API ID
- `telegram.api_hash`: Telegram API Hash
- `telegram.bot_token`: Telegram Bot Token
- `ai.api_key`: AI 模型 API Key
- `ai.base_url`: AI 模型 API 地址
- `ai.model`: AI 模型名称
- `channels`: 要监控的频道列表
- `admin_ids`: 管理员 ID 列表
- `prompt`: 自定义提示词

## 使用说明

### 自动汇总

插件会在每周一早上 9 点自动生成所有配置频道的消息汇总，并发送给管理员。

### 手动触发

您可以通过以下命令手动触发汇总：

```
/summary          # 生成所有频道的汇总
/summary example  # 只生成指定频道的汇总（需提供频道名称）
```

## 命令列表

| 命令 | 描述 | 权限 |
|------|------|------|
| `/summary [channel]` | 立即生成本周频道消息汇总，可指定频道 | 管理员 |
| `/showprompt` | 查看当前使用的提示词 | 管理员 |
| `/setprompt` | 设置自定义提示词 | 管理员 |
| `/showaicfg` | 查看当前 AI 配置 | 管理员 |
| `/setaicfg` | 设置 AI 配置（API Key、Base URL、Model） | 管理员 |
| `/showchannels` | 查看当前配置的频道列表 | 管理员 |
| `/addchannel <url>` | 添加新的监控频道 | 管理员 |
| `/deletechannel <url>` | 删除监控频道 | 管理员 |

## 工作原理

1. **定时任务**：使用 APScheduler 每周一早上 9 点触发汇总任务
2. **消息抓取**：通过 Telethon 库抓取过去一周指定频道的所有文本消息
3. **AI 分析**：将抓取的消息发送给 AI 模型进行汇总分析
4. **报告发送**：将分析结果分段发送给配置的管理员

## 依赖

- telethon: Telegram API 客户端库
- python-dotenv: 环境变量加载库
- openai: OpenAI API 客户端库
- apscheduler: 任务调度库

## 注意事项

1. 确保您的 Telegram Bot 已获得访问目标频道的权限
2. 建议使用 DeepSeek 或其他支持长文本处理的 AI 模型
3. 首次使用时，建议先通过 `/summary` 命令手动测试，确保配置正确
4. 汇总报告可能会根据消息量和 AI 模型的不同而有所延迟

## 日志

插件使用 AstrBot 提供的日志接口，您可以在 AstrBot 的日志中查看插件的运行状态和错误信息。

## 开发者

- 作者：Sakura520222
- 项目地址：https://github.com/Sakura520222/astrbot_plugin_telegram_summary

## 许可证

MIT License
