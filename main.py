# -*- coding: utf-8 -*-
"""
Duel Galatea - 游戏王全能插件
富媒体消息版本
"""

import os
import json
import random
import re
import asyncio
from typing import Dict, Any, List
import aiohttp

from astrbot.api.star import Star, register, StarTools  # 引入 StarTools
from astrbot.api.event import filter
from astrbot.core.config.astrbot_config import AstrBotConfig
from astrbot.core.platform.astr_message_event import AstrMessageEvent
import astrbot.api.message_components as Comp
from astrbot.api.all import logger  # 引入 logger

from .ydk_manager import YDKManager

# 确保 generic_tier_manager.py 在同一目录下
from .generic_tier_manager import GameType, TierCommandHandler

#  deck_breakdown.py
from .deck_breakdown import DeckBreakdownManager
from .rotk_manager import RotKManager


class YugiohCardSearcher:
    # 将映射表提升为类常量，解决 PEP 8 问题
    ATTRIBUTE_MAP = {1: "地", 2: "水", 4: "炎", 8: "风", 16: "光", 32: "暗", 64: "神"}
    RACE_MAP = {
        1: "战士",
        2: "魔法师",
        4: "天使",
        8: "恶魔",
        16: "不死",
        32: "机械",
        64: "水",
        128: "炎",
        256: "岩石",
        512: "鸟兽",
        1024: "植物",
        2048: "昆虫",
        4096: "雷",
        8192: "龙",
        16384: "兽",
        32768: "兽战士",
        65536: "恐龙",
        131072: "鱼",
        262144: "海龙",
        524288: "爬虫类",
        1048576: "念动力",
        2097152: "幻神兽",
    }

    def __init__(self):
        self.base_url = "https://ygocdb.com/api/v0"
        # 优化资源管理：复用 Session
        self.session = aiohttp.ClientSession(headers={"User-Agent": "Mozilla/5.0"})

    async def close(self):
        """关闭 Session"""
        if self.session:
            await self.session.close()

    async def search_card(self, query: str) -> Dict[str, Any]:
        """异步搜索卡片"""
        try:
            url = f"{self.base_url}/?search={query}"
            async with self.session.get(url, timeout=10) as response:
                if response.status == 200:
                    # 修复：必须返回解析后的 JSON
                    return await response.json(content_type=None)
                else:
                    return {"error": f"API请求失败: {response.status}"}
        except Exception as e:
            return {"error": f"搜索出错: {str(e)}"}

    async def get_card_detail(self, card_id: str) -> Dict[str, Any]:
        """异步获取卡片详情"""
        try:
            url = f"{self.base_url}/card/{card_id}?show=all"
            async with self.session.get(url, timeout=10) as response:
                if response.status == 200:
                    # 修复：必须返回解析后的 JSON
                    return await response.json(content_type=None)
                else:
                    return {"error": f"获取详情失败: {response.status}"}
        except Exception as e:
            return {"error": f"获取详情出错: {str(e)}"}

    def format_card_info(self, card_data: Dict[str, Any]) -> str:
        """格式化卡片信息（重构版，拆分逻辑）"""
        if "error" in card_data:
            return card_data["error"]
        try:
            info = []
            # 1. 基础信息
            self._add_basic_info(card_data, info)

            # 2. 类型判断
            text_data = card_data.get("text", {})
            data = card_data.get("data", {})
            types_str = text_data.get("types", "")

            card_type_value = data.get("type", 0)
            is_monster = (card_type_value & 1) != 0

            if not is_monster:
                # 魔法/陷阱
                desc = text_data.get("desc", "")
                if desc:
                    info.append("🔹 卡片效果:\n{}".format(desc))
            else:
                # 怪兽
                self._add_monster_info(data, types_str, text_data, info)

            return "\n".join(info)
        except Exception as e:
            logger.error(f"格式化出错: {e}")
            return "格式化出错: {}".format(str(e))

    def _add_basic_info(self, card_data: Dict, info: List[str]):
        """辅助方法：添加基础信息"""
        cn_name = card_data.get("cn_name", "未知")
        sc_name = card_data.get("sc_name", "")
        name_display = (
            "{} ({})".format(cn_name, sc_name)
            if sc_name and sc_name != cn_name
            else cn_name
        )
        info.append("🃏 名称: {}".format(name_display))
        info.append("🆔 密码: {}".format(card_data.get("id", "未知")))

        types_str = card_data.get("text", {}).get("types", "")
        if types_str:
            info.append("🏷 卡片类型: {}".format(types_str))

    def _add_monster_info(
        self, data: Dict, types_str: str, text_data: Dict, info: List[str]
    ):
        """辅助方法：添加怪兽详细信息"""
        types_lower = types_str.lower()
        is_link = "连接" in types_lower
        is_xyz = "超量" in types_lower or "xyz" in types_lower
        is_pendulum = "灵摆" in types_lower

        atk = data.get("atk", "?")
        if is_link:
            info.append("攻守值: 攻击力{}/-".format(atk))
        else:
            def_val = data.get("def", "?")
            info.append("攻守值: 攻击力{}/守备力{}".format(atk, def_val))

        level_match = re.search(r"\[(?:★|☆|LINK-)(\d+)\]", types_str)
        if level_match:
            level_value = level_match.group(1)
            if is_link:
                info.append("Link值: {}".format(level_value))
            elif is_xyz:
                info.append("阶级: {}".format(level_value))
            else:
                info.append("等级: {}".format(level_value))

        attribute = data.get("attribute", 0)
        if attribute in self.ATTRIBUTE_MAP:
            info.append("属性: {}".format(self.ATTRIBUTE_MAP[attribute]))

        race = data.get("race", 0)
        if race in self.RACE_MAP:
            info.append("种族: {}".format(self.RACE_MAP[race]))

        if is_pendulum:
            self._add_pendulum_info(types_str, text_data, info)

        desc = text_data.get("desc", "")
        if desc:
            effect_title = "🔹 怪兽效果:" if is_pendulum else "🔹 卡片效果:"
            info.append("{}\n{}".format(effect_title, desc))

    def _add_pendulum_info(self, types_str: str, text_data: Dict, info: List[str]):
        """辅助方法：添加灵摆信息"""
        scale_matches = re.findall(r"(\d+)/(\d+)", types_str)
        if scale_matches and len(scale_matches) >= 1:
            left_scale, right_scale = scale_matches[-1]
            info.append("🔹 灵摆刻度: {}/{}".format(left_scale, right_scale))
        pdesc = text_data.get("pdesc", "")
        if pdesc:
            info.append("🔸 灵摆效果:\n{}".format(pdesc))

    def format_search_results(
        self, results: List[Dict], page: int, user_id: str
    ) -> str:
        page_size = 10
        start_idx = (page - 1) * page_size
        end_idx = min(start_idx + page_size, len(results))
        current_results = results[start_idx:end_idx]
        total_results = len(results)
        total_pages = (total_results + page_size - 1) // page_size
        output = [
            "🔍 搜索结果 (第 {}/{} 页，共 {} 个结果):\n".format(
                page, total_pages, total_results
            )
        ]
        for i, card in enumerate(current_results, start=start_idx + 1):
            name = card.get("cn_name", "未知")
            card_type = card.get("type", "")
            type_map = {"monster": "[怪兽]", "spell": "[魔法]", "trap": "[陷阱]"}
            type_tag = type_map.get(card_type, "")
            output.append("{}. {} {}".format(i, name, type_tag))
        output.append(
            "\n💡 请输入 /查卡序号 [序号] 查看详细信息，或使用 /查卡换页 [页码] 切换页面"
        )
        return "\n".join(output)


@register("duel_galatea", "Noctfom & prts", "游戏王全能插件", "1.2.0")
class DuelGalateaPlugin(Star):
    def __init__(self, context=None, config: AstrBotConfig = None):
        super().__init__(context, config)
        self.card_searcher = YugiohCardSearcher()
        self.search_sessions = {}
        self.last_viewed_cards = {}
        self.all_card_ids = []  # 全卡片ID池

        # === 修复数据持久化违规 ===
        # 1. 源码目录：仅用于读取随插件附带的静态文件 (如 card_ids.json)
        self.plugin_source_dir = os.path.dirname(os.path.abspath(__file__))

        # 2. 数据目录：使用 StarTools 获取标准数据目录，用于存储缓存、图片等
        # 这会在 data/plugins/duel_galatea/ 下创建目录
        # 2. 数据目录：尝试使用 StarTools 获取，如果失败则手动指定
        try:
            self.data_dir = StarTools.get_data_dir()
        except Exception as e:
            logger.warning(
                f"StarTools.get_data_dir() 自动获取失败 ({e})，使用手动路径兜底。"
            )
            # 手动构建路径：data/plugins/duel_galatea
            # 注意：这里使用你注册插件时的名字 "duel_galatea"
            self.data_dir = os.path.join(os.getcwd(), "data", "plugins", "duel_galatea")

        # 确保目录存在
        if not os.path.exists(self.data_dir):
            os.makedirs(self.data_dir)

        logger.info(f"DuelGalatea 数据目录: {self.data_dir}")

        # 初始化各个 Manager，传入数据目录以便它们在正确的地方写文件
        # 注意：这里假设您的 Manager 构造函数已经更新为接收 data_dir
        self.tier_handler = TierCommandHandler(str(self.data_dir))
        self.rotk_manager = RotKManager(str(self.data_dir))
        # 实例化 YDKManager
        self.ydk_manager = YDKManager(str(self.data_dir), self.plugin_source_dir)

        # 实例化 DeckBreakdownManager (传入 ydk_manager)
        self.deck_breakdown = DeckBreakdownManager(
            str(self.data_dir), self.plugin_source_dir, self.ydk_manager
        )
        # 加载ID (从源码目录读取)
        self._load_card_ids()

    def terminate(self):
        """插件卸载/关闭时的清理工作"""
        # 关闭 aiohttp session
        asyncio.create_task(self.card_searcher.close())

    def _load_card_ids(self):
        """加载纯ID列表到内存"""
        try:
            # 静态资源从源码目录读取
            ids_file_path = os.path.join(self.plugin_source_dir, "card_ids.json")

            if os.path.exists(ids_file_path):
                with open(ids_file_path, "r", encoding="utf-8") as f:
                    # 确保 ID 是字符串
                    self.all_card_ids = [str(x) for x in json.load(f)]
                # === 修复日志违规 ===
                logger.info(
                    f"DuelGalatea: 成功加载 {len(self.all_card_ids)} 个卡片ID到随机池"
                )
            else:
                logger.warning("DuelGalatea: 未找到card_ids.json文件，使用备用列表")
                self._load_backup_ids()

        except Exception as e:
            logger.error(f"DuelGalatea: 加载卡片ID失败: {e}")
            self._load_backup_ids()

    def _load_backup_ids(self):
        """备用ID列表"""
        backup_ids = [
            "16178681",
            "89631139",
            "4064256",
            "74677422",
            "38033121",
            "10000000",
            "53129443",
            "83104731",
            "94192409",
            "53334471",
            "46986414",
            "70828912",
            "36935103",
            "7902349",
            "65741786",
        ]
        self.all_card_ids = backup_ids
        logger.info(f"DuelGalatea: 使用备用ID列表，共 {len(backup_ids)} 个ID")

    def _resolve_deck_name(self, input_name: str) -> str:
        """利用 TierHandler 中的最新数据进行 中->英 转换"""
        # 1. 如果本身就是英文 Key (在翻译字典的键里)，直接返回
        # (不区分大小写比较)
        for en in self.tier_handler.manager.translations.keys():
            if en.lower() == input_name.lower():
                return en

        # 2. 尝试反向查找 (中文 -> 英文)
        # self.tier_handler.manager.translations 结构是 { "Sky Striker": "闪刀姬" }
        for en, cn in self.tier_handler.manager.translations.items():
            # 精确匹配
            if cn == input_name:
                return en
            # 模糊匹配 (可选，比如输入"闪刀"也能查到"闪刀姬")
            if input_name in cn:
                return en

        # 3. 没找到，原样返回，交给 deck_breakdown 自己去处理
        return input_name

    def _get_session_id(self, event: AstrMessageEvent) -> str:
        """
        获取会话ID (文件隔离核心逻辑)
        优先级: 群聊ID > 私聊用户ID > 默认值
        """
        obj = event.message_obj

        # 1. 尝试获取群号 (Group ID)
        # 不同的平台可能用不同的字段，这里做个兼容判断
        if hasattr(obj, "group_id") and obj.group_id:
            return f"group_{obj.group_id}"

        # 2. 如果没有群号，说明是私聊，尝试获取发送者 ID (Sender ID)
        # 写法 A: 直接在 message_obj 上
        if hasattr(obj, "sender_id") and obj.sender_id:
            return f"user_{obj.sender_id}"

        # 写法 B: 在 sender 对象里 (OneBot 标准常见结构)
        if hasattr(obj, "sender") and isinstance(obj.sender, dict):
            user_id = obj.sender.get("user_id")
            if user_id:
                return f"user_{user_id}"
        elif hasattr(obj, "sender") and hasattr(obj.sender, "user_id"):
            if obj.sender.user_id:
                return f"user_{obj.sender.user_id}"

        # 3. 实在获取不到，记录日志并返回 default
        # 这种情况很少见，除非是完全不支持 ID 的平台
        from astrbot.api.all import logger

        logger.warning(f"DuelGalatea: 无法识别会话 ID，使用 default。Obj: {obj}")
        return "default"

    # ... (Command handlers start here) ...

    @filter.command("查卡", alias={"/查卡"})
    async def handle_cha_ka(self, event: AstrMessageEvent):
        """查卡 卡名"""
        message_text = event.get_message_str().strip()
        user_id = getattr(event.message_obj, "sender_id", "unknown")
        parts = message_text.split() if message_text else []

        if len(parts) <= 1:
            await event.send(
                event.plain_result("请输入要查询的卡片名称，例如: /查卡 青眼白龙")
            )
            return

        query = " ".join(parts[1:])
        result = await self.card_searcher.search_card(query)

        # 修复逻辑：检查 result 是否有效
        if "error" in result:
            await event.send(event.plain_result(f"❌ 搜索出错: {result['error']}"))
        elif "result" in result and result["result"]:
            self.search_sessions[user_id] = {
                "results": result["result"],
                "current_page": 1,
                "page_size": 10,
                "query": query,
            }
            response_text = self.card_searcher.format_search_results(
                result["result"], 1, user_id
            )
            await event.send(event.plain_result(response_text))
        else:
            await event.send(
                event.plain_result("⚠️ 未找到与'{}'相关的卡片".format(query))
            )

    # ... (其他不需要大幅修改的指令，只需注意 print 替换为 logger) ...

    @filter.command("查卡换页", alias={"/查卡换页"})
    async def handle_change_page(self, event: AstrMessageEvent):
        """切换到对应查卡页码"""
        # ... (逻辑同前，略微精简展示) ...
        message_text = event.get_message_str().strip()
        user_id = getattr(event.message_obj, "sender_id", "unknown")
        parts = message_text.split()

        page_str = parts[1] if len(parts) > 1 else ""
        if not page_str.isdigit():
            await event.send(event.plain_result("请输入有效的页码"))
            return

        page = int(page_str)
        if user_id not in self.search_sessions:
            await event.send(event.plain_result("没有正在进行的搜索会话"))
            return

        session = self.search_sessions[user_id]
        results = session["results"]
        # ... 这里的逻辑基本没变 ...
        response_text = self.card_searcher.format_search_results(results, page, user_id)
        await event.send(event.plain_result(response_text))

    @filter.command("查卡序号", alias={"/查卡序号"})
    async def handle_select_card(self, event: AstrMessageEvent):
        """查询对应序号卡片"""
        message_text = event.get_message_str().strip()
        user_id = getattr(event.message_obj, "sender_id", "unknown")
        parts = message_text.split()

        card_number_str = parts[1] if len(parts) > 1 else ""
        if not card_number_str.isdigit():
            await event.send(event.plain_result("请输入卡片序号"))
            return

        card_number = int(card_number_str)
        if user_id not in self.search_sessions:
            await event.send(event.plain_result("请先搜索卡片"))
            return

        session = self.search_sessions[user_id]
        results = session["results"]

        if 1 <= card_number <= len(results):
            selected_card = results[card_number - 1]
            card_id = selected_card["id"]

            detail_result = await self.card_searcher.get_card_detail(str(card_id))

            # 增加错误检查
            if "error" in detail_result:
                await event.send(
                    event.plain_result(f"获取详情失败: {detail_result['error']}")
                )
                return

            self.last_viewed_cards[user_id] = {
                "card_id": str(card_id),
                "card_name": detail_result.get("cn_name", "未知"),
                "card_data": detail_result,
            }
            formatted_detail = self.card_searcher.format_card_info(detail_result)

            thumbnail_url = (
                f"https://cdn.233.momobako.com/ygopro/pics/{card_id}.jpg!half"
            )
            message_chain = [
                Comp.Image.fromURL(thumbnail_url),
                Comp.Plain("\n" + formatted_detail),
            ]
            await event.send(event.chain_result(message_chain))
        else:
            await event.send(event.plain_result("序号超出范围"))

    @filter.command("发送高清卡图", alias={"/发送高清卡图"})
    async def handle_send_image(self, event: AstrMessageEvent):
        """发送上一次查询的卡片大图，或直接输入卡密查询"""
        # ... (逻辑保持不变) ...
        user_id = getattr(event.message_obj, "sender_id", "unknown")
        parts = event.get_message_str().strip().split()
        card_id_str = parts[1] if len(parts) > 1 else ""

        if card_id_str:
            if not card_id_str.isdigit():
                await event.send(event.plain_result("卡片密码必须是数字"))
                return
            card_id = card_id_str
        elif user_id in self.last_viewed_cards:
            card_id = self.last_viewed_cards[user_id]["card_id"]
        else:
            await event.send(event.plain_result("请先查看卡片详情"))
            return

        image_url = "https://cdn.233.momobako.com/ygopro/pics/{}.jpg".format(card_id)
        try:
            await event.send(event.image_result(image_url))
        except:
            await event.send(event.plain_result(image_url))

    @filter.command("随机一卡", alias={"/随机一卡"})
    async def handle_random_card(self, event: AstrMessageEvent):
        """多罗！！！"""
        if not self.all_card_ids:
            await event.send(event.plain_result("卡片数据库未加载"))
            return

        # 重试逻辑
        for _ in range(3):
            try:
                random_card_id = random.choice(self.all_card_ids)
                detail_result = await self.card_searcher.get_card_detail(
                    str(random_card_id)
                )

                if "error" not in detail_result and "data" in detail_result:
                    formatted_detail = self.card_searcher.format_card_info(
                        detail_result
                    )
                    thumbnail_url = f"https://cdn.233.momobako.com/ygopro/pics/{random_card_id}.jpg!half"

                    message_chain = [
                        Comp.Image.fromURL(thumbnail_url),
                        Comp.Plain("\n" + formatted_detail),
                    ]
                    await event.send(event.chain_result(message_chain))

                    user_id = getattr(event.message_obj, "sender_id", "unknown")
                    self.last_viewed_cards[user_id] = {
                        "card_id": str(random_card_id),
                        "card_name": detail_result.get("cn_name", "未知"),
                        "card_data": detail_result,
                    }
                    return  # 成功则退出
            except Exception as e:
                logger.error(f"随机抽取异常: {e}")
                continue

        await event.send(event.plain_result("抽取失败，请稍后再试"))

    @filter.command("发动王牌圣杯", alias={"/发动王牌圣杯"})
    async def handle_holy_grail(self, event: AstrMessageEvent):
        """扔硬币！！"""
        is_positive = random.choice([True, False])
        if is_positive:
            card_id = "55144522"
            message_text = "是正面！抽2张卡！"
        else:
            card_id = "5915629"
            message_text = "是反面......对方抽2张卡。"

        thumbnail_url = f"https://cdn.233.momobako.com/ygopro/pics/{card_id}.jpg!half"
        message_chain = [
            Comp.Image.fromURL(thumbnail_url),
            Comp.Plain("\n" + message_text),
        ]
        await event.send(event.chain_result(message_chain))

    # ================= T表/卡组/OCG 相关指令 =================
    # 注意：这些函数里的 print 也需要改成 logger

    @filter.command("DL更新T表", alias=["/DL更新T表"])
    async def handle_dl_update_tier(self, event: AstrMessageEvent):
        """更新本地的DLT表数据"""
        await self.tier_handler.update_tier_list(
            event, GameType.DUEL_LINKS, "Duel Links"
        )

    @filter.command("DL查询T表", alias=["/DL查询T表"])
    async def handle_dl_query_tier(self, event: AstrMessageEvent):
        """查询本地的DLT表数据"""
        await self.tier_handler.query_tier_list(
            event, GameType.DUEL_LINKS, "Duel Links"
        )

    @filter.command("MD更新T表", alias=["/MD更新T表"])
    async def handle_md_update_tier(self, event: AstrMessageEvent):
        """更新本地的MDT表数据"""
        await self.tier_handler.update_tier_list(
            event, GameType.MASTER_DUEL, "Master Duel"
        )

    @filter.command("MD查询T表", alias=["/MD查询T表"])
    async def handle_md_query_tier(self, event: AstrMessageEvent):
        """查询本地的MDT表数据"""
        await self.tier_handler.query_tier_list(
            event, GameType.MASTER_DUEL, "Master Duel"
        )

    @filter.command("翻译T表", alias=["/翻译T表"])
    async def handle_translate_tier(self, event: AstrMessageEvent):
        """尝试翻译当前T表中未翻译的卡组"""
        message_text = event.get_message_str().strip()
        parts = message_text.split()

        game_type = GameType.DUEL_LINKS
        if len(parts) < 2:
            await event.send(
                event.plain_result(
                    "请输入你要翻译的T表种类！如/翻译T表 DL 或 /翻译T表 MD"
                )
            )
        elif "dl" in parts[1].lower() or "DL" in parts[1].lower():
            await self.tier_handler.translate_tier_list(event, game_type)
        elif "md" in parts[1].lower() or "MD" in parts[1].lower():
            game_type = GameType.MASTER_DUEL
            await self.tier_handler.translate_tier_list(event, game_type)
        else:
            await event.send(event.plain_result("输入错误!"))

    @filter.command("MD查卡组", alias=["/MD查卡组", "/MD查询卡组", "MD查询卡组"])
    async def handle_md_deck_breakdown(self, event: AstrMessageEvent):
        """查询MD卡组配置与图片"""
        message_text = event.get_message_str().strip()
        parts = message_text.split(maxsplit=1)

        if len(parts) < 2:
            await event.send(
                event.plain_result(
                    "请输入卡组名称，例如: /MD查卡组 Maliss\n(可以使用 /MD查询T表 查看推荐卡组名)"
                )
            )
            return

        raw_name = parts[1]

        # === 修改开始: 先尝试进行翻译转换 ===
        deck_name = self._resolve_deck_name(raw_name)

        # 如果名字发生了变化(找到了翻译)，提示一下用户
        if deck_name != raw_name:
            await event.send(
                event.plain_result(
                    f"🔍 识别到中文卡组名【{raw_name}】，自动转换为【{deck_name}】进行查询..."
                )
            )
        else:
            await event.send(
                event.plain_result(
                    f"🔍 [MDM] 正在抓取【{deck_name}】数据并生成构筑图，请稍候..."
                )
            )
        # === 修改结束 ===

        session_id = self._get_session_id(event)  # <--- 获取 ID

        try:
            # 传入 session_id
            result = await self.deck_breakdown.fetch_deck_breakdown(
                deck_name, GameType.MASTER_DUEL, session_id
            )

            text_msg = result.get("text", "无数据")
            image_path = result.get("image_path")

            chain = []
            # 有图先发图
            if image_path and os.path.exists(image_path):
                chain.append(Comp.Image.fromFileSystem(image_path))

            # 再发文字
            chain.append(Comp.Plain(text_msg))

            await event.send(event.chain_result(chain))

        except Exception as e:
            # 建议这里把 e 打印出来，方便调试
            logger.error(f"查询出错: {e}")
            await event.send(event.plain_result("查询过程中发生内部错误。"))

    @filter.command("DL查卡组", alias=["/DL查卡组", "/DL查询卡组", "DL查询卡组"])
    async def handle_dl_deck_breakdown(self, event: AstrMessageEvent):
        """查询DL卡组配置与图片"""
        message_text = event.get_message_str().strip()
        parts = message_text.split(maxsplit=1)

        if len(parts) < 2:
            await event.send(
                event.plain_result("请输入卡组名称，例如: /DL查卡组 Blue-Eyes")
            )
            return

        raw_name = parts[1]

        # === 修改开始: 先尝试进行翻译转换 ===
        deck_name = self._resolve_deck_name(raw_name)

        if deck_name != raw_name:
            await event.send(
                event.plain_result(
                    f"🔍 识别到中文卡组名【{raw_name}】，自动转换为【{deck_name}】进行查询..."
                )
            )
        else:
            await event.send(
                event.plain_result(
                    f"🔍 [DLM] 正在抓取【{deck_name}】数据并生成构筑图，请稍候..."
                )
            )
        # === 修改结束 ===

        session_id = self._get_session_id(event)  # <--- 获取 ID

        try:
            # 传入 session_id
            result = await self.deck_breakdown.fetch_deck_breakdown(
                deck_name, GameType.MASTER_DUEL, session_id
            )
            text_msg = result.get("text", "无数据")
            image_path = result.get("image_path")

            chain = []
            if image_path and os.path.exists(image_path):
                chain.append(Comp.Image.fromFileSystem(image_path))

            chain.append(Comp.Plain(text_msg))

            await event.send(event.chain_result(chain))

        except Exception as e:
            logger.error(f"查询出错: {e}")
            await event.send(event.plain_result("查询过程中发生内部错误。"))

    @filter.command("OCG饼图更新", alias=["/OCG饼图更新"])
    async def handle_ocg_update(self, event: AstrMessageEvent):
        """爬取ROTK获取最新饼图"""
        await event.send(event.plain_result("🔍 正在连接 RotK 抓取数据..."))
        try:
            result = await self.rotk_manager.fetch_latest_report()
            if result is None or "error" in result:
                err = result.get("error", "Unknown") if result else "Empty"
                await event.send(event.plain_result(f"⚠️ 更新失败: {err}"))
                return

            if self.rotk_manager.save_local_data(result):
                msg = f"✅ 更新完毕! 标题: {result['title']}"
                await event.send(event.plain_result(msg))
            else:
                await event.send(event.plain_result("⚠️ 保存失败"))
        except Exception as e:
            logger.error(f"OCG更新出错: {e}")
            await event.send(event.plain_result(f"⚠️ 内部错误: {e}"))

    @filter.command("OCG饼图", alias=["/OCG饼图", "/OCG饼图查询", "OCG饼图查询"])
    async def handle_ocg_query(self, event: AstrMessageEvent):
        """发送本地的OCG饼图数据"""
        data = self.rotk_manager.load_local_data()
        if not data:
            await event.send(event.plain_result("⚠️ 本地无数据，请先 /OCG饼图更新"))
            return

        chain = []
        local_paths = data.get("local_paths", [])
        for path in local_paths[:9]:
            if os.path.exists(path):
                chain.append(Comp.Image.fromFileSystem(path))

        text = f"📊 {data['title']}\n📅 {data['date']}"
        chain.append(Comp.Plain(text))
        await event.send(event.chain_result(chain))

    @filter.command("查询卡组翻译", alias=["/查询卡组翻译"])
    async def handle_query_translation(self, event: AstrMessageEvent):
        """查询已有的卡组翻译(英文)"""
        parts = event.get_message_str().strip().split(maxsplit=1)
        if len(parts) < 2:
            await event.send(event.plain_result("请输入名称"))
            return
        query = parts[1]
        en, cn = self.tier_handler.manager.get_specific_translation(query)
        if en:
            await event.send(event.plain_result(f"🇺🇸 {en}\n🇨🇳 {cn}"))
        else:
            await event.send(event.plain_result("未找到记录"))

    @filter.command("修改卡组翻译", alias=["/修改卡组翻译"])
    async def handle_edit_translation(self, event: AstrMessageEvent):
        """手动添加/修改卡组翻译"""
        parts = event.get_message_str().strip().split()
        if len(parts) < 3:
            await event.send(event.plain_result("用法: /修改卡组翻译 [英文] [中文]"))
            return
        cn_name = parts[-1]
        en_name = " ".join(parts[1:-1])
        if self.tier_handler.manager.set_manual_translation(en_name, cn_name):
            await event.send(event.plain_result(f"✅ 已更新: {en_name} -> {cn_name}"))
        else:
            await event.send(event.plain_result("保存失败"))

    @filter.command("发送ydk", alias=["/发送ydk", "发送ydk文件", "/发送ydk文件"])
    async def handle_send_ydk(self, event: AstrMessageEvent):
        """发送用户缓存的ydk文件"""
        session_id = self._get_session_id(event)
        # 动态拼接路径
        path = os.path.join(self.ydk_manager.cache_dir, f"deck_{session_id}.ydk")
        path = os.path.abspath(path)

        if os.path.exists(path):
            file_name = f"{session_id}.ydk"  # 或者保留 deck_xxx.ydk
            await event.send(
                event.chain_result([Comp.File(name=os.path.basename(path), file=path)])
            )
        else:
            await event.send(event.plain_result("⚠️ 当前会话没有缓存的卡组文件。"))

    @filter.command("发送卡组图片", alias=["/发送卡组图片"])
    async def handle_send_deck_image(self, event: AstrMessageEvent):
        """发送用户缓存的ydk文件的卡组构筑图片"""
        session_id = self._get_session_id(event)
        # 检查文件是否存在
        ydk_path = os.path.join(self.ydk_manager.cache_dir, f"deck_{session_id}.ydk")

        if not os.path.exists(ydk_path):
            await event.send(event.plain_result("⚠️ 当前会话无缓存数据"))
            return

        await event.send(event.plain_result("🎨 正在生成图片..."))
        # 传入 session_id
        img_path = await self.ydk_manager.draw_deck_image(session_id, "Cached Deck")

        if img_path:
            await event.send(event.image_result(img_path))

    @filter.command("接收ydk文本", alias=["/接收ydk文本"])
    async def handle_receive_ydk(self, event: AstrMessageEvent):
        session_id = self._get_session_id(event)
        """接收 YDK 文本并更新缓存"""
        text = event.get_message_str().strip()
        # 去掉指令部分
        parts = text.split("\n", 1)
        if len(parts) < 2:
            await event.send(event.plain_result("请在指令换行后粘贴 YDK 内容"))
            return

        ydk_content = parts[1]
        main, extra, side = self.ydk_manager.parse_ydk(ydk_content)

        if not main and not extra:
            await event.send(event.plain_result("⚠️ 未识别到有效的卡密内容"))
            return

        path = self.ydk_manager.save_ydk(main, extra, side, session_id)
        await event.send(
            event.plain_result(
                f" YDK 已接收 (M:{len(main)} E:{len(extra)} S:{len(side)})。你可以使用 /发送卡组图片 查看。"
            )
        )

    @filter.command(
        "接收卡组链接", alias=["/接收卡组链接", "解析卡组链接", "/解析卡组链接"]
    )
    async def handle_receive_deck_link(self, event: AstrMessageEvent):
        session_id = self._get_session_id(event)
        """解析 ourocg/ygo 卡组链接 或 YDKe 代码并转化为ydk文件缓存"""
        text = event.get_message_str().strip()
        parts = text.split()
        url = parts[1] if len(parts) > 1 else text  # 兼容两种输入方式

        main, extra, side = [], [], []
        source_type = ""

        await event.send(event.plain_result("🔍 正在解析链接..."))

        # === 分流逻辑 ===
        if url.startswith("ydke://"):
            # 处理 YDKe
            source_type = "YDKe"
            main, extra, side = self.ydk_manager.parse_ydke_url(url)
        elif "deck.ourygo.top" in url and "d=" in url:
            # 处理 Ourocg
            source_type = "Ourocg"
            try:
                main, extra, side = self.ydk_manager.parse_ourocg_url(url)
            except Exception as e:
                await event.send(event.plain_result(f"❌ 解析出错: {e}"))
                return
        else:
            await event.send(
                event.plain_result(
                    "⚠️ 未知链接格式。支持：\n1. deck.ourygo.top 分享链接\n2. ydke:// 代码"
                )
            )
            return

        # === 结果处理 ===
        if not main and not extra:
            await event.send(event.plain_result("❌ 解析结果为空，请检查链接是否有效"))
            return

        # 2. 保存 YDK
        ydk_path = self.ydk_manager.save_ydk(main, extra, side, session_id)

        # 3. 生成图片
        await event.send(
            event.plain_result(
                f"✅ [{source_type}] 解析成功 (M:{len(main)} E:{len(extra)} S:{len(side)})\n🎨 正在绘图..."
            )
        )
        img_path = await self.ydk_manager.draw_deck_image(
            session_id, f"Shared {source_type}"
        )

        if img_path:
            await event.send(event.image_result(img_path))
        else:
            await event.send(
                event.plain_result(
                    "⚠️ 图片生成失败，但 YDK 已保存。可以使用 /发送ydk 获取。"
                )
            )
