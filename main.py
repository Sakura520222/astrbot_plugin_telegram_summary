"""AstrBot Telegram频道消息总结插件"""
import asyncio
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from telethon import TelegramClient
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# AstrBot 插件 API
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register, StarTools
from astrbot.api import logger
from astrbot.api import AstrBotConfig

@register("telegram_summary", "Sakura520222", "一个 Telegram 频道消息总结插件，每周自动生成指定频道的消息汇总报告，支持自动推送到QQ群组和用户。", "1.2.0", "https://github.com/Sakura520222/astrbot_plugin_telegram_summary")
class TelegramSummaryPlugin(Star):
    """Telegram 频道消息总结插件
    
    定期抓取指定 Telegram 频道的消息，使用 AI 生成周报总结，
    并自动推送到配置的群组和用户。
    """
    
    # 类常量定义
    
    # 配置相关常量
    DEFAULT_SUMMARY_DAYS: int = 7
    """默认总结时间范围（天）
    
    当频道首次进行总结或上次总结时间记录丢失时，
    使用此值作为默认的抓取时间范围。
    """
    
    SESSION_TIMEOUT: int = 120
    """登录会话超时时间（秒）
    
    Telegram 登录流程（包括手机号、验证码、密码输入）
    的最大等待时间。超时后需要重新开始登录流程。
    """
    
    MESSAGE_TRUNCATE_LENGTH: int = 500
    """消息截断长度（字符数）
    
    为了控制 AI 输入长度和 token 消耗，
    每条 Telegram 消息文本超过此长度时将被截断。
    """
    
    # 推送相关常量
    PUSH_DELAY_MIN: int = 1
    """推送延迟最小值（秒）
    
    向多个目标推送总结消息时，
    每次推送之间的最小延迟时间，用于避免触发频率限制。
    """
    
    PUSH_DELAY_MAX: int = 3
    """推送延迟最大值（秒）
    
    向多个目标推送总结消息时，
    每次推送之间的最大延迟时间，用于避免触发频率限制。
    实际延迟时间将在 PUSH_DELAY_MIN 和 PUSH_DELAY_MAX 之间随机选择。
    """
    
    # URL 相关常量
    TELEGRAM_URL_PREFIX: str = "https://t.me/"
    """Telegram 频道 URL 前缀
    
    用于构建 Telegram 频道消息链接的 URL 前缀。
    示例：https://t.me/channel_name/12345
    """
    
    DEFAULT_AUTO_SUMMARY_TIME: str = "周一 09:00"
    """默认自动总结时间配置
    
    默认的定时任务执行时间，格式为"星期 时间"。
    可以在插件配置中修改此值。
    """
    
    def __init__(self, context: Context, config: AstrBotConfig):
        """初始化插件
        
        Args:
            context: AstrBot 上下文对象
            config: 插件配置对象
        """
        super().__init__(context)
        self.config = config
        
        # 按职责拆分初始化流程
        self._init_data_directory()
        self._init_file_paths()
        self._init_constants()
        self._load_configurations(config)
        self._init_concurrent_safety()
        self._init_runtime_state()
        self._setup_scheduler()
    
    def _init_data_directory(self):
        """初始化数据目录"""
        # 使用传统方式获取数据目录
        # 插件数据目录位于 data/plugin_data/{插件名}/
        # __file__ = AstrBot/data/plugins/astrbot_plugin_telegram_summary/main.py
        # 所以需要向上2级到 data/，然后进入 plugin_data/
        from pathlib import Path
        plugin_data_dir = Path(__file__).parent.parent.parent / 'plugin_data' / 'astrbot_plugin_telegram_summary'
        self.data_dir = plugin_data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"数据目录已准备: {self.data_dir}")
    
    def _init_file_paths(self):
        """初始化文件路径配置"""
        self.PROMPT_FILE = str(self.data_dir / "prompt.txt")
        self.CONFIG_FILE = str(self.data_dir / "config.json")
        self.RESTART_FLAG_FILE = str(self.data_dir / ".restart_flag")
        self.LAST_SUMMARY_FILE = str(self.data_dir / "last_summary_time.json")
        self.USER_SESSION_FILE = str(self.data_dir / "user_session.session")
        
        logger.debug(f"配置文件路径: 提示词={self.PROMPT_FILE}, "
                    f"配置={self.CONFIG_FILE}, "
                    f"上次总结={self.LAST_SUMMARY_FILE}, "
                    f"会话={self.USER_SESSION_FILE}")
        
        # 检查并设置 session 文件权限
        self._ensure_session_file_security()
    
    def _ensure_session_file_security(self):
        """确保 session 文件的安全性
        
        检查 session 文件权限，确保只有文件所有者可以读写。
        在 Windows 上，chmod 的功能受限，但仍会尝试设置。
        """
        session_file = Path(self.USER_SESSION_FILE)
        
        if session_file.exists():
            try:
                # 尝试设置文件权限为 600 (仅所有者可读写)
                # Windows: 设置为只读属性
                # Unix/Linux: 设置为 rw-------
                import stat
                os.chmod(self.USER_SESSION_FILE, 0o600)
                logger.debug(f"已设置 session 文件权限: {self.USER_SESSION_FILE}")
            except Exception as e:
                logger.warning(
                    f"无法设置 session 文件权限: {type(e).__name__}: {e}\n"
                    "建议手动检查文件权限，确保只有所有者可以访问"
                )
    
    def _init_constants(self):
        """初始化常量配置"""
        self.DEFAULT_PROMPT = (
            "请对以下提供的文本内容进行分类总结，并严格遵守以下规则：\n\n"
            "## 一、 语言与布局\n"
            "1. **禁止废话**：直接输出总结后的内容，严禁包含任何前言、备注、解释或后语。\n\n"
            "## 二、 格式与排版\n"
            "1. **主标题**：格式为\"一、xxx\"。\n"
            "2. **层级符号**：\n"
            "   - 一级标题使用 ●\n"
            "   - 二级标题使用 ○\n"
            "   - 三级内容使用 -\n"
            "   - 必须配合恰当的缩进以体现层级。\n"
            "3. 禁止使用 Markdown 格式。\n\n"
            "## 三、 标题与链接处理\n"
            "1. **内容提取**：提取每条消息的核心内容或标题。\n\n"
            "## 四、 内容精简规则\n"
            "1. **删除冗余**：\n"
            "   - 删除所有 source 来源信息（如 Source: XXX 或任何出处链接）。\n"
            "   - 删除所有标签（Tags）。\n"
            "2. **精炼表达**：不要原文复制，对内容进行脱水总结，仅保留关键点。\n"
            "3. **忠于原文**：严禁添加、脑补任何原文中没有的内容。\n\n"
        )
    
    def _load_configurations(self, config: AstrBotConfig):
        """从配置系统加载所有配置
        
        Args:
            config: AstrBot 配置对象
        
        Raises:
            ValueError: 当配置验证失败时
        """
        logger.info("开始从 AstrBot 配置系统加载配置...")
        
        # Telegram 配置（带验证）
        telegram_config = config.get('telegram', {})
        self.API_ID = self._validate_api_id(telegram_config.get('api_id'))
        self.API_HASH = self._validate_api_hash(telegram_config.get('api_hash'))
        
        # 频道配置（带验证）
        self.CHANNELS = self._validate_channels(config.get('channels', []))
        logger.info(f"已加载频道列表: {self.CHANNELS}")
        
        # 提示词配置
        self.CURRENT_PROMPT = config.get('prompt', self.DEFAULT_PROMPT)
        logger.info("已加载提示词配置")
        
        # AI 提供商配置（带验证）
        self.ai_provider = self._validate_ai_provider(config.get('select_provider'))
        logger.info(f"已加载AI提供商: {self.ai_provider}")
        
        # 自动总结时间配置（带验证）
        self.auto_summary_time = self._validate_summary_time(
            config.get('auto_summary_time', self.DEFAULT_AUTO_SUMMARY_TIME)
        )
        logger.info(f"已加载自动总结时间: {self.auto_summary_time}")
        
        # 管理员配置（用于告警）
        self.admin_id = config.get('admin_id')
        if self.admin_id:
            logger.info(f"已配置管理员ID: {self.admin_id}")
        
        # 自动推送目标配置
        self.auto_push_groups = config.get('auto_push_groups', [])
        self.auto_push_users = config.get('auto_push_users', [])
        
        # 验证推送目标格式
        self._validate_push_targets()
        
        logger.info(f"已加载推送目标: 群组 {len(self.auto_push_groups)} 个, 用户 {len(self.auto_push_users)} 个")
        
        # 消息模板配置
        self.message_templates = config.get('message_templates', {})
        logger.info(f"已加载消息模板配置: {len(self.message_templates)} 项")
    
    def _validate_api_id(self, api_id) -> int:
        """验证 Telegram API ID
        
        Args:
            api_id: API ID 配置值
        
        Returns:
            int: 验证后的 API ID
        
        Raises:
            ValueError: 当 API ID 无效时
        """
        if api_id is None:
            raise ValueError(
                "Telegram API ID 未配置。\n"
                "请在插件配置中设置 'telegram.api_id'。\n"
                "获取方式：访问 https://my.telegram.org/apps"
            )
        
        try:
            api_id_int = int(api_id)
            if api_id_int <= 0:
                raise ValueError("API ID 必须为正整数")
            return api_id_int
        except (ValueError, TypeError) as e:
            raise ValueError(
                f"Telegram API ID 格式错误: {api_id}\n"
                f"API ID 必须是整数，当前值: {type(api_id).__name__}\n"
                "获取方式：访问 https://my.telegram.org/apps"
            ) from e
    
    def _validate_api_hash(self, api_hash) -> str:
        """验证 Telegram API Hash
        
        Args:
            api_hash: API Hash 配置值
        
        Returns:
            str: 验证后的 API Hash
        
        Raises:
            ValueError: 当 API Hash 无效时
        """
        if api_hash is None:
            raise ValueError(
                "Telegram API Hash 未配置。\n"
                "请在插件配置中设置 'telegram.api_hash'。\n"
                "获取方式：访问 https://my.telegram.org/apps"
            )
        
        if not isinstance(api_hash, str):
            raise ValueError(
                f"Telegram API Hash 必须是字符串，当前值: {type(api_hash).__name__}"
            )
        
        # API Hash 通常是 32 个十六进制字符
        api_hash_clean = api_hash.strip()
        if len(api_hash_clean) != 32:
            logger.warning(
                f"API Hash 长度异常（应为32字符）: {len(api_hash_clean)} 字符。"
                "请检查配置是否正确。"
            )
        
        try:
            # 验证是否为有效的十六进制字符串
            int(api_hash_clean, 16)
            return api_hash_clean
        except ValueError:
            raise ValueError(
                f"Telegram API Hash 格式错误，应为32位十六进制字符串\n"
                f"当前值长度: {len(api_hash_clean)} 字符\n"
                "获取方式：访问 https://my.telegram.org/apps"
            )
    
    def _validate_channels(self, channels) -> list:
        """验证频道配置
        
        Args:
            channels: 频道列表配置
        
        Returns:
            list: 验证后的频道列表
        
        Raises:
            ValueError: 当频道配置无效时
        """
        if not channels:
            raise ValueError(
                "未配置任何频道。\n"
                "请在插件配置中添加 'channels' 列表。\n"
                "示例：['https://t.me/channel1', 'https://t.me/channel2']"
            )
        
        if not isinstance(channels, list):
            raise ValueError(
                f"频道配置必须是列表格式，当前类型: {type(channels).__name__}"
            )
        
        validated_channels = []
        for channel in channels:
            if not isinstance(channel, str):
                logger.warning(f"跳过非字符串频道配置: {channel} (类型: {type(channel).__name__})")
                continue
            
            channel = channel.strip()
            if not channel:
                logger.warning("跳过空频道配置")
                continue
            
            # 基本格式验证
            if channel.startswith('http'):
                if not ('t.me/' in channel or 'telegram.me/' in channel):
                    logger.warning(f"频道URL格式可能不正确: {channel}")
            validated_channels.append(channel)
        
        if not validated_channels:
            raise ValueError(
                "频道列表为空或所有频道配置均无效。\n"
                "请检查配置格式是否正确。"
            )
        
        return validated_channels
    
    def _validate_ai_provider(self, provider):
        """验证 AI 提供商配置
        
        Args:
            provider: AI 提供商配置值
        
        Returns:
            str: 验证后的提供商名称
        
        Raises:
            ValueError: 当提供商配置无效时
        """
        if provider is None:
            raise ValueError(
                "未配置 AI 提供商。\n"
                "请在插件配置中设置 'select_provider'。\n"
                "可选值: 查看您的 AI 提供商列表"
            )
        
        if not isinstance(provider, str) or not provider.strip():
            raise ValueError(
                f"AI 提供商配置必须是非空字符串，当前值: {provider}"
            )
        
        return provider.strip()
    
    def _validate_summary_time(self, time_str: str) -> str:
        """验证自动总结时间配置
        
        Args:
            time_str: 时间字符串
        
        Returns:
            str: 验证后的时间字符串
        """
        try:
            # 尝试解析时间配置
            self.parse_summary_time(time_str)
            return time_str
        except Exception as e:
            logger.warning(
                f"自动总结时间配置无效: {time_str}，使用默认值: {self.DEFAULT_AUTO_SUMMARY_TIME}\n"
                f"错误原因: {e}"
            )
            return self.DEFAULT_AUTO_SUMMARY_TIME
    
    def _validate_push_targets(self):
        """验证推送目标配置
        
        检查推送目标的格式是否正确
        """
        # 验证群组ID格式
        if not isinstance(self.auto_push_groups, list):
            logger.warning("auto_push_groups 配置必须是列表，已重置为空列表")
            self.auto_push_groups = []
        
        validated_groups = []
        for group_id in self.auto_push_groups:
            if isinstance(group_id, (int, str)):
                validated_groups.append(str(group_id))
            else:
                logger.warning(f"跳过无效的群组ID: {group_id}")
        self.auto_push_groups = validated_groups
        
        # 验证用户ID格式
        if not isinstance(self.auto_push_users, list):
            logger.warning("auto_push_users 配置必须是列表，已重置为空列表")
            self.auto_push_users = []
        
        validated_users = []
        for user_id in self.auto_push_users:
            if isinstance(user_id, (int, str)):
                validated_users.append(str(user_id))
            else:
                logger.warning(f"跳过无效的用户ID: {user_id}")
        self.auto_push_users = validated_users
        
        # 验证管理员ID格式
        if self.admin_id is not None:
            if isinstance(self.admin_id, (int, str)):
                self.admin_id = str(self.admin_id)
                logger.info(f"管理员ID已验证: {self.admin_id}")
            else:
                logger.warning(f"管理员ID格式无效: {self.admin_id}，已忽略")
                self.admin_id = None
    
    def _init_concurrent_safety(self):
        """初始化并发安全机制"""
        self._setting_prompt_lock = asyncio.Lock()
        self._login_states_lock = asyncio.Lock()
        logger.debug("并发安全锁已初始化")
    
    def _init_runtime_state(self):
        """初始化运行时状态"""
        self.setting_prompt_users = set()
        self.login_states = {}
        self.last_summary_times = self.load_last_summary_times()
        logger.info(f"已加载各频道上次总结时间: {self.last_summary_times}")
    
    def _setup_scheduler(self):
        """设置定时任务调度器"""
        self.scheduler = AsyncIOScheduler()
        day_of_week, hour, minute = self.parse_summary_time(self.auto_summary_time)
        self.scheduler.add_job(self.main_job, 'cron', day_of_week=day_of_week, hour=hour, minute=minute)
        logger.info(f"定时任务已配置：{self.auto_summary_time}")
        self.scheduler.start()
        logger.info("调度器已启动")
    
    def _extract_channel_name(self, channel: str) -> str:
        """从频道标识符中提取频道名称
        
        Args:
            channel: 频道标识符（可能是完整URL或频道名）
        
        Returns:
            str: 提取后的频道名称
        """
        return channel.split('/')[-1]
    
    def _match_channel(self, user_input: str, config_channel: str) -> bool:
        """匹配用户输入的频道与配置中的频道
        
        支持多种匹配方式：完全匹配、频道名匹配、URL转换匹配
        
        Args:
            user_input: 用户输入的频道标识符
            config_channel: 配置中的频道标识符
        
        Returns:
            bool: 是否匹配
        
        Examples:
            >>> _match_channel("channel_name", "https://t.me/channel_name")
            True
            >>> _match_channel("https://t.me/channel_name", "channel_name")
            True
            >>> _match_channel("https://t.me/channel_name", "https://t.me/channel_name")
            True
        """
        # 提取频道名称
        config_channel_name = self._extract_channel_name(config_channel)
        user_channel_name = self._extract_channel_name(user_input)
        
        # 构建配置频道的所有可能标识符（使用集合提高查找效率）
        config_identifiers = {
            config_channel,  # 原始配置（可能是URL或频道名）
            config_channel_name  # 提取后的频道名
        }
        
        # 如果配置是URL，添加URL格式
        if not config_channel.startswith('http'):
            config_identifiers.add(f"{self.TELEGRAM_URL_PREFIX}{config_channel}")
        
        # 检查用户输入的任何形式是否匹配配置的标识符
        return (
            user_input in config_identifiers or  # 直接匹配
            user_channel_name in config_identifiers  # 提取后的频道名匹配
        )
    
    def _init_login_state(self, sender_id: str) -> bool:
        """初始化用户登录状态
        
        Args:
            sender_id: 用户ID
        
        Returns:
            bool: 是否成功初始化（False表示用户已在登录流程中）
        """
        if sender_id in self.login_states:
            return False
        
        self.login_states[sender_id] = {
            'stage': 'phone',
            'phone': None,
            'client': None,
            'session_file': None
        }
        return True
    
    async def _cleanup_login_session(self, sender_id: str):
        """清理登录会话资源
        
        Args:
            sender_id: 用户ID
        """
        if sender_id in self.login_states and self.login_states[sender_id].get('client'):
            try:
                await self.login_states[sender_id]['client'].disconnect()
            except Exception as e:
                logger.warning(f"断开Telegram客户端时出错: {type(e).__name__}: {e}")
        
        if sender_id in self.login_states:
            del self.login_states[sender_id]
    
    async def _handle_phone_stage(self, event, user_input: str, login_state: dict, sender_id: str):
        """处理登录流程的手机号输入阶段
        
        Args:
            event: 消息事件对象
            user_input: 用户输入的手机号
            login_state: 登录状态字典
            sender_id: 用户ID
        
        Returns:
            tuple: (success, should_stop) success表示是否成功，should_stop表示是否停止会话
        """
        # 验证手机号格式
        if not user_input.startswith('+'):
            await event.send(event.plain_result(
                "❌ **手机号格式错误**\n\n"
                "手机号必须以 `+` 开头（包含国家代码）\n"
                "正确示例：`+8613812345678`\n\n"
                "请重新输入手机号，或发送 `退出` 取消登录"
            ))
            return False, False
        
        phone = user_input
        logger.info(f"用户 {sender_id} 输入手机号: {phone}")
        
        # 提示正在连接
        await event.send(event.plain_result("📡 正在连接到 Telegram 服务器并请求验证码..."))
        
        try:
            # 创建Telegram客户端（使用固定的session文件）
            session_file = self.USER_SESSION_FILE
            api_id = int(self.API_ID)
            
            client = TelegramClient(session_file, api_id, self.API_HASH)
            await client.connect()
            
            logger.info(f"为用户 {sender_id} 创建Telegram客户端，会话文件: {session_file}")
            
            # 发送验证码
            await client.send_code_request(phone)
            
            logger.info(f"验证码已发送到用户 {sender_id} 的手机/Telegram应用")
            
            # 更新登录状态
            login_state['stage'] = 'code'
            login_state['phone'] = phone
            login_state['client'] = client
            login_state['session_file'] = session_file
            
            # 提示用户输入验证码
            await event.send(event.plain_result(
                "📩 **验证码已发送**\n\n"
                "验证码已发送到您的 Telegram 应用或短信\n"
                "请输入您收到的验证码\n\n"
                "⏱️ 会话将在 120 秒后超时，或发送 `退出` 取消登录"
            ))
            
            return True, False
            
        except Exception as e:
            logger.error(f"发送验证码失败: {type(e).__name__}: {e}")
            await event.send(event.plain_result(
                f"❌ **发送验证码失败**\n\n"
                f"错误：{e}\n\n"
                "请检查手机号和网络连接后重试"
            ))
            await self._cleanup_login_session(sender_id)
            return False, True
    
    async def _handle_code_stage(self, event, user_input: str, login_state: dict, sender_id: str):
        """处理登录流程的验证码输入阶段
        
        Args:
            event: 消息事件对象
            user_input: 用户输入的验证码
            login_state: 登录状态字典
            sender_id: 用户ID
        
        Returns:
            tuple: (success, should_stop) success表示是否成功，should_stop表示是否停止会话
        """
        code = user_input
        phone = login_state['phone']
        client = login_state['client']
        
        logger.info(f"用户 {sender_id} 输入验证码")
        
        try:
            # 尝试使用验证码登录
            await client.sign_in(phone, code)
            
            logger.info(f"用户 {sender_id} Telegram登录成功（无两步验证）")
            
            await event.send(event.plain_result(
                "✅ **登录成功！**\n\n"
                "您的 Telegram 账号已成功登录\n"
                "Session 已保存，后续将自动使用此账号"
            ))
            
            # 保持连接一小段时间确保session正确保存
            await asyncio.sleep(2)
            await self._cleanup_login_session(sender_id)
            return True, True
            
        except Exception as password_error:
            error_msg = str(password_error)
            logger.info(f"用户 {sender_id} 登录时需要两步验证: {error_msg}")
            
            # 检查是否是两步验证错误
            if "SessionPasswordNeededError" in error_msg or ("verification" in error_msg.lower() and "password" in error_msg.lower()):
                # 更新登录状态
                login_state['stage'] = 'password'
                
                # 提示用户输入密码
                await event.send(event.plain_result(
                    "🔐 **检测到两步验证**\n\n"
                    "您的账号启用了两步验证（云密码）\n"
                    "请输入您的两步验证密码\n\n"
                    "⚠️ **安全提示**：输入密码后建议手动撤回该消息\n"
                    "⏱️ 会话将在 120 秒后超时，或发送 `退出` 取消登录"
                ))
                
                return False, False
            else:
                # 其他类型的错误
                logger.error(f"用户 {sender_id} 验证码登录失败: {password_error}")
                await event.send(event.plain_result(
                    "❌ **登录失败**\n\n"
                    f"错误信息：{password_error}\n"
                    "可能的原因：\n"
                    "• 验证码错误或已过期\n"
                    "• 手机号格式不正确\n"
                    "• 网络连接问题\n\n"
                    "请使用 `/tg_login` 重新开始"
                ))
                await self._cleanup_login_session(sender_id)
                return False, True
    
    async def _handle_password_stage(self, event, user_input: str, login_state: dict, sender_id: str):
        """处理登录流程的两步验证密码输入阶段
        
        Args:
            event: 消息事件对象
            user_input: 用户输入的密码
            login_state: 登录状态字典
            sender_id: 用户ID
        
        Returns:
            tuple: (success, should_stop) success表示是否成功，should_stop表示是否停止会话
        """
        password = user_input
        client = login_state['client']
        
        logger.debug(f"用户 {sender_id} 输入两步验证密码")  # 使用 debug 级别避免敏感信息泄露
        
        try:
            # 使用密码登录
            await client.sign_in(password=password)
            
            logger.info(f"用户 {sender_id} Telegram登录成功（使用两步验证）")
            
            await event.send(event.plain_result(
                "✅ **登录成功！**\n\n"
                "您的 Telegram 账号已成功登录\n"
                "Session 已保存，后续将自动使用此账号"
            ))
            
            # 保持连接一小段时间确保session正确保存
            await asyncio.sleep(2)
            await self._cleanup_login_session(sender_id)
            return True, True
            
        except Exception as pwd_error:
            logger.error(f"用户 {sender_id} 两步验证密码错误: {pwd_error}")
            await event.send(event.plain_result(
                "❌ **两步验证密码错误**\n\n"
                f"登录失败：{pwd_error}\n\n"
                "请检查密码后重试，使用 `/tg_login` 重新开始"
            ))
            await self._cleanup_login_session(sender_id)
            return False, True
    
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
    
    def load_last_summary_times(self):
        """从文件中读取各频道的上次总结时间，如果文件不存在则返回空字典"""
        import json
        logger.info(f"开始读取各频道上次总结时间文件: {self.LAST_SUMMARY_FILE}")
        try:
            with open(self.LAST_SUMMARY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                last_times = {}
                for channel, time_str in data.items():
                    if time_str:
                        from datetime import datetime, timezone
                        last_time = datetime.fromisoformat(time_str).replace(tzinfo=timezone.utc)
                        last_times[channel] = last_time
                logger.info(f"成功读取各频道上次总结时间，共 {len(last_times)} 个频道")
                return last_times
        except FileNotFoundError:
            logger.warning(f"上次总结时间文件 {self.LAST_SUMMARY_FILE} 不存在，将使用默认值")
            return {}
        except json.JSONDecodeError as e:
            logger.error(f"上次总结时间文件 {self.LAST_SUMMARY_FILE} 格式错误: {e}")
            return {}
        except Exception as e:
            logger.error(f"读取上次总结时间文件 {self.LAST_SUMMARY_FILE} 时出错: {type(e).__name__}: {e}")
            return {}
    
    def save_last_summary_times(self, times):
        """保存各频道的上次总结时间到文件"""
        import json
        logger.info(f"开始保存各频道上次总结时间到文件: {self.LAST_SUMMARY_FILE}")
        try:
            time_dict = {}
            for channel, time_obj in times.items():
                time_dict[channel] = time_obj.isoformat()
            with open(self.LAST_SUMMARY_FILE, "w", encoding="utf-8") as f:
                json.dump(time_dict, f, ensure_ascii=False, indent=2)
            logger.info(f"成功保存各频道上次总结时间，共 {len(times)} 个频道")
        except Exception as e:
            logger.error(f"保存各频道上次总结时间到文件 {self.LAST_SUMMARY_FILE} 时出错: {type(e).__name__}: {e}")
    
    async def fetch_last_week_messages(self, channels_to_fetch=None):
        """抓取从上次总结时间至今的频道消息
        
        Args:
            channels_to_fetch: 可选，要抓取的频道列表。如果为None，则抓取所有配置的频道。
        
        Returns:
            dict: 按频道分组的消息字典 {channel: [messages]}
        
        Raises:
            Exception: 网络中断、认证失败等异常会向上传播
        """
        # 确保 API_ID 是整数
        logger.info("开始抓取频道消息")
        
        try:
            async with TelegramClient(self.USER_SESSION_FILE, int(self.API_ID), self.API_HASH) as client:
                current_time = datetime.now(timezone.utc)
                
                messages_by_channel = {}  # 按频道分组的消息字典
                
                # 确定要抓取的频道
                if channels_to_fetch and isinstance(channels_to_fetch, list):
                    # 只抓取指定的频道
                    channels = channels_to_fetch
                    logger.info(f"正在抓取指定的 {len(channels)} 个频道的消息")
                else:
                    # 抓取所有配置的频道
                    if not self.CHANNELS:
                        logger.warning("没有配置任何频道，无法抓取消息")
                        return messages_by_channel
                    channels = self.CHANNELS
                    logger.info(f"正在抓取所有 {len(channels)} 个频道的消息")
                
                total_message_count = 0
                
                # 遍历所有要抓取的频道
                for channel in channels:
                    channel_messages = []
                    channel_message_count = 0
                    logger.info(f"开始抓取频道: {channel}")
                    
                    try:
                        # 为每个频道确定独立的起始时间
                        if channel in self.last_summary_times and self.last_summary_times[channel]:
                            start_time = self.last_summary_times[channel]
                            logger.info(f"频道 {channel} 使用上次总结时间作为起始时间: {start_time}")
                        else:
                            start_time = current_time - timedelta(days=self.DEFAULT_SUMMARY_DAYS)
                            logger.info(f"频道 {channel} 没有上次总结时间，使用默认时间范围: 过去{self.DEFAULT_SUMMARY_DAYS}天 ({start_time})")
                        
                        # 异步迭代消息，添加网络中断保护
                        async for message in client.iter_messages(channel, offset_date=start_time, reverse=True):
                            total_message_count += 1
                            channel_message_count += 1
                            if message.text:
                                # 动态获取频道名用于生成链接
                                channel_part = self._extract_channel_name(channel)
                                msg_link = f"{self.TELEGRAM_URL_PREFIX}{channel_part}/{message.id}"
                                channel_messages.append(f"内容: {message.text[:self.MESSAGE_TRUNCATE_LENGTH]}\n链接: {msg_link}")
                                
                                # 每抓取10条消息记录一次日志
                                if len(channel_messages) % 10 == 0:
                                    logger.debug(f"频道 {channel} 已抓取 {len(channel_messages)} 条有效消息")
                    
                    except Exception as channel_error:
                        logger.error(f"抓取频道 {channel} 时出错: {type(channel_error).__name__}: {channel_error}")
                        # 继续处理其他频道，不中断整个流程
                        channel_messages = []
                    
                    # 将当前频道的消息添加到字典中
                    messages_by_channel[channel] = channel_messages
                    logger.info(f"频道 {channel} 抓取完成，共处理 {channel_message_count} 条消息，其中 {len(channel_messages)} 条包含文本内容")
                
                logger.info(f"所有指定频道消息抓取完成，共处理 {total_message_count} 条消息")
                return messages_by_channel
        
        except Exception as e:
            logger.error(f"Telegram客户端连接失败: {type(e).__name__}: {e}")
            raise Exception(f"无法连接到Telegram: 请检查网络连接和登录状态") from e
    
    async def analyze_with_ai(self, messages):
        """调用 AI 进行总结"""
        logger.info("开始调用AI进行消息总结")
        
        if not messages:
            logger.info("没有需要分析的消息，返回空结果")
            return "本周无新动态。"

        context_text = "\n\n---\n\n".join(messages)
        prompt = f"{self.CURRENT_PROMPT}{context_text}"
        
        logger.debug(f"AI请求配置: 提供商={self.ai_provider}, 提示词长度={len(self.CURRENT_PROMPT)}字符, 上下文长度={len(context_text)}字符")
        logger.debug(f"AI请求总长度: {len(prompt)}字符")
        
        try:
            start_time = datetime.now(timezone.utc)
            # 使用AstrBot框架提供的AI调用机制
            response = await self.context.llm_generate(
                chat_provider_id=self.ai_provider,
                prompt=prompt,
                system_prompt="你是一个专业的资讯摘要助手，擅长提取重点并保持客观。"
            )
            end_time = datetime.now(timezone.utc)
            
            processing_time = (end_time - start_time).total_seconds()
            logger.info(f"AI分析完成，处理时间: {processing_time:.2f}秒")
            logger.debug(f"AI响应长度: {len(response.completion_text)}字符")
            
            return response.completion_text
        except Exception as e:
            logger.error(f"AI分析失败: {type(e).__name__}: {e}", exc_info=True)
            return "AI 分析失败，请检查AI提供商配置和网络连接"
    
    def _parse_time_string(self, time_str: str) -> tuple:
        """解析时间字符串为星期和时间部分
        
        Args:
            time_str: 时间字符串，格式如 "周一 09:00"
        
        Returns:
            tuple: (week_day, time_part) 星期部分和时间部分
        
        Raises:
            ValueError: 当时间格式不正确时
        """
        parts = time_str.split()
        if len(parts) != 2:
            raise ValueError(f"时间格式错误，应为 '星期 时间'，实际为: {time_str}")
        
        return parts[0], parts[1]
    
    def _parse_week_day(self, week_day: str) -> str:
        """将中文星期转换为 APScheduler 格式
        
        Args:
            week_day: 中文星期（如"周一"、"二"等）
        
        Returns:
            str: APScheduler 格式的星期（如"mon"、"tue"等）
        """
        week_map = {
            '周一': 'mon',
            '周二': 'tue',
            '周三': 'wed',
            '周四': 'thu',
            '周五': 'fri',
            '周六': 'sat',
            '周日': 'sun',
            '一': 'mon',
            '二': 'tue',
            '三': 'wed',
            '四': 'thu',
            '五': 'fri',
            '六': 'sat',
            '日': 'sun'
        }
        return week_map.get(week_day, 'mon')
    
    async def _send_admin_alert(self, task_name: str, error: Exception, context: dict = None):
        """向管理员发送告警消息
        
        Args:
            task_name: 任务名称
            error: 异常对象
            context: 额外的上下文信息（可选）
        """
        if not self.admin_id:
            logger.warning("未配置管理员ID，无法发送告警")
            return
        
        try:
            from astrbot.api.event import MessageChain
            
            # 构建告警消息
            error_type = type(error).__name__
            error_msg = str(error)
            error_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
            
            alert_message = (
                f"🚨 **插件告警通知**\n\n"
                f"任务名称: {task_name}\n"
                f"错误类型: {error_type}\n"
                f"发生时间: {error_time}\n"
            )
            
            # 添加错误摘要（限制长度）
            if error_msg:
                error_summary = error_msg[:500]
                if len(error_msg) > 500:
                    error_summary += "..."
                alert_message += f"错误摘要: {error_summary}\n"
            
            # 添加额外上下文
            if context:
                alert_message += "\n**详细信息:**\n"
                for key, value in context.items():
                    if value is not None:
                        alert_message += f"- {key}: {value}\n"
            
            alert_message += "\n请检查日志获取详细信息"
            
            # 构建消息链
            message_chain = MessageChain().message(alert_message)
            
            # 发送告警
            admin_umo = f"QQ:FriendMessage:{self.admin_id}"
            await self.context.send_message(admin_umo, message_chain)
            
            logger.info(f"已向管理员 {self.admin_id} 发送告警: {task_name} - {error_type}")
            
        except Exception as e:
            logger.error(f"发送管理员告警失败: {type(e).__name__}: {e}")
    
    def _parse_hour_minute(self, time_part: str) -> tuple:
        """解析时间部分为小时和分钟
        
        Args:
            time_part: 时间字符串，格式如 "09:00"
        
        Returns:
            tuple: (hour, minute) 小时和分钟
        
        Raises:
            ValueError: 当时间格式不正确时
        """
        hour_minute = time_part.split(':')
        if len(hour_minute) != 2:
            raise ValueError(f"时间格式错误，应为 'HH:MM'，实际为: {time_part}")
        
        try:
            hour = int(hour_minute[0])
            minute = int(hour_minute[1])
        except ValueError as e:
            raise ValueError(f"时间必须为数字: {time_part}") from e
        
        # 验证时间范围
        if not (0 <= hour <= 23):
            raise ValueError(f"小时必须在 0-23 之间，实际为: {hour}")
        if not (0 <= minute <= 59):
            raise ValueError(f"分钟必须在 0-59 之间，实际为: {minute}")
        
        return hour, minute
    
    def parse_summary_time(self, time_str: str) -> tuple:
        """解析自动总结时间配置
        
        将时间字符串（如"周一 09:00"）解析为 APScheduler 可用的格式。
        支持完整星期名（周一到周日）和简写（一到日）。
        
        Args:
            time_str: 时间字符串，格式如 "周一 09:00" 或 "一 9:00"
        
        Returns:
            tuple: (day_of_week, hour, minute)
                - day_of_week (str): APScheduler 格式的星期（'mon'到'sun'）
                - hour (int): 小时（0-23）
                - minute (int): 分钟（0-59）
        
        Examples:
            >>> parse_summary_time("周一 09:00")
            ('mon', 9, 0)
            >>> parse_summary_time("三 14:30")
            ('wed', 14, 30)
        
        Note:
            如果解析失败，将返回默认值 ('mon', 9, 0) 并记录警告日志
        """
        try:
            # 步骤1：解析字符串结构
            week_day, time_part = self._parse_time_string(time_str)
            
            # 步骤2：转换星期格式
            day_of_week = self._parse_week_day(week_day)
            
            # 步骤3：解析小时和分钟
            hour, minute = self._parse_hour_minute(time_part)
            
            logger.info(f"解析时间配置: {time_str} -> "
                       f"day_of_week={day_of_week}, hour={hour}, minute={minute}")
            return day_of_week, hour, minute
            
        except Exception as e:
            logger.error(f"解析时间配置失败: {type(e).__name__}: {e}，使用默认值")
            return 'mon', 9, 0
    
    async def push_summary_to_targets(self, summary_text, channel_name):
        """将总结推送到配置的目标
        
        Args:
            summary_text: 总结文本内容
            channel_name: 频道名称
        
        Returns:
            dict: 推送统计信息 {success: 成功数, fail: 失败数}
        """
        from astrbot.api.event import MessageChain
        import random
        
        if not summary_text:
            logger.warning("总结内容为空，跳过推送")
            return {'success': 0, 'fail': 0}
        
        if not self.auto_push_groups and not self.auto_push_users:
            logger.info("未配置推送目标，跳过推送")
            return {'success': 0, 'fail': 0}
        
        # 使用配置的消息模板构建推送消息
        title_template = self.message_templates.get('summary_title', '【频道周报】{channel_name}')
        footer_template = self.message_templates.get('summary_footer', '')
        
        # 格式化标题
        title = title_template.format(channel_name=channel_name)
        
        # 构建完整消息
        push_message = f"{title}\n\n{summary_text}"
        if footer_template:
            push_message += f"\n\n{footer_template}"
        
        # 构建消息链
        message_chain = MessageChain().message(push_message)
        
        success_count = 0
        fail_count = 0
        
        # 构建推送目标列表（群组 + 用户）
        targets = []
        for group_id in self.auto_push_groups:
            targets.append(f"QQ:GroupMessage:{group_id}")
        for user_id in self.auto_push_users:
            targets.append(f"QQ:FriendMessage:{user_id}")
        
        logger.info(f"准备推送到 {len(targets)} 个目标: {targets}")
        
        # 遍历所有推送目标
        for i, umo in enumerate(targets):
            try:
                # 发送消息
                await self.context.send_message(umo, message_chain)
                logger.info(f"成功推送到目标 {umo}")
                success_count += 1
                
                # 随机延迟，避免触发频率限制
                if i < len(targets) - 1:
                    await asyncio.sleep(random.uniform(1, 3))
            except Exception as e:
                logger.error(f"推送到目标 {umo} 失败: {type(e).__name__}: {e}")
                fail_count += 1
        
        logger.info(f"推送完成: 成功 {success_count} 个, 失败 {fail_count} 个")
        return {
            'success': success_count,
            'fail': fail_count
        }
    
    async def main_job(self):
        """主定时任务：每周一生成频道消息总结"""
        start_time = datetime.now(timezone.utc)
        logger.info(f"定时任务启动: {start_time}")
        
        # 检查session文件是否存在
        if not os.path.exists(self.USER_SESSION_FILE):
            logger.warning(f"用户会话文件不存在: {self.USER_SESSION_FILE}，跳过本次自动总结任务")
            logger.info("请管理员使用 /tg_login 命令完成首次登录，之后将正常执行自动总结")
            return
        
        # 统计信息
        total_channels = 0
        empty_channels = 0
        total_push_success = 0
        total_push_fail = 0
        
        try:
            messages_by_channel = await self.fetch_last_week_messages()
            
            if not messages_by_channel:
                logger.info("没有需要处理的频道")
                return
            
            # 按频道分别生成总结报告
            for channel, messages in messages_by_channel.items():
                logger.info(f"开始处理频道 {channel} 的消息")
                total_channels += 1
                
                # 检查是否有消息
                if not messages:
                    logger.info(f"频道 {channel} 本周无新消息，跳过AI分析和推送")
                    empty_channels += 1
                    
                    # 更新该频道的上次总结时间（即使没有消息也要更新）
                    current_utc_time = datetime.now(timezone.utc)
                    self.last_summary_times[channel] = current_utc_time
                    continue
                
                # 调用AI生成总结
                summary = await self.analyze_with_ai(messages)
                
                # 检查总结是否为空或失败
                if not summary or summary.startswith("AI 分析失败"):
                    logger.warning(f"频道 {channel} 总结生成失败或为空，跳过推送")
                    total_push_fail += len(self.auto_push_groups) + len(self.auto_push_users)
                    
                    # 更新该频道的上次总结时间
                    current_utc_time = datetime.now(timezone.utc)
                    self.last_summary_times[channel] = current_utc_time
                    continue
                
                # 获取频道名称用于报告标题
                channel_name = channel.split('/')[-1]
                
                # 记录到日志
                logger.info(f"频道 {channel} 总结已生成")
                
                # 自动推送到配置的目标
                push_result = await self.push_summary_to_targets(summary, channel_name)
                total_push_success += push_result['success']
                total_push_fail += push_result['fail']
                
                # 更新该频道的上次总结时间
                current_utc_time = datetime.now(timezone.utc)
                self.last_summary_times[channel] = current_utc_time
            
            # 保存所有频道的上次总结时间
            self.save_last_summary_times(self.last_summary_times)
            logger.info(f"已更新各频道的上次总结时间")
            
            end_time = datetime.now(timezone.utc)
            processing_time = (end_time - start_time).total_seconds()
            
            # 输出统计日志
            logger.info(f"【自动推送】总结完成。处理频道: {total_channels} 个，"
                       f"无消息频道: {empty_channels} 个，"
                       f"已推送至 {total_push_success} 个目标（群组和用户）。失败: {total_push_fail}。")
            logger.info(f"定时任务完成: {end_time}，总处理时间: {processing_time:.2f}秒")
        except Exception as e:
            end_time = datetime.now(timezone.utc)
            processing_time = (end_time - start_time).total_seconds()
            logger.error(f"定时任务执行失败: {type(e).__name__}: {e}，开始时间: {start_time}，结束时间: {end_time}，处理时间: {processing_time:.2f}秒")
            
            # 发送管理员告警
            await self._send_admin_alert(
                task_name="自动总结定时任务",
                error=e,
                context={
                    "开始时间": start_time.strftime("%Y-%m-%d %H:%M:%S UTC"),
                    "结束时间": end_time.strftime("%Y-%m-%d %H:%M:%S UTC"),
                    "处理时间": f"{processing_time:.2f}秒",
                    "处理频道数": total_channels,
                    "无消息频道数": empty_channels,
                    "推送成功": total_push_success,
                    "推送失败": total_push_fail
                }
            )
    
    # ========== 命令处理 ==========
    
    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("summary")
    async def handle_manual_summary(self, event: AstrMessageEvent):
        """立即生成本周频道消息总结"""
        sender_id = event.get_sender_id()
        command = event.message_str
        logger.info(f"收到命令: {command}，发送者: {sender_id}")
        
        # 检查session文件是否存在
        if not os.path.exists(self.USER_SESSION_FILE):
            logger.info(f"用户会话文件不存在: {self.USER_SESSION_FILE}，自动进入登录流程")
            yield event.plain_result(
                "⚠️ **未检测到登录信息**\n\n"
                "请先完成 Telegram 登录才能使用此功能。\n"
                "正在自动启动登录流程..."
            )
            # 调用登录处理
            async for msg in self.handle_tg_login(event):
                yield msg
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
                        # 频道名称
                        specified_channels.append(part)
                
                # 验证指定的频道是否在配置中
                valid_channels = []
                for channel in specified_channels:
                    # 使用统一的 _match_channel 方法进行智能匹配
                    matched = False
                    for config_channel in self.CHANNELS:
                        if self._match_channel(channel, config_channel):
                            valid_channels.append(config_channel)
                            matched = True
                            break
                    
                    if not matched:
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
                yield event.plain_result(f"✈️ {channel_name} 频道周报总结\n\n{summary}")
                
                # 更新该频道的上次总结时间
                current_utc_time = datetime.now(timezone.utc)
                self.last_summary_times[channel] = current_utc_time
                logger.info(f"已更新频道 {channel} 的上次总结时间: {current_utc_time}")
            
            # 保存所有频道的上次总结时间
            self.save_last_summary_times(self.last_summary_times)
            logger.info(f"已保存各频道的上次总结时间")
            
            logger.info(f"命令 {command} 执行成功")
        except Exception as e:
            logger.error(f"执行命令 {command} 时出错: {type(e).__name__}: {e}", exc_info=True)
            yield event.plain_result("❌ 生成总结时出错，请检查日志获取详细信息")
    
    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("showprompt")
    async def handle_show_prompt(self, event: AstrMessageEvent):
        """查看当前提示词"""
        sender_id = event.get_sender_id()
        command = event.message_str
        logger.info(f"收到命令: {command}，发送者: {sender_id}")
        
        logger.info(f"执行命令 {command} 成功")
        yield event.plain_result(f"当前提示词：\n\n{self.CURRENT_PROMPT}")
    
    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("setprompt")
    async def handle_set_prompt(self, event: AstrMessageEvent):
        """设置自定义提示词"""
        from astrbot.core.utils.session_waiter import session_waiter, SessionController
        
        sender_id = event.get_sender_id()
        command = event.message_str
        logger.info(f"收到命令: {command}，发送者: {sender_id}")
        
        # 使用锁保护共享状态
        async with self._setting_prompt_lock:
            # 检查用户是否已经在设置提示词
            if sender_id in self.setting_prompt_users:
                yield event.plain_result("您已经在设置提示词的过程中，请先完成当前设置")
                return
            
            # 添加用户到正在设置提示词的集合中
            self.setting_prompt_users.add(sender_id)
            logger.info(f"添加用户 {sender_id} 到提示词设置集合")
        
        yield event.plain_result(f"请发送新的提示词，我将使用它来生成总结。\n\n当前提示词：\n{self.CURRENT_PROMPT}")
        
        @session_waiter(timeout=60, record_history_chains=False)
        async def setprompt_session(controller: SessionController, event: AstrMessageEvent):
            """设置提示词的会话处理"""
            sender_id = event.get_sender_id()
            new_prompt = event.message_str.strip()
            
            logger.info(f"用户 {sender_id} 提交了新的提示词，长度: {len(new_prompt)} 字符")
            
            # 使用锁保护共享状态的修改
            async with self._setting_prompt_lock:
                # 更新提示词
                self.CURRENT_PROMPT = new_prompt
                
                # 保存到文件
                self.save_prompt(new_prompt)
                
                # 从正在设置提示词的集合中移除用户
                if sender_id in self.setting_prompt_users:
                    self.setting_prompt_users.remove(sender_id)
                    logger.info(f"从提示词设置集合中移除用户 {sender_id}")
            
            logger.info(f"用户 {sender_id} 成功更新提示词")
            await event.send(event.plain_result("✅ 提示词已成功更新！"))
        
        try:
            await setprompt_session(event)
        except TimeoutError:
            # 超时处理
            async with self._setting_prompt_lock:
                if sender_id in self.setting_prompt_users:
                    self.setting_prompt_users.remove(sender_id)
                    logger.info(f"用户 {sender_id} 设置提示词超时，已从集合中移除")
            yield event.plain_result("⏱️ 设置提示词超时，请重新使用 /setprompt 命令")
        except Exception as e:
            logger.error(f"设置提示词会话异常: {type(e).__name__}: {e}", exc_info=True)
            async with self._setting_prompt_lock:
                if sender_id in self.setting_prompt_users:
                    self.setting_prompt_users.remove(sender_id)
            yield event.plain_result("❌ 设置提示词时出错，请稍后重试")
    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("showchannels")
    async def handle_show_channels(self, event: AstrMessageEvent):
        """查看当前频道列表"""
        sender_id = event.get_sender_id()
        command = event.message_str
        logger.info(f"收到命令: {command}，发送者: {sender_id}")
        
        logger.info(f"执行命令 {command} 成功")
        
        if not self.CHANNELS:
            yield event.plain_result("当前没有配置任何频道")
            return
        
        # 构建频道列表消息
        channels_msg = "当前配置的频道列表：\n\n"
        for i, channel in enumerate(self.CHANNELS, 1):
            channels_msg += f"{i}. {channel}\n"
        
        yield event.plain_result(channels_msg)
    
    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("addchannel")
    async def handle_add_channel(self, event: AstrMessageEvent):
        """添加频道"""
        sender_id = event.get_sender_id()
        command = event.message_str
        logger.info(f"收到命令: {command}，发送者: {sender_id}")
        
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
            
            # 保存到AstrBot配置系统
            self.config['channels'] = self.CHANNELS
            self.config.save_config()
            
            logger.info(f"已添加频道 {channel_url} 到列表并保存到配置文件")
            yield event.plain_result(f"频道 {channel_url} 已成功添加到列表中\n\n当前频道数量：{len(self.CHANNELS)}")
            
        except ValueError:
            # 没有提供频道URL
            yield event.plain_result("请提供有效的频道URL，例如：/addchannel https://t.me/examplechannel")
        except Exception as e:
            logger.error(f"添加频道时出错: {type(e).__name__}: {e}", exc_info=True)
            yield event.plain_result("❌ 添加频道失败，请检查输入格式和网络连接")
    
    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("deletechannel")
    async def handle_delete_channel(self, event: AstrMessageEvent):
        """删除频道"""
        sender_id = event.get_sender_id()
        command = event.message_str
        logger.info(f"收到命令: {command}，发送者: {sender_id}")
        
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
            
            # 保存到AstrBot配置系统
            self.config['channels'] = self.CHANNELS
            self.config.save_config()
            
            logger.info(f"已从列表中删除频道 {channel_url} 并保存到配置文件")
            yield event.plain_result(f"频道 {channel_url} 已成功从列表中删除\n\n当前频道数量：{len(self.CHANNELS)}")
            
        except ValueError:
            # 没有提供频道URL或频道不存在
            yield event.plain_result("请提供有效的频道URL，例如：/deletechannel https://t.me/examplechannel")
        except Exception as e:
            logger.error(f"删除频道时出错: {type(e).__name__}: {e}", exc_info=True)
            yield event.plain_result("❌ 删除频道失败，请稍后重试")
    
    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("clearsummarytime")
    async def handle_clear_summary_time(self, event: AstrMessageEvent):
        """清除上次总结时间记录"""
        sender_id = event.get_sender_id()
        command = event.message_str
        logger.info(f"收到命令: {command}，发送者: {sender_id}")
        
        try:
            # 删除上次总结时间文件
            import os
            if os.path.exists(self.LAST_SUMMARY_FILE):
                os.remove(self.LAST_SUMMARY_FILE)
                logger.info(f"已删除上次总结时间文件: {self.LAST_SUMMARY_FILE}")
            
            # 重置内存中的上次总结时间
            self.last_summary_times = {}
            self.save_last_summary_times(self.last_summary_times)
            
            yield event.plain_result("所有频道的上次总结时间记录已成功清除\n\n下次总结将使用默认时间范围（过去7天）")
        except Exception as e:
            logger.error(f"清除上次总结时间时出错: {type(e).__name__}: {e}", exc_info=True)
            yield event.plain_result("❌ 清除记录失败，请检查文件权限")
    
    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("tg_login")
    async def handle_tg_login(self, event: AstrMessageEvent):
        """开始Telegram交互式登录流程
        
        使用状态机模式实现多步交互。
        """
        from astrbot.core.utils.session_waiter import session_waiter, SessionController
        
        sender_id = event.get_sender_id()
        logger.info(f"用户 {sender_id} 请求进行Telegram登录")
        
        # 使用锁保护登录状态
        async with self._login_states_lock:
            # 检查用户是否已经在登录流程中
            if sender_id in self.login_states:
                yield event.plain_result("您已经在登录流程中，请先完成或使用 /tg_login 重新开始")
                return
            
            # 初始化用户的登录状态
            self.login_states[sender_id] = {
                'stage': 'phone',
                'phone': None,
                'client': None,
                'session_file': None
            }
        
        # 提示用户输入手机号
        yield event.plain_result(
            "🚀 **开始 Telegram 登录流程**\n\n"
            "请输入您的手机号（必须带国家代码）\n"
            "示例：`+8613812345678`\n\n"
            "⏱️ 会话将在 120 秒后超时"
        )
        
        @session_waiter(timeout=120, record_history_chains=False)
        async def tg_login_session(controller: SessionController, event: AstrMessageEvent):
            sender_id = event.get_sender_id()
            
            # 检查用户是否在登录流程中
            if sender_id not in self.login_states:
                await event.send(event.plain_result("登录会话已过期，请使用 `/tg_login` 重新开始"))
                controller.stop()
                return
            
            login_state = self.login_states[sender_id]
            stage = login_state['stage']
            user_input = event.message_str.strip()
            
            # 检查是否要退出
            if user_input == "退出":
                await event.send(event.plain_result("已取消登录"))
                await self._cleanup_login_session(sender_id)
                controller.stop()
                return
            
            # 使用 match-case 处理不同登录阶段（Python 3.10+）
            match stage:
                case 'phone':
                    success, should_stop = await self._handle_phone_stage(event, user_input, login_state, sender_id)
                    if not should_stop:
                        controller.keep(timeout=120, reset_timeout=True)
                
                case 'code':
                    success, should_stop = await self._handle_code_stage(event, user_input, login_state, sender_id)
                    if not should_stop:
                        controller.keep(timeout=120, reset_timeout=True)
                
                case 'password':
                    await self._handle_password_stage(event, user_input, login_state, sender_id)
                
                case _:
                    logger.error(f"未知的登录阶段: {stage}")
                    await event.send(event.plain_result("登录状态异常，请使用 `/tg_login` 重新开始"))
                    await self._cleanup_login_session(sender_id)
                    controller.stop()
        
        try:
            await tg_login_session(event)
        except TimeoutError:
            # 清理登录状态
            if sender_id in self.login_states and self.login_states[sender_id].get('client'):
                await self.login_states[sender_id]['client'].disconnect()
            if sender_id in self.login_states:
                del self.login_states[sender_id]
            yield event.plain_result("⏱️ 登录会话已超时，请使用 `/tg_login` 重新开始")
        except Exception as e:
            logger.error(f"tg_login会话异常: {type(e).__name__}: {e}", exc_info=True)
            # 清理登录状态
            if sender_id in self.login_states and self.login_states[sender_id].get('client'):
                await self.login_states[sender_id]['client'].disconnect()
            if sender_id in self.login_states:
                del self.login_states[sender_id]
            yield event.plain_result("❌ 登录过程出错，请检查网络连接和账号信息")
        finally:
            event.stop_event()
    async def terminate(self):
        """插件被卸载/停用时会调用。"""
        logger.info("插件正在被卸载，停止调度器...")
        if hasattr(self, 'scheduler'):
            self.scheduler.shutdown()
            logger.info("调度器已停止")
