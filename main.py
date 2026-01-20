# -*- coding: utf-8 -*-
"""
Duel Galatea - 游戏王全能插件
"""

import os
import certifi
# === 全局 SSL 补丁  ===
os.environ['SSL_CERT_FILE'] = certifi.where()

import json
import random
import re
import asyncio
from typing import Dict, Any, List
import aiohttp
import html


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

from .duel_simulator import DuelSimulator #引入 DuelSimulator

from .banlist_manager import BanlistManager #引入 BanlistManager


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
    
    # === 新增：HTML 获取与解析方法 ===

    async def get_card_html(self, card_id: str) -> str:
        """获取百鸽详情页的 HTML 源码"""
        url = f"https://ygocdb.com/card/{card_id}"
        try:
            async with self.session.get(url, timeout=10) as response:
                if response.status == 200:
                    return await response.text()
        except Exception as e:
            logger.error(f"HTML fetch error: {e}")
        return ""

    def parse_card_packs(self, html_content: str) -> List[str]:
        """解析卡盒信息 (Date - Code - Name)"""
        # 1. 找到包含 packs 的区域
        pack_list = []
        # 正则匹配 <li class="pack">...</li>
        # 结构: <span>日期</span><span>编号</span><a ...>包名</a>
        pattern = re.compile(
            r'<li class="pack">\s*<span>(.*?)</span><span>(.*?)</span>\s*<a[^>]*>(.*?)</a>',
            re.DOTALL
        )
        
        matches = pattern.findall(html_content)
        for date, code, name in matches:
            # 清理 HTML 转义字符 (如 &#39;)
            clean_name = html.unescape(name.strip())
            pack_list.append(f"[{date}] {code} - {clean_name}")
            
        return pack_list

    def parse_card_faq(self, html_content: str) -> List[Dict[str, str]]:
        """解析 FAQ/裁定 (Q&A Box)"""
        qa_list = []
        
        # 1. 提取所有 qabox
        # <div class="qabox ..."> ... </div>
        box_pattern = re.compile(r'<div class="qabox.*?>(.*?)<div class="info">', re.DOTALL)
        boxes = box_pattern.findall(html_content)
        
        for box in boxes:
            # 提取 Title, Question, Answer
            title_m = re.search(r'<div class="qa title"[^>]*>(.*?)</div>', box, re.DOTALL)
            q_m = re.search(r'<div class="qa question"[^>]*>(.*?)</div>', box, re.DOTALL)
            a_m = re.search(r'<div class="qa answer"[^>]*>(.*?)</div>', box, re.DOTALL)
            
            if q_m and a_m:
                t_str = self._clean_html(title_m.group(1)) if title_m else "Q&A"
                q_str = self._clean_html(q_m.group(1))
                a_str = self._clean_html(a_m.group(1))
                
                qa_list.append({
                    "title": t_str,
                    "q": q_str,
                    "a": a_str
                })
                
        return qa_list

    def _clean_html(self, raw_html: str) -> str:
        """清理 HTML 标签，转义字符，处理换行"""
        if not raw_html: return ""
        # 1. 处理换行: <br> -> \n
        text = re.sub(r'<br\s*/?>', '\n', raw_html, flags=re.IGNORECASE)
        # 2. 去除所有标签: <...>
        text = re.sub(r'<[^>]+>', '', text)
        # 3. 反转义: &lt; -> <
        text = html.unescape(text)
        return text.strip()


@register("duel_galatea", "Noctfom", "游戏王全能插件", "1.4.0")
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
        # 新增：决斗模拟器
        self.duel_sim = DuelSimulator()
        # 新增：禁限表管理器
        self.banlist_manager = BanlistManager(str(self.data_dir))
        # 加载ID (从源码目录读取)
        self._load_card_ids()

    async def terminate(self): # <--- 必须加 async
        """插件卸载/关闭时的清理工作"""
        # 关闭 aiohttp session
        if self.card_searcher:
            await self.card_searcher.close() # <--- 直接 await，确保资源释放

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
    
    async def _send_card_detail(self, event: AstrMessageEvent, card_id: str, card_name_fallback: str = "未知"):
        """获取详情、更新缓存、拼接G点信息并发送"""
        user_id = getattr(event.message_obj, "sender_id", "unknown") # 获取用户ID用于缓存
        
        # 1. 获取详情
        detail = await self.card_searcher.get_card_detail(str(card_id))
        if "error" in detail:
            await event.send(event.plain_result(f"获取详情失败: {detail['error']}"))
            return

        # 2. === 关键：更新最后查看的卡片缓存 ===
        # 这样 /发送高清卡图、/查裁定 都能用了
        self.last_viewed_cards[user_id] = {
            "card_id": str(card_id),
            "card_name": detail.get("cn_name", card_name_fallback),
            "card_data": detail,
        }

        # 3. 格式化基础文本
        formatted_detail = self.card_searcher.format_card_info(detail)

        # 4. 拼接禁卡/Genesys信息
        status_info = self.banlist_manager.get_card_status(str(card_id))
        tags = []
        if status_info["sc"] != "无限制": tags.append(f"🇨🇳简中:{status_info['sc']}")
        if status_info["ocg"] != "无限制": tags.append(f"🇯🇵OCG:{status_info['ocg']}")
        if status_info["genesys"] > 0: tags.append(f"🧬Genesys:{status_info['genesys']}pt")
            
        if tags:
            formatted_detail += "\n" + " | ".join(tags)

        # 5. 下载图片并发送
        chain = []
        local_img = await self.ydk_manager._download_image(self.card_searcher.session, str(card_id))
        if local_img:
            temp_path = os.path.join(self.ydk_manager.images_dir, f"temp_{card_id}.jpg")
            local_img.save(temp_path)
            chain.append(Comp.Image.fromFileSystem(temp_path))
        
        chain.append(Comp.Plain(formatted_detail))
        await event.send(event.chain_result(chain))

    # ... (Command handlers start here) ...

    @filter.command("查卡", alias={"/查卡"})
    async def handle_cha_ka(self, event: AstrMessageEvent):
        message_text = event.get_message_str().strip()
        user_id = getattr(event.message_obj, "sender_id", "unknown")
        parts = message_text.split() if message_text else []

        if len(parts) <= 1:
            await event.send(event.plain_result("请输入要查询的卡片名称，例如: /查卡 青眼白龙"))
            return

        query = " ".join(parts[1:])
        result = await self.card_searcher.search_card(query)

        if "error" in result:
            await event.send(event.plain_result(f"❌ 搜索出错: {result['error']}"))
        elif "result" in result and result["result"]:
            results = result["result"]
            
            # === 修改点：单结果直接显示 ===
            if len(results) == 1:
                # 只有一张卡，直接发送详情并缓存
                card = results[0]
                await self._send_card_detail(event, card["id"], card.get("cn_name", query))
                return
            # ==========================

            self.search_sessions[user_id] = {
                "results": results,
                "current_page": 1,
                "page_size": 10,
                "query": query,
            }
            response_text = self.card_searcher.format_search_results(results, 1, user_id)
            await event.send(event.plain_result(response_text))
        else:
            await event.send(event.plain_result("⚠️ 未找到与'{}'相关的卡片".format(query)))

    @filter.command("查卡换页", alias={"/查卡换页"})
    async def handle_change_page(self, event: AstrMessageEvent):
        """切换到对应查卡页码"""
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
            
            # 这里会自动处理详情查询、G点显示、图片下载和缓存更新
            await self._send_card_detail(event, selected_card["id"], selected_card.get("cn_name"))
            
        else:
            await event.send(event.plain_result("序号超出范围"))

    @filter.command("发送高清卡图", alias={"/发送高清卡图"})
    async def handle_send_image(self, event: AstrMessageEvent):
        """发送上一次查询的卡片大图，或直接输入卡密查询"""
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
    
    # ================= 网页解析扩展功能 =================

    @filter.command("查询卡盒", alias=["/查询卡盒", "查卡盒", "/查卡盒"])
    async def handle_query_packs(self, event: AstrMessageEvent):
        """查询当前卡片的收录卡盒信息"""
        user_id = getattr(event.message_obj, "sender_id", "unknown")
        
        # 1. 检查是否有缓存的卡片
        if user_id not in self.last_viewed_cards:
            await event.send(event.plain_result("⚠️ 请先使用 /查卡 或 /查卡序号 查看一张卡片。"))
            return
            
        card_info = self.last_viewed_cards[user_id]
        card_id = card_info["card_id"]
        card_name = card_info["card_name"]
        
        await event.send(event.plain_result(f"🔍 正在查询【{card_name}】的收录信息..."))
        
        # 2. 获取 HTML 并解析
        html_text = await self.card_searcher.get_card_html(card_id)
        packs = self.card_searcher.parse_card_packs(html_text)
        
        if not packs:
            await event.send(event.plain_result(f"📦【{card_name}】暂无卡盒收录信息或解析失败。"))
            return
            
        # 3. 构建回复 (如果太长则截断)
        msg_lines = [f"📦【{card_name}】收录详情 ({len(packs)}条):"]
        
        # 只显示前 15 条，防止刷屏
        display_packs = packs[:15]
        for p in display_packs:
            msg_lines.append(p)
            
        if len(packs) > 15:
            msg_lines.append(f"...以及其他 {len(packs)-15} 个卡盒")
            
        await event.send(event.plain_result("\n".join(msg_lines)))

    @filter.command("查询裁定", alias=["/查询裁定", "查裁定", "/查裁定", "/查询FAQ", "查询FAQ", "/查FAQ", "查FAQ"])
    async def handle_query_rulings(self, event: AstrMessageEvent):
        """查询当前卡片的官方裁定(Q&A)"""
        user_id = getattr(event.message_obj, "sender_id", "unknown")
        
        # 1. 检查缓存
        if user_id not in self.last_viewed_cards:
            await event.send(event.plain_result("⚠️ 请先使用 /查卡 或 /查卡序号 查看一张卡片。"))
            return
            
        card_info = self.last_viewed_cards[user_id]
        card_id = card_info["card_id"]
        card_name = card_info["card_name"]
        
        await event.send(event.plain_result(f"🔍 正在查询【{card_name}】的官方裁定..."))
        
        # 2. 获取 HTML 并解析
        html_text = await self.card_searcher.get_card_html(card_id)
        faqs = self.card_searcher.parse_card_faq(html_text)
        
        if not faqs:
            await event.send(event.plain_result(f"⚖️【{card_name}】暂无收录的官方裁定(Q&A)。"))
            return
            
        # 3. 发送 (由于裁定字数很多，建议合并转发或分条发送，这里暂时合并发送文本)
        # 如果条数太多，我们只发前 3 条，或者提示去网页看
        
        chain = [Comp.Plain(f"⚖️【{card_name}】裁定 Q&A ({len(faqs)}条):\n")]
        
        # 限制显示前 3 条，以免消息过长发不出去
        limit = 3
        for i, qa in enumerate(faqs[:limit]):
            chain.append(Comp.Plain(f"\nQ{i+1}: {qa['title']}\n"))
            chain.append(Comp.Plain(f"问: {qa['q']}\n"))
            chain.append(Comp.Plain(f"答: {qa['a']}\n"))
            chain.append(Comp.Plain("-" * 20))
            
        if len(faqs) > limit:
            chain.append(Comp.Plain(f"\n...剩余 {len(faqs)-limit} 条裁定请访问网页查看: https://ygocdb.com/card/{card_id}"))
            
        await event.send(event.chain_result(chain))

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
        """接收 YDK 文本并更新缓存"""
        session_id = self._get_session_id(event)
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
        """解析 ourocg/ygo 卡组链接 或 YDKe 代码并转化为ydk文件缓存"""
        session_id = self._get_session_id(event)
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

    # ================= 决斗模拟器指令 (v1.4.0) =================

    @filter.command("卡组转存", alias=["/卡组转存"])
    async def handle_deck_transfer(self, event: AstrMessageEvent):
        """将当前群聊的 YDK 存入用户的私有仓库"""
        # 1. 获取群组ID和用户ID
        group_id = getattr(event.message_obj, "group_id", None)
        sender_id = getattr(event.message_obj, "sender_id", None)
        # 兼容性处理
        if not sender_id and hasattr(event.message_obj, "sender"):
             sender_id = getattr(event.message_obj.sender, "user_id", None)

        if not group_id:
            await event.send(event.plain_result("⚠️ 请在群聊中使用此指令，用于将群内讨论的卡组保存为您的私有卡组。"))
            return
        if not sender_id:
            await event.send(event.plain_result("❌ 无法获取您的用户 ID。"))
            return

        src_session = f"group_{group_id}"
        target_session = f"user_{sender_id}"
        
        if self.ydk_manager.copy_ydk_from_session(src_session, target_session):
            await event.send(event.plain_result(f"✅ 卡组已转存至您的私人空间！\n您现在可以在任何地方使用 /卡组起手 来练习这套卡组。"))
        else:
            await event.send(event.plain_result(f"⚠️ 当前群聊没有缓存的卡组文件。请先使用 /MD查卡组 或 /接收ydk文本。"))

    @filter.command("卡组分享", alias=["/卡组分享", "/分享卡组", "分享卡组"])
    async def handle_deck_share(self, event: AstrMessageEvent):
        """将私人卡组分享到当前群聊"""
        group_id = getattr(event.message_obj, "group_id", None)
        sender_id = getattr(event.message_obj, "sender_id", None)
        if not sender_id and hasattr(event.message_obj, "sender"):
             sender_id = getattr(event.message_obj.sender, "user_id", None)

        if not group_id:
            await event.send(event.plain_result("⚠️ 此指令仅限群聊使用。"))
            return

        user_session = f"user_{sender_id}"
        group_session = f"group_{group_id}"
        
        # 检查自己有没有卡组
        user_ydk_path = os.path.join(self.ydk_manager.cache_dir, f"deck_{user_session}.ydk")
        if not os.path.exists(user_ydk_path):
            await event.send(event.plain_result("⚠️ 您的私人仓库为空，无法分享。请先导入一套卡组。"))
            return

        # 执行复制: 私 -> 群
        if self.ydk_manager.copy_ydk_from_session(user_session, group_session):
            await event.send(event.plain_result("✅ 已将您的私人卡组分享到当前群聊！\n群友们可以直接使用 /卡组起手 体验这套卡组了。"))
        else:
            await event.send(event.plain_result("❌ 分享失败。"))

    @filter.command("卡组起手", alias=["/卡组起手"])
    async def handle_sim_start(self, event: AstrMessageEvent):
        """
        初始化决斗模拟：
        优先使用私人卡组，如果私人为空且在群聊中，自动获取群卡组。
        """
        # 1. 获取 User Key
        sender_id = getattr(event.message_obj, "sender_id", None)
        if not sender_id and hasattr(event.message_obj, "sender"):
             sender_id = getattr(event.message_obj.sender, "user_id", None)
        if not sender_id:
             await event.send(event.plain_result("❌ 无法获取用户身份。"))
             return
        user_key = str(sender_id)
        user_session = f"user_{sender_id}"

        # 2. 尝试读取私人 YDK
        main, _, _ = self.ydk_manager.load_last_ydk(user_session)
        
        # 3. 如果私人没卡组，尝试自动从群聊获取
        if not main:
            group_id = getattr(event.message_obj, "group_id", None)
            if group_id:
                group_session = f"group_{group_id}"
                # 尝试复制 群 -> 私
                if self.ydk_manager.copy_ydk_from_session(group_session, user_session):
                    # 复制成功后，重新读取私人 YDK
                    main, _, _ = self.ydk_manager.load_last_ydk(user_session)
                    await event.send(event.plain_result("💡 检测到您没有私人卡组，已自动载入当前群聊卡组。"))
        
        # 4. 还是没有（私没有，且群也没有/不在群）
        if not main:
            await event.send(event.plain_result(f"⚠️ 无法启动决斗。\n请先导入卡组（私聊发送YDK），或者等待群友分享卡组。"))
            return
            
        # 5. 初始化并抽卡
        self.duel_sim.init_duel(user_key, main)
        hand = self.duel_sim.draw_card(user_key, 5)
        
        # 6. 绘图与发送
        img_path = await self.ydk_manager.draw_cards_image(hand, f"Starting Hand ({len(hand)})")
        
        chain = [Comp.Plain(f"🎲 决斗开始！卡组已重置 (Main: {len(main)})\n已抽取起手 5 张：")]
        if img_path:
            chain.append(Comp.Image.fromFileSystem(img_path))
        await event.send(event.chain_result(chain))

    @filter.command("卡组抽卡", alias=["/卡组抽卡"])
    async def handle_sim_draw(self, event: AstrMessageEvent):
        """模拟抽一张卡"""
        sender_id = getattr(event.message_obj, "sender_id", None)
        if not sender_id and hasattr(event.message_obj, "sender"):
             sender_id = getattr(event.message_obj.sender, "user_id", None)
        user_key = str(sender_id)
        
        # 检查状态
        state = self.duel_sim.get_state(user_key)
        if not state:
            await event.send(event.plain_result("⚠️ 请先发送 /卡组起手 开始新对局"))
            return
        if not state["deck"]:
            await event.send(event.plain_result("⚠️ 卡组已经抽干了！(Deck Out)"))
            return
            
        # 抽卡
        drawn = self.duel_sim.draw_card(user_key, 1)
        card_id = drawn[0]
        
        # 获取名字
        detail = await self.card_searcher.get_card_detail(card_id)
        name = detail.get("cn_name", "未知卡片")
        
        # 绘图
        img_path = await self.ydk_manager.draw_cards_image(drawn, f"Draw: {name}")
        
        chain = [Comp.Plain(f"🎴 抽牌！\n{name}\n剩余卡组: {len(state['deck'])}")]
        if img_path:
            chain.append(Comp.Image.fromFileSystem(img_path))
        await event.send(event.chain_result(chain))

    @filter.command("卡组检索", alias=["/卡组检索"])
    async def handle_sim_search(self, event: AstrMessageEvent):
        """从卡组检索特定卡片"""
        sender_id = getattr(event.message_obj, "sender_id", None)
        if not sender_id and hasattr(event.message_obj, "sender"):
             sender_id = getattr(event.message_obj.sender, "user_id", None)
        user_key = str(sender_id)

        msg = event.get_message_str().strip()
        parts = msg.split(maxsplit=1)
        if len(parts) < 2:
            await event.send(event.plain_result("请输入要检索的卡名，例如: /卡组检索 增殖的G"))
            return
        query = parts[1]
        
        state = self.duel_sim.get_state(user_key)
        if not state:
            await event.send(event.plain_result("⚠️ 请先发送 /卡组起手"))
            return
        if not state["deck"]:
            await event.send(event.plain_result("⚠️ 卡组为空。"))
            return

        await event.send(event.plain_result(f"🔍 正在检索【{query}】..."))

        # 1. 查卡获取 ID
        search_res = await self.card_searcher.search_card(query)
        if "error" in search_res or not search_res.get("result"):
             await event.send(event.plain_result("❌ 未找到该卡片信息。"))
             return
        
        # 2. 匹配卡组
        candidates = search_res["result"]
        target_id = None
        target_name = ""
        
        for card in candidates:
            cid = str(card["id"])
            # 利用 simulator 的 check 方法
            if self.duel_sim.check_deck_contains(user_key, cid):
                target_id = cid
                target_name = card["cn_name"]
                break
        
        if target_id:
            # 3. 移动卡片
            self.duel_sim.remove_from_deck_to_hand(user_key, target_id)
            
            # 4. 展示
            img_path = await self.ydk_manager.draw_cards_image([target_id], f"Search: {target_name}")
            chain = [Comp.Plain(f"✅ 检索成功：【{target_name}】加入手牌。\n剩余卡组: {len(state['deck'])}")]
            if img_path:
                chain.append(Comp.Image.fromFileSystem(img_path))
            await event.send(event.chain_result(chain))
        else:
            await event.send(event.plain_result(f"⚠️ 卡组中没有【{query}】(或已全部上手)。"))

    @filter.command("卡组状态", alias=["/卡组状态"])
    async def handle_sim_status(self, event: AstrMessageEvent):
        """查看当前手牌和卡组数量"""
        sender_id = getattr(event.message_obj, "sender_id", None)
        if not sender_id and hasattr(event.message_obj, "sender"):
             sender_id = getattr(event.message_obj.sender, "user_id", None)
        user_key = str(sender_id)
        
        state = self.duel_sim.get_state(user_key)
        if not state:
            await event.send(event.plain_result("⚠️ 未进行对局。"))
            return
            
        hand = state["hand"]
        deck_count = len(state["deck"])
        
        img_path = await self.ydk_manager.draw_cards_image(hand, f"Hand ({len(hand)}) | Deck: {deck_count}")
        
        chain = [Comp.Plain(f"📊 当前状态\n🎴 手牌: {len(hand)} 张\n📚 卡组: {deck_count} 张")]
        if img_path:
            chain.append(Comp.Image.fromFileSystem(img_path))
        await event.send(event.chain_result(chain))

    @filter.command("卡组状态重置", alias=["/卡组状态重置", "/重置决斗", "/重置卡组" , "重置决斗", "重置卡组"])
    async def handle_sim_reset(self, event: AstrMessageEvent):
        """
        重置当前用户的决斗状态：
        清空手牌，将所有卡片洗回卡组。
        不会影响任何已保存的文件。
        """
        # 1. 获取 User Key
        sender_id = getattr(event.message_obj, "sender_id", None)
        if not sender_id and hasattr(event.message_obj, "sender"):
             sender_id = getattr(event.message_obj.sender, "user_id", None)
        if not sender_id:
             await event.send(event.plain_result("❌ 无法获取用户身份。"))
             return
        user_key = str(sender_id)
        user_session = f"user_{sender_id}"

        # 2. 重新读取私有 YDK (作为重置的基准)
        main, _, _ = self.ydk_manager.load_last_ydk(user_session)
        
        if not main:
            await event.send(event.plain_result("⚠️ 您没有正在使用的私有卡组，无法重置。\n请先使用 /卡组起手 或 /卡组转存。"))
            return

        # 3. 初始化模拟器 (这就相当于重置了)
        # init_duel 会把传入的 main 列表作为新卡组，并清空手牌
        self.duel_sim.init_duel(user_key, main)
        
        # 4. 反馈
        await event.send(event.plain_result(f"🔄 状态已重置！\n手牌已清空，所有卡片({len(main)}张)已洗回卡组。\n您可以发送 /卡组抽卡 开始操作。"))

    @filter.command("禁卡表更新", alias=["/禁卡表更新", "/更新禁卡表", "更新禁卡表"])
    async def handle_banlist_update(self, event: AstrMessageEvent):
        """
        更新禁卡表数据。
        用法: /禁卡表更新 [OCG/简中] (默认OCG)
        """
        msg = event.get_message_str().strip().upper()
        parts = msg.split()
        
        # === 修改默认值为 OCG ===
        target_env = "ocg" 
        target_name = "OCG"
        
        if len(parts) > 1:
            if "简中" in parts[1] or "SC" in parts[1]:
                target_env = "sc"
                target_name = "简中"
            # 如果显式输入 OCG 也是 OCG
            elif "OCG" in parts[1]:
                target_env = "ocg"
                target_name = "OCG"
        
        await event.send(event.plain_result(f"⏳ 正在获取最新 {target_name} 禁卡表，这可能需要一点时间..."))
        
        # 传入 card_searcher 用于变动卡名翻译
        success, info, changes = await self.banlist_manager.update_banlist(target_env, self.card_searcher)
        
        if not success:
            await event.send(event.plain_result(f"❌ {info}"))
            return

        result_msg = [f"✅ {target_name} 禁卡表 {info}"]
        
        if changes:
            result_msg.append("\n📊 本期变动 (中文译名):")
            result_msg.extend([f"• {c}" for c in changes])
        else:
            result_msg.append("\n(本期无卡片状态变动)")
            
        await event.send(event.plain_result("\n".join(result_msg)))


    @filter.command("卡组检查", alias=["/卡组检查", "/检查卡组", "检查卡组"])
    async def handle_deck_check(self, event: AstrMessageEvent):
        """检查卡组。用法: /卡组检查 [OCG/简中]"""
        msg = event.get_message_str().strip().upper()
        parts = msg.split()
        target_env = "ocg"
        env_display = "OCG"
        
        if len(parts) > 1:
            if "简中" in parts[1] or "SC" in parts[1]:
                target_env = "sc"
                env_display = "简中"
            # 如果显式输入 OCG 也是 OCG
            elif "OCG" in parts[1]:
                target_env = "ocg"
                env_display = "OCG"
        
        sender_id = getattr(event.message_obj, "sender_id", None)
        if not sender_id and hasattr(event.message_obj, "sender"):
             sender_id = getattr(event.message_obj.sender, "user_id", None)
        user_session = f"user_{sender_id}"
        
        main, extra, side = self.ydk_manager.load_last_ydk(user_session)
        if not main:
            await event.send(event.plain_result("⚠️ 未找到卡组。"))
            return

        res = self.banlist_manager.check_deck_legality(target_env, main, extra, side)
        
        lines = [f"📊 卡组检查报告 ({env_display}环境)"]
        
        ban_issues = res["banlist_issues"]
        if not ban_issues:
            lines.append("✅ 禁限表: 合规")
        else:
            lines.append("❌ 禁限表违规:")
            for cid, status, count, limit in ban_issues:
                # 查中文名
                detail = await self.card_searcher.get_card_detail(cid)
                name = detail.get("cn_name", f"ID:{cid}") # 兜底显示ID
                lines.append(f"   • [{status}] {name}: 投入 {count} 张 (上限 {limit})")

        g_points = res["genesys_points"]
        g_details = res["genesys_details"]
        
        lines.append(f"\n🧬 Genesys点数: {g_points} pt")
        if g_points > 0:
            lines.append("   (点数明细):")
            for cid, pts, count in g_details:
                # 查中文名
                detail = await self.card_searcher.get_card_detail(cid)
                name = detail.get("cn_name", f"ID:{cid}") # 兜底显示ID
                lines.append(f"   • {name}: {pts}pt × {count}")

        await event.send(event.plain_result("\n".join(lines)))


    @filter.command("Genesys更新", alias=["/Genesys更新", "/更新G点", "更新G点"])
    async def handle_genesys_update(self, event: AstrMessageEvent):
        """从官网更新 Genesys 构筑点数"""
        await event.send(event.plain_result("⏳ 正在连接 Genesys 官网抓取数据... (解析卡名可能需要几十秒，请稍候)"))
        
        # 传入 card_searcher 以便进行英文名 -> ID 的反查
        success, msg, report = await self.banlist_manager.update_genesys(self.card_searcher)
        
        if not success:
            await event.send(event.plain_result(f"❌ {msg}"))
            return

        # 构建详细报告
        lines = [f"✅ {msg}", "", "📊 收录样本 (前15条):"]
        
        # 为了让报告更好看，我们随机取样或者取前几条
        # 这里取前15条展示
        if report:
            preview = report[:15]
            for item in preview:
                lines.append(f"• {item}")
            if len(report) > 15:
                lines.append(f"...以及其他 {len(report)-15} 条")
        else:
            lines.append("(未获取到具体明细，可能是解析失败)")
            
        await event.send(event.plain_result("\n".join(lines)))

    # ================= 帮助指令 =================

    @filter.command("游戏王帮助", alias=["/游戏王帮助", "游戏王指令", "/游戏王指令", "duelhelp", "/duelhelp"])
    async def handle_duel_help(self, event: AstrMessageEvent):
        """发送插件功能总览 (全指令收录 v1.4.0)"""
        
        help_text = [
            "Duel_galatea 游戏王工具箱 v1.4.0",
            "================================",
            "🔍 **基础查卡**",
            "• `/查卡 <卡名>` : 查询卡片详情、价格、G点及状态",
            "• `/查卡序号 <数字>` : 选中特定卡片",
            "• `/查卡换页 <数字>` : 跳转到指定查卡页码",
            "• `/发送高清卡图` : 获取上一张卡的大图",
            "• `/随机一卡` : 每日一抽",
            "• `/查询裁定` : 查看官方Q&A (新!)",
            "• `/查询卡盒` : 查看收录信息 (新!)",
            "• `/发动王牌圣杯` : 扔硬币！！！！",
            "",
            "⚔️ **决斗模拟**",
            "• `/卡组起手` : 模拟起手5张 (优先私有，其次群组)",
            "• `/卡组抽卡` : 模拟抽1张",
            "• `/卡组检索 <卡名>` : 检索特定卡片上手(模糊检索)",
            "• `/卡组状态` : 查看手牌/卡组余量",
            "• `/卡组状态重置` : 洗牌并重置",
            "",
            "💾 **卡组管理**",
            "• `/接收卡组链接 <链接>` : 解析 Ourocg/YDKe 链接作为卡组缓存",
            "• `/接收ydk文本` : (粘贴纯文本内容) 解析ydk文本并作为卡组缓存",
            "• `/发送ydk` : 发送当前缓存的 YDK 文件",
            "• `/发送卡组图片` : 生成当前卡组的构筑图",
            "• `/卡组转存` : 将群卡组存入私有仓库",
            "• `/卡组分享` : 将私有卡组分享到群聊",
            "• `/卡组检查 [OCG/简中]` : 检查[OCG/简中]禁限与Genesys点数",
            "",
            "📊 **环境与T表 (OCG/MD/DL)**",
            "• `/OCG饼图更新` / `/OCG饼图` : RoTK环境饼图",
            "• `/MD更新T表` / `/MD查询T表` : Master Duel T表",
            "• `/DL更新T表` / `/DL查询T表` : Duel Links T表",
            "• `/MD查卡组 <卡组名>` : 查询MD主流构筑",
            "• `/DL查卡组 <卡组名>` : 查询DL主流构筑",
            "• `/翻译T表 [DL/MD]` : 尝试自动汉化T表",
            "• `/查询卡组翻译 <英文>` : 查询本地对应卡组翻译映射",
            "• `/修改卡组翻译 <英文> <中文>` : 手动修正对应卡组翻译",
            "",
            "🚫 **禁卡表与规则**",
            "• `/禁卡表更新 [OCG/简中]` : 同步[OCG/简中]官方禁卡表",
            "• `/Genesys更新` : 同步 Genesys 构筑点数",
            "================================",
            "💡 **提示**",
            "1. 卡组管理支持会话隔离：私聊是个人仓库，群聊是公共仓库，可用转存/分享流转。",
            "2. 查卡组支持模糊匹配中文译名 (如: /MD查卡组 闪刀)。",
            "3. 部分更新指令可能需要网络条件良好才能成功。",
        ]
        
        await event.send(event.plain_result("\n".join(help_text)))