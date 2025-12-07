# -*- coding: utf-8 -*-
"""
Duel Galatea - 游戏王全能插件
富媒体消息版本
"""

import os
import json
import random  # 移到顶部
import re  # 移到顶部
from typing import Dict, Any, List
import aiohttp

from astrbot.api.star import Star, register
from astrbot.api.event import filter
from astrbot.core.config.astrbot_config import AstrBotConfig
from astrbot.core.platform.astr_message_event import AstrMessageEvent
import astrbot.api.message_components as Comp

# 确保 generic_tier_manager.py 在同一目录下
from .generic_tier_manager import GameType, TierCommandHandler

#  deck_breakdown.py
from .deck_breakdown import DeckBreakdownManager
from .rotk_manager import RotKManager


class YugiohCardSearcher:
    def __init__(self):
        self.base_url = "https://ygocdb.com/api/v0"

    # 修复后的 YugiohCardSearcher.search_card
    async def search_card(self, query: str) -> Dict[str, Any]:
        """异步搜索卡片"""
        try:
            url = f"{self.base_url}/?search={query}"
            headers = {"User-Agent": "Mozilla/5.0"}
            async with aiohttp.ClientSession(headers=headers) as session:
                async with session.get(url, timeout=10) as response:
                    if response.status == 200:
                        # 👈 修复：将解析结果返回
                        return await response.json(content_type=None)
                    else:
                        return {"error": f"API请求失败: {response.status}"}
        except Exception as e:
            # 错误信息应该用字典包裹
            return {"error": f"搜索出错: {str(e)}"}

    # 修复后的 YugiohCardSearcher.get_card_detail
    async def get_card_detail(self, card_id: str) -> Dict[str, Any]:
        """异步获取卡片详情"""
        try:
            url = f"{self.base_url}/card/{card_id}?show=all"
            headers = {"User-Agent": "Mozilla/5.0"}
            async with aiohttp.ClientSession(headers=headers) as session:
                async with session.get(url, timeout=10) as response:
                    if response.status == 200:
                        # 👈 修复：将解析结果返回
                        return await response.json(content_type=None)
                    else:
                        return {"error": f"获取详情失败: {response.status}"}
        except Exception as e:
            return {"error": f"获取详情出错: {str(e)}"}

    def format_card_info(self, card_data: Dict[str, Any]) -> str:
        if "error" in card_data:
            return card_data["error"]
        try:
            info = []
            cn_name = card_data.get("cn_name", "未知")
            sc_name = card_data.get("sc_name", "")
            name_display = (
                "{} ({})".format(cn_name, sc_name)
                if sc_name and sc_name != cn_name
                else cn_name
            )
            info.append("🃏 名称: {}".format(name_display))
            info.append("🆔 密码: {}".format(card_data.get("id", "未知")))
            text_data = card_data.get("text", {})
            types_str = text_data.get("types", "")
            if types_str:
                info.append("🏷 卡片类型: {}".format(types_str))
            data = card_data.get("data", {})
            card_type_value = data.get("type", 0)
            is_monster = (card_type_value & 1) != 0
            if not is_monster:
                desc = text_data.get("desc", "")
                if desc:
                    info.append("🔹 卡片效果:\n{}".format(desc))
            else:
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
                attribute_map = {
                    1: "地",
                    2: "水",
                    4: "炎",
                    8: "风",
                    16: "光",
                    32: "暗",
                    64: "神",
                }
                race_map = {
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
                attribute = data.get("attribute", 0)
                if attribute in attribute_map:
                    info.append("属性: {}".format(attribute_map[attribute]))
                race = data.get("race", 0)
                if race in race_map:
                    info.append("种族: {}".format(race_map[race]))
                if is_pendulum:
                    scale_matches = re.findall(r"(\d+)/(\d+)", types_str)
                    if scale_matches and len(scale_matches) >= 1:
                        left_scale, right_scale = scale_matches[-1]
                        info.append(
                            "🔹 灵摆刻度: {}/{}".format(left_scale, right_scale)
                        )
                    pdesc = text_data.get("pdesc", "")
                    if pdesc:
                        info.append("🔸 灵摆效果:\n{}".format(pdesc))
                desc = text_data.get("desc", "")
                if desc:
                    effect_title = "🔹 怪兽效果:" if is_pendulum else "🔹 卡片效果:"
                    info.append("{}\n{}".format(effect_title, desc))

            return "\n".join(info)
        except Exception as e:
            return "格式化出错: {}".format(str(e))

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

        # 自动获取当前插件目录，避免硬编码路径
        current_file_path = os.path.abspath(__file__)
        self.plugin_dir = os.path.dirname(current_file_path)

        # 初始化 T表管理器
        self.tier_handler = TierCommandHandler(self.plugin_dir)
        # 新增：卡组详情查询器
        self.deck_breakdown = DeckBreakdownManager(self.plugin_dir)
        # 新增：RotK 管理器
        self.rotk_manager = RotKManager(self.plugin_dir)
        # 加载ID
        self._load_card_ids()

    def _load_card_ids(self):
        """加载纯ID列表到内存"""
        try:
            ids_file_path = os.path.join(self.plugin_dir, "card_ids.json")

            if os.path.exists(ids_file_path):
                with open(ids_file_path, "r", encoding="utf-8") as f:
                    self.all_card_ids = json.load(f)
                print(f" 成功加载 {len(self.all_card_ids)} 个卡片ID到随机池")
            else:
                print(" 未找到card_ids.json文件")
                self._load_backup_ids()

        except Exception as e:
            print(f" 加载卡片ID失败: {e}")
            self._load_backup_ids()

    def _load_backup_ids(self):
        """备用ID列表（以防主文件加载失败）"""
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
        print(f"️ 使用备用ID列表，共 {len(backup_ids)} 个ID")

    # 修复：on_message 必须在类的一级缩进中，不能在函数里
    async def on_message(self, event: AstrMessageEvent):
        pass  # 如果不需要处理普通消息，保持 pass 即可

    @filter.command("查卡", alias={"/查卡"})
    async def handle_cha_ka(self, event: AstrMessageEvent):
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

        if "error" in result:
            await event.send(
                event.plain_result(" 搜索出错: {}".format(result["error"]))
            )
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
                event.plain_result(" 未找到与'{}'相关的卡片".format(query))
            )

    @filter.command("查卡换页", alias={"/查卡换页"})
    async def handle_change_page(self, event: AstrMessageEvent):
        message_text = event.get_message_str().strip()
        user_id = getattr(event.message_obj, "sender_id", "unknown")
        parts = message_text.split() if message_text else []

        page_str = parts[1] if len(parts) > 1 else ""
        if not page_str or not page_str.isdigit():
            await event.send(event.plain_result("请输入有效的页码，例如: /查卡换页 2"))
            return

        page = int(page_str)
        if user_id not in self.search_sessions:
            await event.send(
                event.plain_result(
                    "没有正在进行的搜索会话，请先使用 /查卡 命令搜索卡片"
                )
            )
            return

        session = self.search_sessions[user_id]
        results = session["results"]
        page_size = session["page_size"]
        total_pages = (len(results) + page_size - 1) // page_size

        if page < 1 or page > total_pages:
            await event.send(
                event.plain_result(
                    "页码超出范围，请输入 1 到 {} 之间的数字".format(total_pages)
                )
            )
            return

        session["current_page"] = page
        response_text = self.card_searcher.format_search_results(results, page, user_id)
        await event.send(event.plain_result(response_text))

    @filter.command("查卡序号", alias={"/查卡序号"})
    async def handle_select_card(self, event: AstrMessageEvent):
        """通过序号查看卡片详情 - 支持富媒体消息"""
        message_text = event.get_message_str().strip()
        user_id = getattr(event.message_obj, "sender_id", "unknown")
        parts = message_text.split() if message_text else []

        card_number_str = parts[1] if len(parts) > 1 else ""
        if not card_number_str.isdigit():
            await event.send(event.plain_result("请输入卡片序号，例如: /查卡序号 1"))
            return

        card_number = int(card_number_str)
        if user_id not in self.search_sessions:
            await event.send(
                event.plain_result(
                    "没有正在进行的搜索会话，请先使用 /查卡 命令搜索卡片"
                )
            )
            return

        session = self.search_sessions[user_id]
        results = session["results"]

        if 1 <= card_number <= len(results):
            selected_card = results[card_number - 1]
            card_id = selected_card["id"]
            self.last_viewed_cards[user_id] = {
                "card_id": card_id,
                "card_name": selected_card.get("cn_name", "未知"),
                "card_data": selected_card,
            }
            detail_result = await self.card_searcher.get_card_detail(str(card_id))
            formatted_detail = self.card_searcher.format_card_info(detail_result)

            # 构建富媒体消息链
            card_id_real = detail_result.get("id", "")
            if card_id_real:
                thumbnail_url = (
                    f"https://cdn.233.momobako.com/ygopro/pics/{card_id_real}.jpg!half"
                )
                message_chain = [
                    Comp.Image.fromURL(thumbnail_url),  # 缩略图
                    Comp.Plain("\n" + formatted_detail),  # 卡片详情
                ]
                await event.send(event.chain_result(message_chain))
            else:
                await event.send(event.plain_result(formatted_detail))
        else:
            await event.send(
                event.plain_result(f"序号超出范围，共 {len(results)} 个结果")
            )

    @filter.command("发送高清卡图", alias={"/发送高清卡图"})
    async def handle_send_image(self, event: AstrMessageEvent):
        """发送卡片高清图片"""
        message_text = event.get_message_str().strip()
        user_id = getattr(event.message_obj, "sender_id", "unknown")
        parts = message_text.split() if message_text else []
        card_id_str = parts[1] if len(parts) > 1 else ""

        # 如果指定了卡密
        if card_id_str:
            if card_id_str.isdigit():
                card_id = card_id_str
            else:
                await event.send(event.plain_result(" 卡片密码必须是数字"))
                return
        else:
            # 使用最近查看的卡片
            if user_id not in self.last_viewed_cards:
                await event.send(
                    event.plain_result("请先查看卡片详情，或在命令后指定卡片密码")
                )
                return
            card_id = self.last_viewed_cards[user_id]["card_id"]

        # 发送高清图片
        image_url = "https://cdn.233.momobako.com/ygopro/pics/{}.jpg".format(card_id)
        try:
            await event.send(event.image_result(image_url))
        except:
            await event.send(event.plain_result("卡片高清图片:\n{}".format(image_url)))

    @filter.command("随机一卡", alias={"/随机一卡"})
    async def handle_random_card(self, event: AstrMessageEvent):
        """从全卡片ID池中随机抽取一张卡片"""
        if not self.all_card_ids:
            await event.send(event.plain_result(" 卡片数据库未加载"))
            return

        try:
            random_card_id = random.choice(self.all_card_ids)
            detail_result = await self.card_searcher.get_card_detail(
                str(random_card_id)
            )

            if "error" not in detail_result and "data" in detail_result:
                formatted_detail = self.card_searcher.format_card_info(detail_result)
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
            else:
                await self._retry_random_card(event)

        except Exception as e:
            print(f" 随机抽取异常: {str(e)}")
            await event.send(event.plain_result(" 抽取失败，请稍后再试"))

    async def _retry_random_card(self, event):
        """重试随机抽取"""
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
                    return
            except:
                continue

    @filter.command("发动王牌圣杯", alias={"/发动王牌圣杯"})
    async def handle_holy_grail(self, event: AstrMessageEvent):
        """发动王牌圣杯"""
        is_positive = random.choice([True, False])
        if is_positive:
            card_id = "55144522"  # 正面
            message_text = "是正面！抽2张卡！"
        else:
            card_id = "5915629"  # 反面
            message_text = "是反面......对方抽2张卡。"

        thumbnail_url = f"https://cdn.233.momobako.com/ygopro/pics/{card_id}.jpg!half"
        message_chain = [
            Comp.Image.fromURL(thumbnail_url),
            Comp.Plain("\n" + message_text),
        ]
        await event.send(event.chain_result(message_chain))

    # ================= T表指令 =================
    # 修复：增加了 self 参数，并复用了 self.tier_handler

    @filter.command("DL更新T表", alias=["/DL更新T表"])
    async def handle_dl_update_tier(self, event: AstrMessageEvent):
        """更新Duel Links T表"""
        await self.tier_handler.update_tier_list(
            event, GameType.DUEL_LINKS, "Duel Links"
        )

    @filter.command("DL查询T表", alias=["/DL查询T表"])
    async def handle_dl_query_tier(self, event: AstrMessageEvent):
        """查询Duel Links T表"""
        await self.tier_handler.query_tier_list(
            event, GameType.DUEL_LINKS, "Duel Links"
        )

    @filter.command("MD更新T表", alias=["/MD更新T表"])
    async def handle_md_update_tier(self, event: AstrMessageEvent):
        """更新Master Duel T表"""
        await self.tier_handler.update_tier_list(
            event, GameType.MASTER_DUEL, "Master Duel"
        )

    @filter.command("MD查询T表", alias=["/MD查询T表"])
    async def handle_md_query_tier(self, event: AstrMessageEvent):
        """查询Master Duel T表"""
        await self.tier_handler.query_tier_list(
            event, GameType.MASTER_DUEL, "Master Duel"
        )

    # ... (接在 DL/MD 查询指令后面)

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

    # ================= 卡组详情查询 (新增) =================

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

        deck_name = parts[1]
        await event.send(
            event.plain_result(
                f"🔍 [MDM] 正在抓取【{deck_name}】数据并生成构筑图，请稍候..."
            )
        )

        try:
            result = await self.deck_breakdown.fetch_deck_breakdown(
                deck_name, GameType.MASTER_DUEL
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

        except Exception:
            print("查询出错")

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

        deck_name = parts[1]
        await event.send(
            event.plain_result(
                f"🔍 [DLM] 正在抓取【{deck_name}】数据并生成构筑图，请稍候..."
            )
        )

        try:
            result = await self.deck_breakdown.fetch_deck_breakdown(
                deck_name, GameType.DUEL_LINKS
            )
            text_msg = result.get("text", "无数据")
            image_path = result.get("image_path")

            chain = []
            if image_path and os.path.exists(image_path):
                chain.append(Comp.Image.fromFileSystem(image_path))

            chain.append(Comp.Plain(text_msg))

            await event.send(event.chain_result(chain))

        except Exception:
            print("查询出错")

    @filter.command("OCG饼图更新", alias=["/OCG饼图更新"])
    async def handle_ocg_update(self, event: AstrMessageEvent):
        """爬取并更新本地 OCG 饼图数据"""
        await event.send(
            event.plain_result("🔍 正在连接 RotK 抓取数据并下载图片 (耗时较长)...")
        )

        try:
            # 【变化点】直接 await，不再使用 run_in_executor
            result = await self.rotk_manager.fetch_latest_report()

            if result is None or "error" in result:
                err = result.get("error", "Unknown Error") if result else "Empty Result"
                await event.send(event.plain_result(f"⚠️ 更新失败: {err}"))
                return

            if self.rotk_manager.save_local_data(result):
                img_count = len(result.get("local_paths", []))
                msg = "✅ 更新完毕!\n"
                msg += f"📄 标题: {result['title']}\n"
                msg += f"📥 已下载: {img_count} 张图片\n"
                msg += "发送 /OCG饼图 即可秒速查看。"
                await event.send(event.plain_result(msg))
            else:
                await event.send(event.plain_result("⚠️ 抓取成功但保存失败"))

        except Exception as e:
            await event.send(event.plain_result(f"⚠️ 内部错误: {e}"))

    @filter.command("OCG饼图", alias=["/OCG饼图", "/OCG饼图查询", "OCG饼图查询"])
    async def handle_ocg_query(self, event: AstrMessageEvent):
        """查看本地保存的 OCG 饼图"""
        data = self.rotk_manager.load_local_data()

        if not data:
            await event.send(
                event.plain_result("⚠️ 本地暂无数据，请先发送 /OCG饼图更新")
            )
            return

        chain = []
        # 读取本地路径列表
        local_paths = data.get("local_paths", [])

        # 限制数量，比如前 9 张 (太多发不出来)
        for path in local_paths[:9]:
            if os.path.exists(path):
                # 【关键】发送本地文件
                chain.append(Comp.Image.fromFileSystem(path))
            else:
                print(f"Image missing: {path}")

        text = f"📊 {data['title']}\n"
        text += f"📅 发布日期: {data['date']}\n"
        text += f"🕒 缓存时间: {data['update_time']}\n"
        text += f"🔗 原文链接: {data['url']}\n"

        if len(local_paths) > 9:
            text += f"(共 {len(local_paths)} 张图，已显示前 9 张)"

        chain.append(Comp.Plain(text))
        await event.send(event.chain_result(chain))

    @filter.command("查询卡组翻译", alias=["/查询卡组翻译"])
    async def handle_query_translation(self, event: AstrMessageEvent):
        """查询本地存储的卡组翻译
        用法: /查询卡组翻译 Sky Striker
        """
        message_text = event.get_message_str().strip()
        parts = message_text.split(maxsplit=1)

        if len(parts) < 2:
            await event.send(
                event.plain_result(
                    "请输入要查询的名称，例如: /查询卡组翻译 Sky Striker"
                )
            )
            return

        query = parts[1]
        # 访问 tier_handler 里的 manager
        en, cn = self.tier_handler.manager.get_specific_translation(query)

        if en:
            msg = "🔍 翻译记录:\n"
            msg += f"🇺🇸 英文: {en}\n"
            msg += f"🇨🇳 中文: {cn}"
        else:
            msg = f"未找到关于 '{query}' 的翻译记录。\n(如果是新卡组，请尝试运行 /翻译T表，或使用 /修改卡组翻译 手动添加)"

        await event.send(event.plain_result(msg))

    @filter.command("修改卡组翻译", alias=["/修改卡组翻译"])
    async def handle_edit_translation(self, event: AstrMessageEvent):
        """手动修改或添加卡组翻译
        用法: /修改卡组翻译 Sky Striker 闪刀姬
        注意: 最后一个词会被识别为中文，前面的被识别为英文
        """
        message_text = event.get_message_str().strip()
        parts = message_text.split()

        if len(parts) < 3:
            await event.send(
                event.plain_result(
                    "格式错误。\n用法: /修改卡组翻译 [英文名] [中文名]\n示例: /修改卡组翻译 Sky Striker 闪刀姬"
                )
            )
            return

        # 逻辑：最后一个参数是中文，中间的全部拼起来算英文
        cn_name = parts[-1]
        en_name = " ".join(parts[1:-1])

        success = self.tier_handler.manager.set_manual_translation(en_name, cn_name)

        if success:
            msg = "修改成功!\n"
            msg += f"📝 映射关系已更新: [{en_name}] -> [{cn_name}]\n"
            msg += "下次查询 T 表或卡组时将立即生效。"
            await event.send(event.plain_result(msg))
        else:
            await event.send(event.plain_result("保存失败，请检查日志。"))
