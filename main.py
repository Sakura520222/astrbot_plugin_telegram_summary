import os
import asyncio
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from telethon import TelegramClient
from openai import OpenAI, AsyncOpenAI
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# AstrBot 插件 API
from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register
from astrbot.api import logger # 使用 astrbot 提供的 logger 接口
from astrbot.api import AstrBotConfig # 使用 astrbot 提供的配置接口

# 加载 .env 文件中的变量
load_dotenv()
logger.info("已加载 .env 文件中的环境变量")

@register("telegram_summary", "author", "一个 Telegram 频道消息汇总插件，每周一生成指定频道的消息汇总报告。", "1.0.0", "repo url")
class TelegramSummaryPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        
        # 配置文件
        self.PROMPT_FILE = "prompt.txt"
        self.CONFIG_FILE = "config.json"
        self.RESTART_FLAG_FILE = ".restart_flag"
        logger.debug(f"配置文件路径: 提示词文件={self.PROMPT_FILE}, 配置文件={self.CONFIG_FILE}")
        
        # 默认提示词
        self.DEFAULT_PROMPT = "请总结以下 Telegram 消息，提取核心要点并列出重要消息的链接：\n\n"
        
        # 从 AstrBot 配置系统读取配置
        logger.info("开始从 AstrBot 配置系统加载配置...")
        
        # Telegram 配置
        telegram_config = config.get('telegram', {})
        self.API_ID = telegram_config.get('api_id', os.getenv('TELEGRAM_API_ID'))
        self.API_HASH = telegram_config.get('api_hash', os.getenv('TELEGRAM_API_HASH'))
        self.BOT_TOKEN = telegram_config.get('bot_token', os.getenv('TELEGRAM_BOT_TOKEN'))
        
        # AI 配置
        ai_config = config.get('ai', {})
        self.LLM_API_KEY = ai_config.get('api_key', os.getenv('LLM_API_KEY', os.getenv('DEEPSEEK_API_KEY')))
        self.LLM_BASE_URL = ai_config.get('base_url', os.getenv('LLM_BASE_URL', 'https://api.deepseek.com'))
        self.LLM_MODEL = ai_config.get('model', os.getenv('LLM_MODEL', 'deepseek-chat'))
        
        # 频道配置
        self.CHANNELS = config.get('channels', [])
        if not self.CHANNELS:
            # 从环境变量获取默认值
            TARGET_CHANNEL = os.getenv('TARGET_CHANNEL')
            if TARGET_CHANNEL:
                # 支持多个频道，用逗号分隔
                self.CHANNELS = [channel.strip() for channel in TARGET_CHANNEL.split(',')]
                logger.info(f"已从环境变量加载频道配置: {self.CHANNELS}")
        else:
            logger.info(f"已从 AstrBot 配置加载频道列表: {self.CHANNELS}")
        
        # 管理员 ID 列表
        admin_ids = config.get('admin_ids', [])
        if admin_ids:
            self.ADMIN_LIST = [int(admin_id) for admin_id in admin_ids]
            logger.info(f"已从 AstrBot 配置加载管理员ID列表: {self.ADMIN_LIST}")
        else:
            # 从环境变量获取默认值
            REPORT_ADMIN_IDS = os.getenv('REPORT_ADMIN_IDS', '')
            logger.debug(f"从环境变量读取的管理员ID: {REPORT_ADMIN_IDS}")
            if REPORT_ADMIN_IDS:
                self.ADMIN_LIST = [int(admin_id.strip()) for admin_id in REPORT_ADMIN_IDS.split(',')]
                logger.info(f"已从环境变量加载管理员ID列表: {self.ADMIN_LIST}")
            else:
                # 如果没有配置管理员ID，默认发送给自己
                self.ADMIN_LIST = ['me']
                logger.info("未配置管理员ID，默认发送给机器人所有者")
        
        # 提示词配置
        self.CURRENT_PROMPT = config.get('prompt', self.DEFAULT_PROMPT)
        logger.info("已加载提示词配置")
        logger.debug(f"当前提示词: {self.CURRENT_PROMPT[:100]}..." if len(self.CURRENT_PROMPT) > 100 else f"当前提示词: {self.CURRENT_PROMPT}")
        
        # 初始化 AI 客户端
        logger.info("开始初始化AI客户端...")
        logger.debug(f"AI客户端配置: Base URL={self.LLM_BASE_URL}, Model={self.LLM_MODEL}, API Key={'***' if self.LLM_API_KEY else '未设置'}")
        
        self.client_llm = AsyncOpenAI(
            api_key=self.LLM_API_KEY, 
            base_url=self.LLM_BASE_URL
        )
        
        logger.info("AI客户端初始化完成")
        
        # 全局变量，用于跟踪正在设置提示词的用户
        self.setting_prompt_users = set()
        # 全局变量，用于跟踪正在设置AI配置的用户
        self.setting_ai_config_users = set()
        # 全局变量，用于存储正在配置中的AI参数
        self.current_ai_config = {}
        
        # 初始化调度器
        self.scheduler = AsyncIOScheduler()
        # 每周一早 9 点执行
        self.scheduler.add_job(self.main_job, 'cron', day_of_week='mon', hour=9, minute=0)
        logger.info("定时任务已配置：每周一早上9点执行")
        self.scheduler.start()
        logger.info("调度器已启动")
    
    def load_prompt(self):
        """从文件中读取提示词，如果文件不存在则使用默认提示词"""
        logger.info(f"开始读取提示词文件: {self.PROMPT_FILE}")
        try:
            with open(self.PROMPT_FILE, "r", encoding="utf-8") as f:
                content = f.read().strip()
                logger.info(f"成功读取提示词文件，长度: {len(content)}字符")
                return content
        except FileNotFoundError:
            logger.warning(f"提示词文件 {self.PROMPT_FILE} 不存在，将使用默认提示词并创建文件")
            # 如果文件不存在，使用默认提示词并创建文件
            self.save_prompt(self.DEFAULT_PROMPT)
            return self.DEFAULT_PROMPT
        except Exception as e:
            logger.error(f"读取提示词文件 {self.PROMPT_FILE} 时出错: {type(e).__name__}: {e}")
            # 如果读取失败，使用默认提示词
            return self.DEFAULT_PROMPT
    
    def save_prompt(self, prompt):
        """将提示词保存到文件中"""
        logger.info(f"开始保存提示词到文件: {self.PROMPT_FILE}")
        try:
            with open(self.PROMPT_FILE, "w", encoding="utf-8") as f:
                f.write(prompt)
            logger.info(f"成功保存提示词到文件，长度: {len(prompt)}字符")
        except Exception as e:
            logger.error(f"保存提示词到文件 {self.PROMPT_FILE} 时出错: {type(e).__name__}: {e}")
    
    def load_config(self):
        """从配置文件读取AI配置"""
        import json
        logger.info(f"开始读取配置文件: {self.CONFIG_FILE}")
        try:
            with open(self.CONFIG_FILE, "r", encoding="utf-8") as f:
                config = json.load(f)
                logger.info(f"成功读取配置文件，配置项数量: {len(config)}")
                return config
        except FileNotFoundError:
            logger.warning(f"配置文件 {self.CONFIG_FILE} 不存在，返回空配置")
            return {}
        except json.JSONDecodeError as e:
            logger.error(f"配置文件 {self.CONFIG_FILE} 格式错误: {e}")
            return {}
        except Exception as e:
            logger.error(f"读取配置文件 {self.CONFIG_FILE} 时出错: {type(e).__name__}: {e}")
            return {}
    
    def save_config(self, config):
        """保存AI配置到文件"""
        import json
        logger.info(f"开始保存配置到文件: {self.CONFIG_FILE}")
        try:
            with open(self.CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
            logger.info(f"成功保存配置到文件，配置项数量: {len(config)}")
        except Exception as e:
            logger.error(f"保存配置到文件 {self.CONFIG_FILE} 时出错: {type(e).__name__}: {e}")
    
    async def fetch_last_week_messages(self, channels_to_fetch=None):
        """抓取过去一周的频道消息
        
        Args:
            channels_to_fetch: 可选，要抓取的频道列表。如果为None，则抓取所有配置的频道。
        """
        # 确保 API_ID 是整数
        logger.info("开始抓取过去一周的频道消息")
        
        async with TelegramClient('session_name', int(self.API_ID), self.API_HASH) as client:
            last_week = datetime.now(timezone.utc) - timedelta(days=7)
            messages_by_channel = {}  # 按频道分组的消息字典
            
            # 确定要抓取的频道
            if channels_to_fetch and isinstance(channels_to_fetch, list):
                # 只抓取指定的频道
                channels = channels_to_fetch
                logger.info(f"正在抓取指定的 {len(channels)} 个频道的消息，时间范围: {last_week} 至今")
            else:
                # 抓取所有配置的频道
                if not self.CHANNELS:
                    logger.warning("没有配置任何频道，无法抓取消息")
                    return messages_by_channel
                channels = self.CHANNELS
                logger.info(f"正在抓取所有 {len(channels)} 个频道的消息，时间范围: {last_week} 至今")
            
            total_message_count = 0
            
            # 遍历所有要抓取的频道
            for channel in channels:
                channel_messages = []
                channel_message_count = 0
                logger.info(f"开始抓取频道: {channel}")
                
                async for message in client.iter_messages(channel, offset_date=last_week, reverse=True):
                    total_message_count += 1
                    channel_message_count += 1
                    if message.text:
                        # 动态获取频道名用于生成链接
                        channel_part = channel.split('/')[-1]
                        msg_link = f"https://t.me/{channel_part}/{message.id}"
                        channel_messages.append(f"内容: {message.text[:500]}\n链接: {msg_link}")
                        
                        # 每抓取10条消息记录一次日志
                        if len(channel_messages) % 10 == 0:
                            logger.debug(f"频道 {channel} 已抓取 {len(channel_messages)} 条有效消息")
                
                # 将当前频道的消息添加到字典中
                messages_by_channel[channel] = channel_messages
                logger.info(f"频道 {channel} 抓取完成，共处理 {channel_message_count} 条消息，其中 {len(channel_messages)} 条包含文本内容")
            
            logger.info(f"所有指定频道消息抓取完成，共处理 {total_message_count} 条消息")
            return messages_by_channel
    
    async def analyze_with_ai(self, messages):
        """调用 AI 进行汇总"""
        logger.info("开始调用AI进行消息汇总")
        
        if not messages:
            logger.info("没有需要分析的消息，返回空结果")
            return "本周无新动态。"

        context_text = "\n\n---\n\n".join(messages)
        prompt = f"{self.CURRENT_PROMPT}{context_text}"
        
        logger.debug(f"AI请求配置: 模型={self.LLM_MODEL}, 提示词长度={len(self.CURRENT_PROMPT)}字符, 上下文长度={len(context_text)}字符")
        logger.debug(f"AI请求总长度: {len(prompt)}字符")
        
        try:
            start_time = datetime.now()
            response = await self.client_llm.chat.completions.create(
                model=self.LLM_MODEL,
                messages=[
                    {"role": "system", "content": "你是一个专业的资讯摘要助手，擅长提取重点并保持客观。"},
                    {"role": "user", "content": prompt},
                ]
            )
            end_time = datetime.now()
            
            processing_time = (end_time - start_time).total_seconds()
            logger.info(f"AI分析完成，处理时间: {processing_time:.2f}秒")
            logger.debug(f"AI响应状态: 成功，选择索引={response.choices[0].index}, 完成原因={response.choices[0].finish_reason}")
            logger.debug(f"AI响应长度: {len(response.choices[0].message.content)}字符")
            
            return response.choices[0].message.content
        except Exception as e:
            logger.error(f"AI分析失败: {type(e).__name__}: {e}")
            return f"AI 分析失败: {e}"
    
    async def send_report(self, summary_text):
        """发送报告"""
        logger.info("开始发送报告")
        logger.debug(f"报告长度: {len(summary_text)}字符")
        
        client = TelegramClient('bot_session', int(self.API_ID), self.API_HASH)
        async with client:
            await client.start(bot_token=self.BOT_TOKEN)
            logger.info("Telegram机器人客户端已启动")
            
            # 向所有管理员发送消息
            for admin_id in self.ADMIN_LIST:
                try:
                    logger.info(f"正在向管理员 {admin_id} 发送报告")
                    await self.send_long_message(client, admin_id, summary_text)
                    logger.info(f"成功向管理员 {admin_id} 发送报告")
                except Exception as e:
                    logger.error(f"向管理员 {admin_id} 发送报告失败: {type(e).__name__}: {e}")
    
    async def main_job(self):
        """主定时任务：每周一生成频道消息汇总"""
        start_time = datetime.now()
        logger.info(f"定时任务启动: {start_time}")
        
        try:
            messages_by_channel = await self.fetch_last_week_messages()
            
            # 按频道分别生成和发送总结报告
            for channel, messages in messages_by_channel.items():
                logger.info(f"开始处理频道 {channel} 的消息")
                summary = await self.analyze_with_ai(messages)
                # 获取频道名称用于报告标题
                channel_name = channel.split('/')[-1]
                await self.send_report(f"📋 **{channel_name} 频道周报汇总**\n\n{summary}")
            
            end_time = datetime.now()
            processing_time = (end_time - start_time).total_seconds()
            logger.info(f"定时任务完成: {end_time}，总处理时间: {processing_time:.2f}秒")
        except Exception as e:
            end_time = datetime.now()
            processing_time = (end_time - start_time).total_seconds()
            logger.error(f"定时任务执行失败: {type(e).__name__}: {e}，开始时间: {start_time}，结束时间: {end_time}，处理时间: {processing_time:.2f}秒")
    
    async def send_long_message(self, client, chat_id, text, max_length=4000):
        """分段发送长消息"""
        logger.info(f"开始发送长消息，接收者: {chat_id}，消息总长度: {len(text)}字符，最大分段长度: {max_length}字符")
        
        if len(text) <= max_length:
            logger.info(f"消息长度未超过限制，直接发送")
            await client.send_message(chat_id, text, link_preview=False)
            return
        
        # 提取频道名称用于分段消息标题
        channel_title = "频道周报汇总"
        if "**" in text and "** " in text:
            # 提取 ** 之间的频道名称
            start_idx = text.index("**") + 2
            end_idx = text.index("** ", start_idx)
            channel_title = text[start_idx:end_idx]
        
        # 分段发送
        parts = []
        current_part = ""
        
        logger.info(f"消息需要分段发送，开始分段处理")
        for line in text.split('\n'):
            # 检查添加当前行是否超过限制
            if len(current_part) + len(line) + 1 <= max_length:
                current_part += line + '\n'
            else:
                # 如果当前部分不为空，添加到列表
                if current_part:
                    parts.append(current_part.strip())
                # 检查当前行是否超过限制
                if len(line) > max_length:
                    # 对超长行进行进一步分割
                    logger.warning(f"发现超长行，长度: {len(line)}字符，将进一步分割")
                    for i in range(0, len(line), max_length):
                        parts.append(line[i:i+max_length])
                else:
                    current_part = line + '\n'
        
        # 添加最后一部分
        if current_part:
            parts.append(current_part.strip())
        
        logger.info(f"消息分段完成，共分成 {len(parts)} 段")
        
        # 发送所有部分
        for i, part in enumerate(parts):
            logger.info(f"正在发送第 {i+1}/{len(parts)} 段，长度: {len(part)}字符")
            await client.send_message(chat_id, f"📋 **{channel_title} ({i+1}/{len(parts)})**\n\n{part}", link_preview=False)
            logger.debug(f"成功发送第 {i+1}/{len(parts)} 段")
    
    # ========== 命令处理 ==========
    
    @filter.command("summary")
    async def handle_manual_summary(self, event: AstrMessageEvent):
        """立即生成本周频道消息汇总"""
        sender_id = event.get_sender_id()
        command = event.message_str
        logger.info(f"收到命令: {command}，发送者: {sender_id}")
        
        # 检查发送者是否为管理员
        if sender_id not in self.ADMIN_LIST and self.ADMIN_LIST != ['me']:
            logger.warning(f"发送者 {sender_id} 没有权限执行命令 {command}")
            yield event.plain_result("您没有权限执行此命令")
            return
        
        # 发送正在处理的消息
        yield event.plain_result("正在为您生成本周总结...")
        logger.info(f"开始执行 {command} 命令")
        
        # 解析命令参数，支持指定频道
        try:
            # 分割命令和参数
            parts = command.split()
            if len(parts) > 1:
                # 有指定频道参数
                specified_channels = []
                for part in parts[1:]:
                    if part.startswith('http'):
                        # 完整的频道URL
                        specified_channels.append(part)
                    else:
                        # 频道名称，需要转换为完整URL
                        specified_channels.append(f"https://t.me/{part}")
                
                # 验证指定的频道是否在配置中
                valid_channels = []
                for channel in specified_channels:
                    if channel in self.CHANNELS:
                        valid_channels.append(channel)
                    else:
                        yield event.plain_result(f"频道 {channel} 不在配置列表中，将跳过")
                
                if not valid_channels:
                    yield event.plain_result("没有找到有效的指定频道")
                    return
                
                # 执行总结任务，只处理指定的有效频道
                messages_by_channel = await self.fetch_last_week_messages(valid_channels)
            else:
                # 没有指定频道，处理所有配置的频道
                messages_by_channel = await self.fetch_last_week_messages()
            
            # 按频道分别生成和发送总结报告
            for channel, messages in messages_by_channel.items():
                logger.info(f"开始处理频道 {channel} 的消息")
                summary = await self.analyze_with_ai(messages)
                # 获取频道名称用于报告标题
                channel_name = channel.split('/')[-1]
                yield event.plain_result(f"📋 **{channel_name} 频道周报汇总**\n\n{summary}")
            
            logger.info(f"命令 {command} 执行成功")
        except Exception as e:
            logger.error(f"执行命令 {command} 时出错: {type(e).__name__}: {e}")
            yield event.plain_result(f"生成总结时出错: {e}")
    
    @filter.command("showprompt")
    async def handle_show_prompt(self, event: AstrMessageEvent):
        """查看当前提示词"""
        sender_id = event.get_sender_id()
        command = event.message_str
        logger.info(f"收到命令: {command}，发送者: {sender_id}")
        
        # 检查发送者是否为管理员
        if sender_id not in self.ADMIN_LIST and self.ADMIN_LIST != ['me']:
            logger.warning(f"发送者 {sender_id} 没有权限执行命令 {command}")
            yield event.plain_result("您没有权限执行此命令")
            return
        
        logger.info(f"执行命令 {command} 成功")
        yield event.plain_result(f"当前提示词：\n\n{self.CURRENT_PROMPT}")
    
    @filter.command("setprompt")
    async def handle_set_prompt(self, event: AstrMessageEvent):
        """设置自定义提示词"""
        sender_id = event.get_sender_id()
        command = event.message_str
        logger.info(f"收到命令: {command}，发送者: {sender_id}")
        
        # 检查发送者是否为管理员
        if sender_id not in self.ADMIN_LIST and self.ADMIN_LIST != ['me']:
            logger.warning(f"发送者 {sender_id} 没有权限执行命令 {command}")
            yield event.plain_result("您没有权限执行此命令")
            return
        
        # 添加用户到正在设置提示词的集合中
        self.setting_prompt_users.add(sender_id)
        logger.info(f"添加用户 {sender_id} 到提示词设置集合")
        yield event.plain_result(f"请发送新的提示词，我将使用它来生成总结。\n\n当前提示词：\n{self.CURRENT_PROMPT}")
    
    @filter.command("showaicfg")
    async def handle_show_ai_config(self, event: AstrMessageEvent):
        """查看AI配置"""
        sender_id = event.get_sender_id()
        command = event.message_str
        logger.info(f"收到命令: {command}，发送者: {sender_id}")
        
        # 检查发送者是否为管理员
        if sender_id not in self.ADMIN_LIST and self.ADMIN_LIST != ['me']:
            logger.warning(f"发送者 {sender_id} 没有权限执行命令 {command}")
            yield event.plain_result("您没有权限执行此命令")
            return
        
        # 显示当前配置
        config_info = f"当前AI配置：\n\n"
        config_info += f"API Key：{self.LLM_API_KEY[:10]}...{self.LLM_API_KEY[-10:] if len(self.LLM_API_KEY) > 20 else self.LLM_API_KEY}\n"
        config_info += f"Base URL：{self.LLM_BASE_URL}\n"
        config_info += f"Model：{self.LLM_MODEL}\n"
        
        logger.info(f"执行命令 {command} 成功")
        yield event.plain_result(config_info)
    
    @filter.command("setaicfg")
    async def handle_set_ai_config(self, event: AstrMessageEvent):
        """设置AI配置"""
        sender_id = event.get_sender_id()
        command = event.message_str
        logger.info(f"收到命令: {command}，发送者: {sender_id}")
        
        # 检查发送者是否为管理员
        if sender_id not in self.ADMIN_LIST and self.ADMIN_LIST != ['me']:
            logger.warning(f"发送者 {sender_id} 没有权限执行命令 {command}")
            yield event.plain_result("您没有权限执行此命令")
            return
        
        # 添加用户到正在设置AI配置的集合中
        self.setting_ai_config_users.add(sender_id)
        logger.info(f"添加用户 {sender_id} 到AI配置设置集合")
        
        # 初始化当前配置，使用None值来标识未处理的参数
        self.current_ai_config = {
            'api_key': None,
            'base_url': None,
            'model': None
        }
        
        logger.info(f"开始执行 {command} 命令")
        yield event.plain_result("请依次发送以下AI配置参数，或发送/skip跳过：\n\n1. API Key\n2. Base URL\n3. Model\n\n发送/cancel取消设置")
    
    @filter.command("showchannels")
    async def handle_show_channels(self, event: AstrMessageEvent):
        """查看当前频道列表"""
        sender_id = event.get_sender_id()
        command = event.message_str
        logger.info(f"收到命令: {command}，发送者: {sender_id}")
        
        # 检查发送者是否为管理员
        if sender_id not in self.ADMIN_LIST and self.ADMIN_LIST != ['me']:
            logger.warning(f"发送者 {sender_id} 没有权限执行命令 {command}")
            yield event.plain_result("您没有权限执行此命令")
            return
        
        logger.info(f"执行命令 {command} 成功")
        
        if not self.CHANNELS:
            yield event.plain_result("当前没有配置任何频道")
            return
        
        # 构建频道列表消息
        channels_msg = "当前配置的频道列表：\n\n"
        for i, channel in enumerate(self.CHANNELS, 1):
            channels_msg += f"{i}. {channel}\n"
        
        yield event.plain_result(channels_msg)
    
    @filter.command("addchannel")
    async def handle_add_channel(self, event: AstrMessageEvent):
        """添加频道"""
        sender_id = event.get_sender_id()
        command = event.message_str
        logger.info(f"收到命令: {command}，发送者: {sender_id}")
        
        # 检查发送者是否为管理员
        if sender_id not in self.ADMIN_LIST and self.ADMIN_LIST != ['me']:
            logger.warning(f"发送者 {sender_id} 没有权限执行命令 {command}")
            yield event.plain_result("您没有权限执行此命令")
            return
        
        try:
            _, channel_url = command.split(maxsplit=1)
            channel_url = channel_url.strip()
            
            if not channel_url:
                yield event.plain_result("请提供有效的频道URL")
                return
            
            # 检查频道是否已存在
            if channel_url in self.CHANNELS:
                yield event.plain_result(f"频道 {channel_url} 已存在于列表中")
                return
            
            # 添加频道到列表
            self.CHANNELS.append(channel_url)
            
            # 更新配置文件
            config = self.load_config()
            config['channels'] = self.CHANNELS
            self.save_config(config)
            
            logger.info(f"已添加频道 {channel_url} 到列表")
            yield event.plain_result(f"频道 {channel_url} 已成功添加到列表中\n\n当前频道数量：{len(self.CHANNELS)}")
            
        except ValueError:
            # 没有提供频道URL
            yield event.plain_result("请提供有效的频道URL，例如：/addchannel https://t.me/examplechannel")
        except Exception as e:
            logger.error(f"添加频道时出错: {type(e).__name__}: {e}")
            yield event.plain_result(f"添加频道时出错: {e}")
    
    @filter.command("deletechannel")
    async def handle_delete_channel(self, event: AstrMessageEvent):
        """删除频道"""
        sender_id = event.get_sender_id()
        command = event.message_str
        logger.info(f"收到命令: {command}，发送者: {sender_id}")
        
        # 检查发送者是否为管理员
        if sender_id not in self.ADMIN_LIST and self.ADMIN_LIST != ['me']:
            logger.warning(f"发送者 {sender_id} 没有权限执行命令 {command}")
            yield event.plain_result("您没有权限执行此命令")
            return
        
        try:
            _, channel_url = command.split(maxsplit=1)
            channel_url = channel_url.strip()
            
            if not channel_url:
                yield event.plain_result("请提供有效的频道URL")
                return
            
            # 检查频道是否存在
            if channel_url not in self.CHANNELS:
                yield event.plain_result(f"频道 {channel_url} 不在列表中")
                return
            
            # 从列表中删除频道
            self.CHANNELS.remove(channel_url)
            
            # 更新配置文件
            config = self.load_config()
            config['channels'] = self.CHANNELS
            self.save_config(config)
            
            logger.info(f"已从列表中删除频道 {channel_url}")
            yield event.plain_result(f"频道 {channel_url} 已成功从列表中删除\n\n当前频道数量：{len(self.CHANNELS)}")
            
        except ValueError:
            # 没有提供频道URL或频道不存在
            yield event.plain_result("请提供有效的频道URL，例如：/deletechannel https://t.me/examplechannel")
        except Exception as e:
            logger.error(f"删除频道时出错: {type(e).__name__}: {e}")
            yield event.plain_result(f"删除频道时出错: {e}")
    
    async def terminate(self):
        """插件被卸载/停用时会调用。"""
        logger.info("插件正在被卸载，停止调度器...")
        if hasattr(self, 'scheduler'):
            self.scheduler.shutdown()
            logger.info("调度器已停止")
