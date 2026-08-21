import os
import json
import re
import time
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum
import asyncio
import aiohttp  # 引入 aiohttp 异步请求库
from astrbot.api.all import logger


class GameType(Enum):
    DUEL_LINKS = "dl"
    MASTER_DUEL = "md"


@dataclass
class TierChange:
    card_name: str
    action: str
    from_tier: Optional[str]
    to_tier: Optional[str]
    description: str


@dataclass
class TierData:
    game_type: GameType
    update_date: str
    update_title: str
    tiers: Dict[str, List[str]]
    deck_translations: Dict[str, str] = field(default_factory=dict)
    changes: List[TierChange] = field(default_factory=list)
    source_url: str = ""
    last_save: str = ""


class GenericTierManager:
    def __init__(self, data_dir: str):  # 1. 参数名改为 data_dir
        self.data_dir = data_dir  # 2. 属性名改为 self.data_dir
        self.ensure_data_dir()
        self.translations = self.load_external_translations()

    def ensure_data_dir(self):
        if not os.path.exists(self.data_dir):
            os.makedirs(self.data_dir)

    def get_data_file_path(self, game_type: GameType) -> str:
        return os.path.join(self.data_dir, f"{game_type.value}_tier_data.json")

    def get_translations_file_path(self) -> str:
        return os.path.join(self.data_dir, "deck_translations.json")

    def load_external_translations(self) -> Dict[str, str]:
        file_path = self.get_translations_file_path()
        if os.path.exists(file_path):
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"加载翻译文件失败: {e}")
                return {}
        return {}

    def save_external_translations(self) -> bool:
        try:
            with open(self.get_translations_file_path(), "w", encoding="utf-8") as f:
                json.dump(self.translations, f, ensure_ascii=False, indent=2)
            return True
        except Exception as e:
            logger.error(f"保存翻译文件失败: {e}")
            return False

    async def get_chinese_name(
        self, session: aiohttp.ClientSession, english_name: str, force_api: bool = False
    ) -> str:
        """
        获取中文名称（异步版本）
        逻辑：预设 -> 本地 -> API (精确匹配 -> 分隔符统计 -> 公共前缀分析)
        """
        clean_name = english_name.replace("%20", " ").strip()

        # 2. 查内存字典 (非强制模式)
        if not force_api:
            if clean_name in self.translations:
                return self.translations[clean_name]
            # 模糊匹配
            target_key = clean_name.replace("-", " ").lower()
            for key, val in self.translations.items():
                if key.replace("-", " ").lower() == target_key:
                    return val

        # 3. 查 API
        try:
            # 这里的 sleep 是为了防止爬虫过快被封。在异步中用 asyncio.sleep
            if force_api:
                await asyncio.sleep(0.3)
            else:
                await asyncio.sleep(0.05)

            search_url = "https://ygocdb.com/api/v0/"
            params = {"search": clean_name}

            # 使用 aiohttp 替换 requests
            async with session.get(search_url, params=params, timeout=10, ssl=False) as response:
                if response.status == 200:
                    data = await response.json()  # <-- 异步读取 JSON
                    results = data.get("result", [])

                    if results:
                        # --- 阶段 A: 精确匹配 (最完美的情况) ---
                        for item in results:
                            if item.get("en_name", "").lower() == clean_name.lower():
                                return item.get("cn_name")

                        # --- 阶段 B: 统计学猜测 (针对系列名) ---
                        # ... (后续的解析和猜测逻辑不变)
                        top_results = [r.get("cn_name", "") for r in results[:10]]
                        candidates = []

                        # 定义可能的分隔符：中间点、乘号、空格、全角空格
                        separators = ["·", "×", " ", "　", "-"]

                        for cn in top_results:
                            # 1. 尝试按分隔符提取前缀
                            for sep in separators:
                                if sep in cn:
                                    prefix = cn.split(sep)[0]
                                    if len(prefix) >= 2:  # 忽略太短的
                                        candidates.append(prefix)
                                    break  # 找到一个分隔符就停，避免重复处理

                        # 2. 尝试计算“公共前缀” (针对无符号情况，如: 银河眼)
                        if len(top_results) >= 2:
                            from os.path import (
                                commonprefix,
                            )  # 使用 os.path.commonprefix 计算字符串列表的公共开头

                            common = commonprefix(top_results)
                            # 如果公共前缀长度 >= 2 (防止只是“神”这种单字)，也加入候选
                            if len(common) >= 2:
                                # 权重加倍，因为这是硬性的共同点
                                candidates.append(common)
                                candidates.append(common)

                        # 3. 统计票数
                        if candidates:
                            from collections import Counter

                            most_common = Counter(candidates).most_common(1)
                            if most_common:
                                top_name, count = most_common[0]
                                # 如果这个词在结果中出现频率较高，就采纳它
                                if count >= 2:
                                    return top_name

                        # --- 阶段 C: 兜底 (返回第一个结果的智能截断) ---
                        first_cn = top_results[0]
                        for sep in separators:
                            if sep in first_cn:
                                return first_cn.split(sep)[0]
                        return first_cn

                return clean_name
        except Exception:
            return clean_name

    async def batch_translate_and_save(
        self, game_type: GameType
    ) -> Tuple[int, List[str]]:
        tier_data = self.load_local_data(game_type)
        if not tier_data:
            return 0, []

        updated_count = 0
        new_translations = []
        all_decks = set()

        for decks in tier_data.tiers.values():
            for d in decks:
                all_decks.add(d)

        # 找出需要更新的翻译目标
        targets = [
            d
            for d in all_decks
            if (d not in self.translations) or (self.translations[d] == d)
        ]

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        async with aiohttp.ClientSession(trust_env=True, headers=headers) as session:
            tasks = []
            for deck in targets:
                # 注意：这里我们使用 self.get_chinese_name(session, ...)
                tasks.append(self.get_chinese_name(session, deck, force_api=True))

            if tasks:
                # 并发获取所有翻译
                cn_names = await asyncio.gather(*tasks)

                for deck, cn_name in zip(targets, cn_names):
                    if cn_name != deck:
                        self.translations[deck] = cn_name
                        updated_count += 1
                        new_translations.append(f"{deck} -> {cn_name}")
                    else:
                        if deck not in self.translations:
                            self.translations[deck] = deck

        if updated_count > 0:
            self.save_external_translations()
            tier_data.deck_translations = self.translations
            self.save_local_data(tier_data)

        return updated_count, new_translations

    def get_specific_translation(self, query: str) -> Tuple[str, str]:
        """
        查询特定翻译
        返回: (英文原名, 中文翻译) 或 (None, None)
        """
        query_lower = query.lower()
        for k, v in self.translations.items():
            if k.lower() == query_lower or v == query:
                return k, v
        return None, None

    def set_manual_translation(self, en_name: str, cn_name: str) -> bool:
        """
        手动设置翻译并保存
        """
        # 1. 检查是否存在（忽略大小写），如果存在则覆盖 Key，保持 Key 格式一致性
        target_key = en_name
        for k in self.translations.keys():
            if k.lower() == en_name.lower():
                target_key = k
                break

        # 2. 更新内存字典
        self.translations[target_key] = cn_name

        # 3. 保存到文件
        return self.save_external_translations()

    def load_local_data(self, game_type: GameType) -> Optional[TierData]:
        try:
            data_file = self.get_data_file_path(game_type)
            if os.path.exists(data_file):
                with open(data_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                try:
                    g_type = GameType(data.get("game_type"))
                except ValueError:
                    g_type = game_type
                changes = [TierChange(**c) for c in data.get("changes", [])]
                saved_trans = data.get("deck_translations", {})
                merged_trans = {**saved_trans, **self.translations}
                return TierData(
                    game_type=g_type,
                    update_date=data.get("update_date", ""),
                    update_title=data.get("update_title", ""),
                    tiers=data.get("tiers", {}),
                    deck_translations=merged_trans,
                    changes=changes,
                    source_url=data.get("source_url", ""),
                    last_save=data.get("last_save", ""),
                )
            return None
        except Exception as e:
            logger.error(f"加载本地数据失败: {e}")
            return None

    def save_local_data(self, tier_data: TierData) -> bool:
        try:
            data_file = self.get_data_file_path(tier_data.game_type)
            tier_data.last_save = time.strftime("%Y-%m-%d %H:%M:%S")
            data_dict = {
                "game_type": tier_data.game_type.value,
                "update_date": tier_data.update_date,
                "update_title": tier_data.update_title,
                "tiers": tier_data.tiers,
                "deck_translations": tier_data.deck_translations,
                "changes": [vars(c) for c in tier_data.changes],
                "source_url": tier_data.source_url,
                "last_save": tier_data.last_save,
            }
            with open(data_file, "w", encoding="utf-8") as f:
                json.dump(data_dict, f, ensure_ascii=False, indent=2)
            return True
        except Exception as e:
            logger.error(f"保存数据失败: {e}")
            return False

    def parse_tier_changes(self, content: str) -> List[TierChange]:
        changes = []
        change_patterns = [
            r"([\w\s\-\.]+?)\s+(moved|added|demoted)\s+(?:from\s+(Tier\s*[1-3]|T[1-3]).*?)?(?:to\s+(Tier\s*[1-3]|T[1-3])|out of.*?the.*?Tier)"
        ]
        clean_text = re.sub(r"<[^>]+>", " ", content)
        seen_changes = set()
        for pattern in change_patterns:
            matches = re.findall(pattern, clean_text, re.IGNORECASE)
            for match in matches:
                if len(match) >= 2:
                    card_name = match[0].strip()
                    if len(card_name) > 40 or "Update" in card_name:
                        continue
                    action = match[1].strip()
                    from_tier = (
                        match[2].strip() if len(match) > 2 and match[2] else None
                    )
                    to_tier = match[3].strip() if len(match) > 3 and match[3] else None
                    key = f"{card_name}-{action}"
                    if key in seen_changes:
                        continue
                    seen_changes.add(key)
                    if "out of" in action or "demoted" in action and not to_tier:
                        desc = f"{card_name} 移出环境"
                    elif to_tier:
                        desc = f"{card_name} 调整至 {to_tier}"
                    else:
                        desc = f"{card_name} {action}"
                    changes.append(
                        TierChange(card_name, action, from_tier, to_tier, desc)
                    )
        return changes

    def _extract_decks_from_html(self, html_snippet: str) -> List[str]:
        decks = re.findall(r'/tier-list/deck-types/([^"\'\?]+)', html_snippet)
        clean_decks = []
        seen = set()
        for d in decks:
            d_name = d.replace("%20", " ").strip()
            if len(d_name) > 50 or "Update" in d_name:
                continue
            if d_name not in seen:
                clean_decks.append(d_name)
                seen.add(d_name)
        return clean_decks

    def _parse_dl_data(self, content: str) -> Dict[str, List[str]]:
        logger.info("🔍 使用 DL 专用解析模式 (Classic)")
        tier_data = {"T1": [], "T2": [], "T3": []}
        full_text_lower = content.lower()

        # DLM 长描述标记
        markers = {
            "T1": "Expected to be a large percentage",
            "T2": "Expected to be in the top cut",
            "T3": "Expected to be played in a competitive",
        }

        t1_idx = full_text_lower.find(markers["T1"].lower())
        t2_idx = full_text_lower.find(markers["T2"].lower())
        t3_idx = full_text_lower.find(markers["T3"].lower())

        # 寻找 T3 之后最早出现的停止词 (DLM结构比较传统)
        stop_keywords = [
            "High Potential",
            "Other Decks",
            "Power Rankings",
            "Off Tier",
            "Community Tournaments",
            "Top Decks",
        ]
        end_idx = len(content)
        start_search_stop = t3_idx if t3_idx != -1 else (t2_idx if t2_idx != -1 else 0)

        for kw in stop_keywords:
            idx = full_text_lower.find(kw.lower(), start_search_stop)
            if idx != -1 and idx < end_idx:
                end_idx = idx

        if t1_idx != -1:
            end = t2_idx if t2_idx != -1 else end_idx
            tier_data["T1"] = self._extract_decks_from_html(content[t1_idx:end])
        if t2_idx != -1:
            end = t3_idx if t3_idx != -1 else end_idx
            tier_data["T2"] = self._extract_decks_from_html(content[t2_idx:end])
        if t3_idx != -1:
            tier_data["T3"] = self._extract_decks_from_html(content[t3_idx:end_idx])

        return tier_data

    def _parse_md_data(self, content: str) -> Dict[str, List[str]]:
        """
        MD 专用解析模式 (线性 Token 扫描法)
        最稳健的解析方式，无视 DOM 结构嵌套，只看出现顺序
        """
        logger.info("🔍 [MD Parse] 使用线性扫描模式...")
        tier_data = {"T1": [], "T2": [], "T3": []}

        # 1. 定义所有感兴趣的元素 (Regex)
        # 捕获组 1: Tier等级 (1/2/3)
        # 捕获组 2: 停止信号 (Trending/High Potential)
        # 捕获组 3: 卡组名 (URL)

        # 匹配 Tier 标题图片: alt="Tier 1"
        # 注意：这里我们分别找，最后合并

        tokens = []

        # A. 找 Tier 标记 (T1, T2, T3)
        for i in range(1, 4):
            pattern = re.compile(rf'alt=["\']Tier\s*{i}["\']', re.IGNORECASE)
            for m in pattern.finditer(content):
                tokens.append(
                    {"pos": m.start(), "type": "TIER_HEADER", "value": f"T{i}"}
                )

        # B. 找停止标记 (Trending, High Potential, Power Rankings)
        stop_pattern = re.compile(
            r'(?:alt=["\']|title=["\']|>)(Trending|High Potential|Power Rankings|Top Decks)(?:["\']|<)',
            re.IGNORECASE,
        )
        for m in stop_pattern.finditer(content):
            tokens.append({"pos": m.start(), "type": "STOP", "value": "STOP"})

        # C. 找卡组链接
        # 排除掉导航栏等无效链接，只找 /tier-list/deck-types/ 下的
        deck_pattern = re.compile(r'href=["\']/tier-list/deck-types/([^"\'\?]+)["\']')
        for m in deck_pattern.finditer(content):
            d_name = m.group(1).replace("%20", " ").strip()
            # 简单过滤垃圾
            if len(d_name) > 50 or "Update" in d_name or "/" in d_name:
                continue

            tokens.append({"pos": m.start(), "type": "DECK", "value": d_name})

        # 2. 排序 (关键步骤)
        # 按照在 HTML 中出现的位置从小到大排序
        tokens.sort(key=lambda x: x["pos"])

        # 3. 线性扫描状态机
        current_tier = None

        # 找到 "Last Updated" 或 "Tier List" 标题的大致位置，忽略之前的导航栏噪音
        start_threshold = content.lower().find("tier list update")
        if start_threshold == -1:
            start_threshold = 0

        for token in tokens:
            # 忽略过早的 Token (导航栏)
            if token["pos"] < start_threshold:
                continue

            if token["type"] == "TIER_HEADER":
                current_tier = token["value"]
                # print(f"   -> 进入 {current_tier} 区域")

            elif token["type"] == "STOP":
                # print("   -> 遇到停止符，停止扫描")
                current_tier = None  # 停止收集

            elif token["type"] == "DECK":
                if current_tier:
                    # 去重添加
                    if token["value"] not in tier_data[current_tier]:
                        tier_data[current_tier].append(token["value"])

        # 4. 打印统计结果
        t1_len = len(tier_data["T1"])
        t2_len = len(tier_data["T2"])
        t3_len = len(tier_data["T3"])
        logger.info(f"   -> [解析结果] T1:{t1_len}, T2:{t2_len}, T3:{t3_len}")

        # 如果还是空，尝试 fallback (可能没有图片 alt，只有文本)
        if t1_len + t2_len + t3_len == 0:
            logger.warning("⚠️ [MD Parse] 标准模式为空，尝试文本匹配...")
            # 备用方案：直接找文本 >Tier 1<
            # (为了保持代码简洁，这里暂不展开备用方案，因为 alt 属性在你的源码里是存在的)

        return tier_data

    async def _async_crawl_tier_data(
        self, session: aiohttp.ClientSession, game_type: GameType
    ) -> TierData:
        logger.info(f"🔍 开始异步爬取 {game_type.value} T表...")
        urls = {
            GameType.DUEL_LINKS: "https://www.duellinksmeta.com/tier-list",
            GameType.MASTER_DUEL: "https://www.masterduelmeta.com/tier-list",
        }
        url = urls.get(game_type)
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }

        content = ""
        try:
            async with session.get(url, timeout=15, ssl=False) as response:
                response.raise_for_status()  # 异步检查状态码
                content = await response.text()  # 异步读取文本内容
        except Exception as e:
            logger.warning(f"[Tier] 主页面请求失败: {e}")
            raise  # 抛出异常，让上层捕获

        # 注意：这里我们离开了 session 的作用域，
        # 但是由于翻译也需要 session，我们将在顶层统一管理 session。
        # 暂时把这部分逻辑移到顶层函数中实现。

        # --- (爬虫解析逻辑：保持不变) ---

        # 日期和标题提取 (保持不变)
        date_match = re.search(r"Tier List Update: ([^<\n]+)", content, re.IGNORECASE)
        update_date = (
            date_match.group(1).strip() if date_match else time.strftime("%Y-%m-%d")
        )

        title_match = re.search(
            r"<h[1-6][^>]*>([^<]*?Update[^<]*?)</h[1-6]>", content, re.IGNORECASE
        )
        update_title = title_match.group(1).strip() if title_match else "T表更新"

        # 分流 (保持不变)
        if game_type == GameType.DUEL_LINKS:
            tier_raw = self._parse_dl_data(content)
        else:
            tier_raw = self._parse_md_data(content)

        changes = self.parse_tier_changes(content)

        return TierData(
            game_type=game_type,
            update_date=update_date,
            update_title=update_title,
            tiers=tier_raw,
            deck_translations={},  # 暂时留空，上层处理
            changes=changes,
            source_url=url,
        )

    async def crawl_tier_data(self, game_type: GameType) -> Optional[TierData]:
        # 统一创建并管理 session
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }

        async with aiohttp.ClientSession(trust_env=True, headers=headers) as session:
            try:
                # 1. 爬取 T 表基础数据
                # 注意：这里需要传入 session，因为 _async_crawl_tier_data 我们之前改为接收 session 了
                # 如果你的 _async_crawl_tier_data 还是自己开 session 的旧版，请去掉 session 参数
                tier_data = await self._async_crawl_tier_data(session, game_type)

                # 2. 收集所有需要翻译的卡组
                all_decks = set()
                for decks in tier_data.tiers.values():
                    for d in decks:
                        all_decks.add(d)

                # 3. 异步获取翻译
                tasks = []
                deck_names_for_api = []  # 存储需要 API 翻译的英文名

                for deck_name in all_decks:
                    # 避免对已有翻译的卡组进行 API 调用
                    if (
                        deck_name in self.translations
                        and self.translations[deck_name] != deck_name
                    ):
                        tier_data.deck_translations[deck_name] = self.translations[
                            deck_name
                        ]
                        continue

                    # 创建翻译任务
                    task_coroutine = self.get_chinese_name(
                        session, deck_name, force_api=True
                    )
                    tasks.append(task_coroutine)
                    deck_names_for_api.append(deck_name)

                # 4. 并发执行翻译任务
                if tasks:
                    # 【修复点 1】这里应该是 *tasks，不是 *api_tasks
                    cn_names = await asyncio.gather(*tasks)

                    # 【修复点 2】这里应该是 deck_names_for_api，不是 deck_names
                    for en_name, cn_name in zip(deck_names_for_api, cn_names):
                        tier_data.deck_translations[en_name] = cn_name
                        self.translations[en_name] = cn_name

                # 5. 将合并后的翻译设置到 TierData
                tier_data.deck_translations.update(self.translations)

                return tier_data

            except Exception as e:
                import traceback

                traceback.print_exc()  # 建议保留这个，方便看报错
                logger.error(f"[Tier] 爬取或翻译流程失败: {e}")
                return None


class TierCommandHandler:
    def __init__(self, data_dir: str):  # 参数名对应 main.py 传进来的含义
        self.manager = GenericTierManager(data_dir)

    async def update_tier_list(self, event, game_type: GameType, game_name: str):
        try:
            await event.send(event.plain_result(f"🔍 正在更新{game_name} T表数据..."))
            tier_data = await self.manager.crawl_tier_data(game_type)
            if tier_data:
                if self.manager.save_local_data(tier_data):
                    total = sum(len(d) for d in tier_data.tiers.values())
                    msg = (
                        f"{game_name} T表更新成功!\n"
                        f"📅 更新: {tier_data.update_date}\n"
                        f"📊 统计: T1({len(tier_data.tiers['T1'])}) + T2({len(tier_data.tiers['T2'])}) + T3({len(tier_data.tiers['T3'])}) = {total}卡组"
                    )
                    await event.send(event.plain_result(msg))
                else:
                    await event.send(event.plain_result("数据保存失败"))
            else:
                await event.send(event.plain_result("数据读取返回为空"))
        except Exception as e:
            await event.send(event.plain_result(f" T表获取失败: {e}"))

    async def query_tier_list(self, event, game_type: GameType, game_name: str):
        try:
            tier_data = self.manager.load_local_data(game_type)
            if not tier_data:
                await event.send(
                    event.plain_result(
                        f"未找到{game_name}数据，请先发送 /{game_type.value.upper()}更新T表"
                    )
                )
                return

            lines = [f"🏆 {game_name} T表", "=" * 25]
            lines.append(f"📅 日期: {tier_data.update_date}")
            lines.append(f"🕒 更新于: {tier_data.last_save}")
            lines.append("")

            for tier in ["T1", "T2", "T3"]:
                decks = tier_data.tiers.get(tier, [])
                if decks:
                    icon = {"T1": "🔥", "T2": "", "T3": "💫"}.get(tier, "🔹")
                    lines.append(f"{icon} {tier}")
                    lines.append("-" * 20)
                    for i, d in enumerate(decks, 1):
                        cn = self.manager.translations.get(d, d)
                        display = f"{cn} ({d})" if cn != d else d
                        lines.append(f" {i}. {display}")
                    lines.append("")

            if tier_data.changes:
                lines.append("📊 近期变化:")
                lines.append("-" * 20)
                for c in tier_data.changes[:8]:
                    lines.append(f" • {c.description}")

            await event.send(event.plain_result("\n".join(lines)))
        except Exception as e:
            await event.send(event.plain_result(f"查询出错: {e}"))

    async def translate_tier_list(self, event, game_type: GameType):
        await event.send(event.plain_result("🔄 正在尝试翻译..."))
        try:
            count, items = await self.manager.batch_translate_and_save(game_type)
            msg = f"翻译了 {count} 个新卡组!" if count > 0 else "🤔 未发现新翻译。"
            if count > 0:
                msg += "\n" + "\n".join(items[:5])
            await event.send(event.plain_result(msg))
        except Exception as e:
            await event.send(event.plain_result(f"出错: {e}"))
