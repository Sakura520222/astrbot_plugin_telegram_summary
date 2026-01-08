## [v1.0.0] - 2026-01-08

### 功能特性
- 每周一自动生成指定Telegram频道的消息汇总报告
- 支持手动触发生成汇总报告（`/summary`命令）
- 支持指定特定频道生成汇总（`/summary 频道名`或`/summary 频道URL`）
- 支持查看当前提示词（`/showprompt`命令）
- 支持设置自定义提示词（`/setprompt`命令）
- 支持查看当前频道列表（`/showchannels`命令）
- 支持添加频道（`/addchannel 频道URL`命令）
- 支持删除频道（`/deletechannel 频道URL`命令）
- 使用AstrBot框架的配置系统管理配置
- 使用AstrBot框架的AI调用机制生成汇总
- 使用AstrBot框架的权限检查机制
- 支持多频道消息抓取和汇总
- 自动生成包含消息链接的汇总报告
- 支持灵活的配置选项

### 技术实现
- 基于Telethon库与Telegram API交互
- 使用APScheduler实现定时任务
- 支持从配置文件加载和保存配置
- 完善的日志记录系统
- 支持异常处理和错误恢复

