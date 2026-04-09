import time, sys
import asyncio, re
from astrbot.api.all import Star, EventMessageType, logger
from astrbot.api.event import filter
from astrbot.core import AstrBotConfig
from astrbot.core.message.components import At, Plain, Reply
from astrbot.core.star import Context
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent
op = time.perf_counter()

class 黑名单系统(Star):

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config

        # ======获取配置======
        try:
            self.黑名单群聊:list[str] = config['黑名单群聊']
            self.黑名单列表:list[str] = config['黑名单用户']
            self.不包括指令:bool = not config['包括指令']
            self.闭嘴列表:list[str] = config['闭嘴列表']
            self.显示日志:bool = config['显示日志']
        except Exception as e:
            logger.critical(f"致命错误！获取配置失败，请重新安装插件，或联系开发者\n错误信息：{str(e)}", exc_info=True)
            raise RuntimeError

        # ======获取系统配置======
        try:
            self.指令前缀 = context.get_config()["wake_prefix"]
        except Exception as e:
            logger.error(f"获取指令前缀失败，使用默认值 '/': {e}")
            self.指令前缀 = ["/"]
        self.指令前缀 = tuple(self.指令前缀)

        try:
            self.管理员列表 = context.get_config()["admins_id"]
        except Exception as e:
            logger.error("获取管理员列表失败，你可在代码中手动配置管理员，错误信息：\n" + str(e))
            self.管理员列表 = []

        self.格式化黑名单用户 = []
        self.黑名单字典 = {}

        # 格式化黑名单用户（使用竖线分隔）
        for entry in self.黑名单列表:
            分割 = entry.split('|')
            分割 = [s.strip() for s in 分割]  # 去除两端空白

            用户ID = 分割[0]
            try:
                结束时间 = float(分割[1])
            except (ValueError, IndexError):
                结束时间 = 5102444800  # 2131年
            try:
                名字 = 分割[2]
            except IndexError:
                名字 = '未知'
            try:
                理由 = 分割[3]
            except IndexError:
                理由 = '未知'
            try:
                群号 = 分割[4]
            except IndexError:
                群号 = '全局'
            try:
                操作者 = 分割[5]
            except IndexError:
                操作者 = 'WebUI'

            # 用竖线重新生成标准格式
            self.格式化黑名单用户.append(
                f"{用户ID}|{结束时间 if 结束时间 < 4102444800 else '永久'}|{名字}|{理由}|{群号}|{操作者}"
            )
            if 群号 not in self.黑名单字典:
                self.黑名单字典[群号] = {}
            self.黑名单字典[群号][用户ID] = 结束时间
        self.群闭嘴结束时间 = {}
        for i in self.闭嘴列表:
            i = i.split(':')
            try:
                self.群闭嘴结束时间[i[0]] = float(i[1])
            except (ValueError, IndexError, TypeError):
                pass

        # 同步
        self.黑名单列表 = self.格式化黑名单用户
        config['黑名单用户'] = self.黑名单列表
        self.config.save_config()

        self.ID名字 = {}
        self.解除拉黑正则 = re.compile(r'解除拉黑\s*(\d+)')
        self.time_pattern = re.compile(r'(?:(\d+)|([一二三四五六七八九十两]+))\s*(分钟?|小时?|天|个月?|年)')
        self.id_pattern = re.compile(r'\b(\d{5,12})\b')
        self.reason_pattern = re.compile(r'理由[:：]?\s*(.*?)(?=\s*(?:理由|名字|，|,|$))', re.DOTALL)
        self.name_pattern = re.compile(r'名字[:：]?\s*(.*?)(?=\s*(?:理由|名字|，|,|$))', re.DOTALL)

        self.黑名单锁 = asyncio.Lock()
        self.闭嘴锁 = asyncio.Lock()

        ed = time.perf_counter()
        耗时 = ed - op
        logger.info(f"启动完成，耗时{耗时:.6f}秒")

    @filter.event_message_type(EventMessageType.ALL, priority=sys.maxsize-1)
    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    async def 入口(self, event: AiocqhttpMessageEvent):
        """消息主入口"""
        if not (消息链:=event.get_messages()):
            return

        if event.get_message_str() == '查看黑名单':
            return

        发送者 = event.get_sender_id()
        当前时间 = time.time()

        if not (群号 := event.get_group_id()):
            if 发送者 in self.黑名单字典.get("私聊", {}):
                if 当前时间 < self.黑名单字典['私聊'][发送者]:
                    event.stop_event()
                    return
            return #私聊只有这个处理

        if 群号 in self.黑名单群聊:
            if event.is_admin():
                return
            event.stop_event()
            return

        if event.is_admin() or self.是管理员(event):
            if await self.开闭嘴(event, 当前时间):
                return

        处理文本 = next((seg.text for seg in 消息链 if isinstance(seg, Plain)), "").strip()

        if event.is_admin():  # 是管理员
            await self.管理员命令(event, 处理文本)
            return

        if 当前时间 < self.群闭嘴结束时间.get(群号, 0):
            event.stop_event()
            return
        if 发送者 in self.黑名单字典.get(群号, {}):
            if 当前时间 < self.黑名单字典[群号][发送者]:
                if self.不包括指令 and 处理文本.startswith(self.指令前缀):
                    return
                event.stop_event()
                return
            else:
                await self.移除黑名单(黑名单用户=发送者, 群ID=群号)
        elif 发送者 in self.黑名单字典.get('全局', {}):
            if 当前时间 < self.黑名单字典['全局'][发送者]:
                if self.不包括指令 and 处理文本.startswith(self.指令前缀):
                    return
                if self.显示日志:
                    logger.info(f"用户{发送者}已被拉黑")
                event.stop_event()
                return
            else:
                await self.移除黑名单(黑名单用户=发送者, 群ID='全局')

    @filter.on_llm_request(priority=5201314)
    async def llm请求前(self, event: AiocqhttpMessageEvent, _):
        """兜底彻底拦截llm"""
        if event.is_admin():
            return
        if not (群号 := event.get_group_id()):
            if event.get_sender_id() in self.黑名单字典.get("私聊", {}):
                event.stop_event()
                return
        if 群号 in self.黑名单群聊:
            event.stop_event()
            return
        if time.time() < self.群闭嘴结束时间.get(群号, 0):
            event.stop_event()
            return
        发送者 = event.get_sender_id()
        当前时间 = time.time()
        处理文本 = next((seg.text for seg in event.get_messages() if isinstance(seg, Plain)), "").strip()

        if 发送者 in self.黑名单字典.get(群号, {}):
            if 当前时间 < self.黑名单字典[群号][发送者]:
                if self.不包括指令 and 处理文本.startswith(self.指令前缀):
                    return
                event.stop_event()
                return
            else:
                await self.移除黑名单(黑名单用户=发送者, 群ID=群号)
        elif 发送者 in self.黑名单字典.get('全局', {}):
            if 当前时间 < self.黑名单字典['全局'][发送者]:
                if self.不包括指令 and 处理文本.startswith(self.指令前缀):
                    return
                if self.显示日志:
                    logger.info(f"用户{发送者}已被拉黑")
                event.stop_event()
                return
            else:
                await self.移除黑名单(黑名单用户=发送者, 群ID='全局')

    async def 开闭嘴(self, event: AiocqhttpMessageEvent, 当前时间):
        消息链 = event.get_messages()
        消息文本 = event.get_message_str()
        群号 = event.get_group_id()

        if 消息文本 == "开嘴":
            for seg in 消息链:
                if isinstance(seg, At):
                    if str(seg.qq) != event.get_self_id():
                        return True
                    if self.群闭嘴结束时间.get(群号, 0):
                        async with self.闭嘴锁:
                            self.群闭嘴结束时间[群号] = 0
                            self.闭嘴列表[:] = [i for i in self.闭嘴列表 if i.split(':')[0] != 群号]
                            self.config.save_config()
                        await self.发送回复文本(event, "")
                    return True
            return True

        if 消息文本.startswith('闭嘴'):
            for seg in 消息链:
                if isinstance(seg, At):
                    if str(seg.qq) != event.get_self_id():
                        return True
                    # 1. 匹配时间模式（数字 + 时间词）
                    time_match = self.time_pattern.search(消息文本)
                    if time_match:
                        num_str = time_match.group(1) or time_match.group(2)
                        unit = time_match.group(3)
                        if num_str.isdigit():
                            num = int(num_str)
                        else:
                            num = self._chinese_to_int(num_str) or 0
                        时长 = int(self.时间转换(unit, num))
                        async with self.闭嘴锁:
                            self.群闭嘴结束时间[群号] = 当前时间 + 时长 * 60
                            self.闭嘴列表[:] = [i for i in self.闭嘴列表 if i.split(':')[0] != 群号]
                            self.闭嘴列表.append(f"{群号}:{当前时间 + 时长 * 60}")
                            self.config.save_config()
                        # logger.debug(f"闭嘴时长{时长}秒")
                        event.stop_event()
                        await self.发送回复文本(event, f"我将闭嘴{self._格式化时长显示(时长)}")
                    return True
            return True
        return False

    async def 管理员命令(self, event: AiocqhttpMessageEvent, 处理文本):
        群号 = event.get_group_id()
        for i in self.指令前缀:
            if 处理文本.startswith(i):
                处理文本 = 处理文本[len(i):]
                break

        if 处理文本.startswith('拉黑'):
            rest = 处理文本[2:].strip()
            if not rest:
                # 只有“拉黑”，从消息中提取用户
                结果 = await self.加入黑名单(event, 理由='无', 操作者ID='管理员')
            else:
                parsed = self._parse_blacklist_command(rest)
                user_id = parsed['user_id'] or None  # 若为None则从消息事件中获取，提示使用便利
                duration_minutes = parsed['duration'] or None
                reason = parsed['reason']
                name = parsed['name']
                # 执行拉黑
                结果 = await self.加入黑名单(
                    event,
                    黑名单用户=user_id,
                    时长=duration_minutes,
                    理由=reason,
                    名字=name,
                    操作者ID='管理员'
                )
            if 结果:
                await self.发送回复文本(event, 结果)
                event.stop_event()
                return

        elif 处理文本.startswith('解除拉黑'):
            if (黑名单 := self.解除拉黑正则.search(处理文本)) and (4 < len(黑名单[1]) < 12):
                结果 = await self.移除黑名单(event, 黑名单[1], 群号)
            elif 处理文本 == '解除拉黑':
                结果 = await self.移除黑名单(event, 群ID=群号)
            else:
                结果 = False
            if 结果:
                await self.发送回复文本(event, 结果)
                event.stop_event()
                return

    @staticmethod
    def _chinese_to_int(s: str) -> int | None:
        """将中文数字字符串转换为整数，支持0~99（如“十二”、“三十一”）"""
        mapping = {'零': 0, '一': 1, '二': 2, '两': 2, '三': 3, '四': 4,
                   '五': 5, '六': 6, '七': 7, '八': 8, '九': 9, '十': 10}
        s = s.strip()
        if not s:
            return None
        # 单字直接映射
        if len(s) == 1:
            return mapping.get(s)
        # 处理含“十”的两位数（如“十二”、“二十”、“二十一”）
        if '十' in s:
            parts = s.split('十')
            left = parts[0] if parts[0] else ''  # “十”左边可能为空
            right = parts[1] if len(parts) > 1 else ''
            left_val = mapping.get(left, 1) if left else 1  # 左边为空时视为1（如“十”）
            right_val = mapping.get(right, 0) if right else 0
            return left_val * 10 + right_val
        return None

    def _parse_blacklist_command(self, text: str):
        """
        解析拉黑指令文本，提取用户ID、时长（分钟）、理由、名字。
        返回字典：{'user_id': str or None, 'duration': int or None, 'reason': str or None, 'name': str or None}
        """
        # 初始化结果
        result = {
            'user_id': '',
            'duration': 0,
            'reason': '无',
            'name': '名字未知'
        }
        rest = text

        # 1. 匹配时间模式（数字 + 时间词）
        time_match = self.time_pattern.search(rest)
        if time_match:
            num_str = time_match.group(1) or time_match.group(2)
            unit = time_match.group(3)
            if num_str.isdigit():
                num = int(num_str)
            else:
                num = self._chinese_to_int(num_str) or 0

            # 转换为分钟（增加对年、月的支持）
            result['duration'] = self.时间转换(unit, num)

            # 移除时间部分
            rest = rest[:time_match.start()] + rest[time_match.end():]

        # 2. 匹配用户ID（纯数字，5~12位）
        id_match = self.id_pattern.search(rest)
        if id_match:
            result['user_id'] = id_match.group(1)
            rest = rest[:id_match.start()] + rest[id_match.end():]

        # 按关键词查找（理由、名字）
        reason_match = self.reason_pattern.search(rest)
        if reason_match:
            result['reason'] = reason_match.group(1).strip()
            rest = rest[:reason_match.start()] + rest[reason_match.end():]
        # 名字
        name_match = self.name_pattern.search(rest)
        if name_match:
            result['name'] = name_match.group(1).strip()

        # 剩余部分可以忽略（或作为额外信息，暂不处理）
        return result

    @staticmethod
    def 时间转换(unit, num) -> int:
        """注意返回分钟"""
        if '年' in unit:
            return num * 365 * 24 * 60
        elif '月' in unit:  # 匹配“月”或“个月”
            return num * 30 * 24 * 60
        elif unit.startswith('小时') or unit.startswith('时'):
            return num * 60
        elif unit == '天':
            return num * 24 * 60
        elif unit.startswith('分'):  # “分”或“分钟”
            return num
        else:
            return num  # 兜底

    @filter.command("查看黑名单", alias={'黑名单', '黑名单列表'})
    async def 黑名单列表指令(self, event: AiocqhttpMessageEvent):
        """查看黑名单列表"""
        分割 = event.message_str.split()
        if len(分割) > 1 and 分割[1] == 'all' and event.is_admin():
            结果 = await self.格式化黑名单列表(event, notall=False)
        else:
            结果 = await self.格式化黑名单列表(event)
        if 结果 == 'ℹ️ 还没有黑名单用户哦':
            await self.发送回复文本(event, 结果)
            return
        if event.is_admin():
            await self.发送回复文本(event, 结果 + '\n\n使用“解除拉黑 用户ID”解除拉黑')
        else:
            await self.发送回复文本(event, 结果)

    @filter.command("全局拉黑")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def 全局拉黑(self, event: AiocqhttpMessageEvent):
        """全局拉黑"""
        try:
            黑名单用户 = str(int(event.message_str.split()[1]))
            结果 = await self.加入黑名单(event, 黑名单用户, 操作者ID='管理员', 群ID='全局')
        except (ValueError, IndexError):
            结果 = await self.加入黑名单(event, 操作者ID='管理员', 群ID='全局')
        if 结果:
            await self.发送回复文本(event, 结果)
        else:
            await self.发送回复文本(event, "拉黑失败")

    @filter.command("全局解除拉黑")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def 全局解除拉黑(self, event: AiocqhttpMessageEvent):
        """全局解除拉黑"""
        分割 = event.message_str.split()
        if len(分割) > 1:
            结果 = await self.移除黑名单(event, 分割[1], '全局')
            if 结果:
                await self.发送回复文本(event, 结果)
            else:
                await self.发送回复文本(event, "解除拉黑失败")
        else:
            await self.发送回复文本(event, "格式不对，格式为：/解除拉黑 用户ID")

    @filter.command("清空黑名单")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def 清空黑名单(self, event: AiocqhttpMessageEvent):
        """
        清空黑名单指令
        用法：
            /清空黑名单            清空当前群的黑名单（仅在群聊中可用）
            /清空黑名单 all        清空所有群的黑名单（包括全局）
            /清空黑名单 123456    清空指定群号（123456）的黑名单
        """
        分割 = event.message_str.split()
        当前群号 = event.get_group_id()

        # 参数解析
        if len(分割) > 1:
            param = 分割[1].lower()
            if param == 'all':
                清空全部 = True
                指定群号 = None
            elif param.isdigit():
                清空全部 = False
                指定群号 = param
            else:
                await self.发送回复文本(event, "❌ 参数错误，请使用：/清空黑名单 [all|群号]")
                return
        else:
            # 无参数时，必须处于群聊中
            if not 当前群号:
                await self.发送回复文本(event, "❌ 请在群聊中使用或指定要清空的群号：/清空黑名单 群号")
                return
            清空全部 = False
            指定群号 = 当前群号

        # 检查黑名单是否为空
        if not self.黑名单字典:
            await self.发送回复文本(event, "ℹ️ 黑名单已经是空的啦~")
            return

        # 清空所有群
        if 清空全部:
            self.黑名单列表.clear()
            self.黑名单字典.clear()
            self.config.save_config()
            await self.发送回复文本(event, "✅ 已清空所有群的黑名单（包括全局）")
            return

        # 清空指定群（包括当前群）
        if 指定群号 not in self.黑名单字典:
            await self.发送回复文本(event, f"ℹ️ 群 {指定群号} 的黑名单已经是空的啦~")
            return

        # 从黑名单列表中移除该群的所有条目
        async with self.黑名单锁:
            原条目数 = len(self.黑名单列表)
            self.黑名单列表[:] = [i for i in self.黑名单列表 if i.split('|')[4] != 指定群号]
            移除条目数 = 原条目数 - len(self.黑名单列表)
            # 从字典中删除该群
            del self.黑名单字典[指定群号]
            self.config.save_config()
        await self.发送回复文本(event, f"✅ 已清空群 {指定群号} 的黑名单，共移除 {移除条目数} 条记录")

    async def 格式化黑名单列表(self, event: AiocqhttpMessageEvent, notall: bool = True):
        """重大优化，使用群组隔离防止在一个群出现另一个群的用户导致另一个群的用户可能被骚扰"""
        if not self.黑名单字典:
            return 'ℹ️ 还没有黑名单用户哦'
        当前时间 = time.time()
        for 群号, 群字典 in self.黑名单字典.copy().items():
            for 用户ID, 结束时间 in 群字典.copy().items():
                if 当前时间 > 结束时间:
                    await self.移除黑名单(黑名单用户=用户ID, 群ID=群号)
        if not self.黑名单字典:
            return 'ℹ️ 还没有黑名单用户哦'
        群ID = event.get_group_id()

        格式化列表 = []
        for entry in self.黑名单列表:
            分割 = entry.split('|')
            if notall and 分割[4] != 群ID:
                continue
            if 分割[1] == '永久':
                分割[1] = '∞'
            else:
                分割[1] = self._格式化时长显示((self.黑名单字典[分割[4]][分割[0]] - 当前时间) // 60 + 1)
            if notall:
                分割 = 分割[:4]
            格式化列表.append(' | '.join(分割))
        if not 格式化列表:
            return 'ℹ️ 还没有黑名单用户哦'
        if notall:
            表头 = f'有{len(格式化列表)}个用户在黑名单，格式：\n用户ID | 剩余时间 | 用户名 | 拉黑理由\n\n'
        else:
            表头 = f'有{len(格式化列表)}个用户在黑名单，格式：\n用户ID | 剩余时间 | 用户名 | 拉黑理由 | 群ID | 操作者\n\n'
        return 表头 + '\n\n'.join(格式化列表)

    async def 加入黑名单(self,
                         event: AiocqhttpMessageEvent = None,
                         黑名单用户: str = None,
                         名字: str = '名字未知',
                         时长: int | float = None,
                         理由: str = '无',
                         操作者ID: str = None,
                         群ID: str = None
                         ):
        """时长：分钟"""
        当前时间 = time.time()
        if (时长 is None) or (时长 == -1):
            结束时间 = 5102444800
        else:
            结束时间 = float(当前时间 + 时长 * 60)
        if 黑名单用户 is None:
            if event is None:
                return False
            自己 = event.get_self_id()
            for seg in event.get_messages():
                if isinstance(seg, At):
                    黑名单用户 = str(seg.qq)
                    if 黑名单用户 == 自己:
                        return False
                    名字 = seg.name
                    break
                if isinstance(seg, Reply):
                    黑名单用户 = str(seg.qq)
                    名字 = seg.sender_nickname
                    break
            else:
                return False
        if 群ID is None:
            if event is None:
                群ID = '全局'
            else:
                群ID = event.get_group_id()
        if 操作者ID is None:
            if event is None:
                操作者ID = '操作者未知'
            else:
                操作者ID = event.get_sender_id()

        if 名字 == '名字未知' and 群ID != '全局':
            名字 = await self.获取用户名(event, 黑名单用户, 群ID)

        async with self.黑名单锁:
            # 先删掉可能存在的旧条目
            self.黑名单列表[:] = [
                i for i in self.黑名单列表
                if not (i.split('|')[4] == 群ID and i.split('|')[0] == 黑名单用户)
            ]
            self.黑名单列表.append(
                f"{黑名单用户}|{结束时间 if 结束时间 < 4102444800 else '永久'}|"
                f"{名字}|{理由}|{群ID}|{操作者ID}"
            )
            if 群ID not in self.黑名单字典:
                self.黑名单字典[群ID] = {}
            self.黑名单字典[群ID][黑名单用户] = 结束时间
            self.config.save_config()
        return (f"✅ 已添加\n「{名字}（{黑名单用户}）群'{群ID}'」\n到黑名单！\n"
                f"时长：{self._格式化时长显示(时长 or -1) if 结束时间 < 4102444800 else '永久'}\n"
                f"理由：{理由}")

    @staticmethod
    def _格式化时长显示(时长: int|float) -> str:
        """
        将分钟数转换为中文格式的时长字符串（年/月/天/小时/分钟），
        使用简化规则：1年 = 365天，1月 = 30天。
        """
        if 时长 <= 0:
            return "0分钟"
        分钟_PER_DAY = 24 * 60
        分钟_PER_MONTH = 30 * 分钟_PER_DAY
        分钟_PER_YEAR = 365 * 分钟_PER_DAY

        年 = 时长 // 分钟_PER_YEAR
        剩余分钟 = 时长 % 分钟_PER_YEAR
        月 = 剩余分钟 // 分钟_PER_MONTH
        剩余分钟 %= 分钟_PER_MONTH
        天 = 剩余分钟 // 分钟_PER_DAY
        剩余分钟 %= 分钟_PER_DAY
        小时 = 剩余分钟 // 60
        分钟 = 剩余分钟 % 60

        parts = []
        if 年 > 0:
            parts.append(f"{int(年)}年")
        if 月 > 0:
            parts.append(f"{int(月)}月")
        if 天 > 0:
            parts.append(f"{int(天)}天")
        if 小时 > 0:
            parts.append(f"{int(小时)}小时")
        if 分钟 > 0:
            parts.append(f"{int(分钟)}分钟")
        return "".join(parts) if parts else "0分钟"

    async def 移除黑名单(self, event: AiocqhttpMessageEvent = None, 黑名单用户: str = None, 群ID: str = None):
        名字 = '名字未知'
        if 黑名单用户 is None:
            if event is None:
                return False
            for seg in event.get_messages():
                if isinstance(seg, At):
                    if 黑名单用户 == str(seg.qq):
                        return False
                    黑名单用户 = str(seg.qq)
                    名字 = seg.name
                    break
                if isinstance(seg, Reply):
                    黑名单用户 = str(seg.qq)
                    名字 = seg.sender_nickname
                    break
            else:
                return False
        if 群ID is None:
            if event is None:
                群ID = '全局'
            else:
                群ID = event.get_group_id()
        async with self.黑名单锁:
            if 群ID in self.黑名单字典:
                if 黑名单用户 in self.黑名单字典[群ID]:
                    for i in self.黑名单列表:
                        分割 = i.split('|')
                        if 分割[4] == 群ID and 分割[0] == 黑名单用户 and 分割[2] != '名字未知':
                            名字 = 分割[2]
                            break
                    self.黑名单列表[:] = [
                        i for i in self.黑名单列表
                        if not (i.split('|')[4] == 群ID and i.split('|')[0] == 黑名单用户)
                    ]
                    del self.黑名单字典[群ID][黑名单用户]
                    self.config.save_config()
                    return f"✅ 移除\n「{名字}（{黑名单用户}）群'{群ID}'」\n黑名单用户成功！"
            return f"⚠️ 用户\n「{名字}（{黑名单用户}）群'{群ID}'」\n不在黑名单列表！"

    @filter.llm_tool(name="add_to_blacklist")
    async def 加入黑名单工具(self, event: AiocqhttpMessageEvent, 时长: int = None, 用户ID: int = None, 理由: str = '无'):
        """在聊天中遇到恶意用户（例如一直骂人，使坏等）你可以自行将其拉黑，拉黑后该用户艾特或回复你将全部忽略
        Args:
            时长(number):分钟，-1代表永久，拉黑时长应视情节轻重，不应轻易永久拉黑
            理由(string):拉黑理由（可选）
            用户ID(number):要拉黑的用户ID（可不填，默认为最后一个发送者）
        """
        if not event.get_group_id():
            return "此功能仅能在群聊中使用，当前不是群聊"

        if 用户ID is None:
            用户ID = event.get_sender_id()
            用户名字 = event.get_sender_name()
        else:
            try:
                用户ID = str(用户ID)
                时长 = int(时长)
            except:
                return "参数不正确，请传入用户ID和时长"

            用户名字 = await self.获取用户名(event, 用户ID, 私聊=(not event.get_group_id()))

        if 用户ID != event.get_sender_id():  # 如果拉黑用户和发送者不一致，说明是LLM拉黑他人
            if not event.is_admin():
                return '❌ 权限问题，拉黑对象不是发送者，且发送者不是管理员，你的权限可以自行拉黑发送者，和辅助管理员拉黑其他人'

        结果 = await self.加入黑名单(event, 用户ID, 用户名字, 时长, 理由, 'llm', event.get_group_id() or "私聊")
        return 结果

    @filter.llm_tool("list_blacklist")
    async def 查看黑名单工具(self, event: AiocqhttpMessageEvent):
        """查看已拉黑的用户"""
        结果 = await self.格式化黑名单列表(event)
        return 结果

    @filter.command("屏蔽此群", alias={'屏蔽该群', '屏蔽这个群'})
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def 屏蔽群(self, event: AiocqhttpMessageEvent, 群号:str = None):
        if 群号 is None:
            群号 = event.get_group_id()
            if not 群号:
                await self.发送回复文本(event, f"当前不在群聊，请传入群号或在群里使用")
                return
        群号 = str(群号)
        if 群号 in self.黑名单群聊:
            await self.发送回复文本(event, f"⚠️ 群 {群号} 已在黑名单/屏蔽列表中")
        else:
            self.黑名单群聊.append(群号)
            await self.发送回复文本(event, f"✅ 已屏蔽当前群 {群号}")
            self.config.save_config()
        event.stop_event()

    @filter.command("取消屏蔽", alias={'取消屏蔽此群', '取消屏蔽该群'})
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def 取消屏蔽(self, event: AiocqhttpMessageEvent, 群号:str = None):
        if 群号 is None:
            群号 = event.get_group_id()
            if not 群号:
                await self.发送回复文本(event, f"当前不在群聊，请传入群号或在群里使用")
                return
        群号 = str(群号)
        if 群号 in self.黑名单群聊:
            self.黑名单群聊[:] = [i for i in self.黑名单群聊 if i != 群号]  # 更安心可靠，防止用户在WebUI添加了多个一样的群
            await self.发送回复文本(event, f"✅ 已取消屏蔽当前群 {群号}")
            self.config.save_config()
        else:
            await self.发送回复文本(event, f"⚠️ 群 {群号} 不在黑名单/屏蔽列表中")
        event.stop_event()

    @filter.command("拉黑")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def 拉黑指令(self, _):
        """拉黑指令，示例：拉黑 用户ID 十分钟 名字…… 理由……
        或者艾特某个人或者回复之后，输入拉黑 时间 理由 此时不需要用户ID和名字，即可拉黑"""

    @filter.command("解除拉黑")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def 解除拉黑指令(self, _):
        """解除拉黑指令，示例：解除拉黑 用户ID……
        或者艾特某个人或者回复某个人之后，输入解除拉黑，即可解除拉黑"""

    @filter.command("群拉黑")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def 群拉黑指令(self, event: AiocqhttpMessageEvent):
        """指定群拉黑用户，格式：群拉黑 群号 用户ID 时长(分钟)，时长默认60分钟"""
        parts = event.get_message_str().split()
        if len(parts) < 4:
            await self.发送回复文本(event, "❌ 参数不足，格式：群拉黑 群号 用户ID 时长(分钟)")
            return
        群号 = parts[1]
        用户ID = parts[2]
        try:
            时长 = int(parts[3])
        except ValueError:
            await self.发送回复文本(event, "❌ 时长参数必须为整数")
            return
        结果 = await self.加入黑名单(
            event,
            黑名单用户=用户ID,
            时长=时长,
            理由='管理员远程拉黑',
            操作者ID=event.get_sender_id(),
            群ID=群号
        )
        if 结果:
            await self.发送回复文本(event, 结果)
        else:
            await self.发送回复文本(event, "❌ 拉黑失败，请检查参数")

    @filter.command("群解除拉黑")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def 群解除拉黑指令(self, event: AiocqhttpMessageEvent):
        """指定群解除拉黑用户，格式：群解除拉黑 群号 用户ID"""
        parts = event.get_message_str().split()
        if len(parts) < 3:
            await self.发送回复文本(event, "❌ 参数不足，格式：群解除拉黑 群号 用户ID")
            return
        群号 = parts[1]
        用户ID = parts[2]
        结果 = await self.移除黑名单(event, 黑名单用户=用户ID, 群ID=群号)
        if 结果:
            await self.发送回复文本(event, 结果)
        else:
            await self.发送回复文本(event, "❌ 解除拉黑失败，请检查用户是否在黑名单中")

    @filter.llm_tool("shut_up")
    async def 闭嘴工具(self, event: AiocqhttpMessageEvent, 时长: int=None):
        """当群友觉得你很吵或者管理员叫你闭嘴时，可以使用此工具让自己闭嘴一段时间，建议时长：40分钟，80分钟，120分钟
        Args:
            时长(int): 闭嘴时间，单位分钟，默认40分钟
        """
        群号 = event.get_group_id()
        if not 群号:
            群号 = "私聊"

        if 时长 is None:
            时长 = 40

        if 时长 <= 0:
            return "时长不能为负数"
        else:
            当前时间 = time.time()
            async with self.闭嘴锁:
                self.群闭嘴结束时间[群号] = 当前时间 + 时长 * 60
                self.闭嘴列表[:] = [i for i in self.闭嘴列表 if i.split(':')[0] != 群号]
                self.闭嘴列表.append(f"{群号}:{当前时间 + 时长 * 60}")
                self.config.save_config()
            时长文本 = self._格式化时长显示(时长)  # 复用已有的格式化函数
            return f"✅ 机器人将在本群闭嘴{时长文本}"

    @staticmethod
    def 是管理员(event) -> bool:
        """判断发送者是否为群管理员或群主"""
        try:
            return (event.message_obj.raw_message.sender['role'] in ('owner', 'admin')) or event.is_admin()
        except:
            return event.is_admin()

    @staticmethod
    async def 发送回复文本(event, 文本: str):
        await event.send(event.chain_result([Reply(id=event.message_obj.message_id), Plain(文本)]))

    @staticmethod
    async def 获取用户名(event: AiocqhttpMessageEvent, 用户ID:str, 群ID:str=None, 私聊:bool=False) -> str:
        try:
            if 私聊:
                return (await event.bot.get_stranger_info(
                    user_id=int(用户ID))
                        )['nickname']
            信息 = await event.bot.get_group_member_info(
                group_id=(int(群ID) if 群ID else int(event.get_group_id())),
                user_id=int(用户ID)
            )
            return 信息['card'] or 信息['nickname']
        except Exception as e:
            logger.error(f"获取名字失败：\n{e}", exc_info=True)
            return "名字未知"