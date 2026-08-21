# -*- coding: utf-8 -*-
import os
import json
import aiohttp
import asyncio
import re
import html
from typing import List, Dict, Tuple
import urllib.parse
from astrbot.api.all import logger


class DeckBreakdownManager:
    # 新增 ydk_manager 参数
    def __init__(self, data_dir: str, plugin_dir: str, ydk_manager):
        self.data_dir = data_dir
        self.plugin_dir = plugin_dir
        self.ydk_manager = ydk_manager  # 保存实例

        # 2. 下面的文件全部改用 self.data_dir
        self.deck_trans_file = os.path.join(self.data_dir, "deck_translations.json")
        self.card_cache_file = os.path.join(self.data_dir, "card_cache.json")
        self.images_dir = os.path.join(self.data_dir, "temp_images")

        if not os.path.exists(self.images_dir):
            os.makedirs(self.images_dir)

        self.deck_translations = self._load_json(self.deck_trans_file)
        self.card_cache = self._load_json(self.card_cache_file)

    def _load_json(self, path: str) -> Dict:
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except:
                return {}
        return {}

    def _save_json(self, path: str, data: Dict):
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Save failed {path}: {e}")

    async def get_card_info(
        self, session: aiohttp.ClientSession, english_name: str
    ) -> Tuple[str, str, bool]:
        clean_name = html.unescape(english_name).strip()
        if not clean_name:
            return clean_name, "", False

        if clean_name in self.card_cache:
            info = self.card_cache[clean_name]
            if isinstance(info, str):
                return info, "", False
            return (
                info.get("cn", clean_name),
                info.get("id", ""),
                info.get("is_extra", False),
            )

        try:
            search_url = "https://ygocdb.com/api/v0/"
            params = {"search": clean_name}
            async with session.get(search_url, params=params, timeout=5, ssl=False) as response:
                if response.status == 200:
                    data = await response.json()
                    if data.get("result"):
                        best = data["result"][0]
                        for item in data["result"]:
                            if item.get("en_name", "").lower() == clean_name.lower():
                                best = item
                                break

                        cn_name = best.get("cn_name", clean_name)
                        card_id = str(best.get("id", ""))

                        types = ""
                        if "text" in best and "types" in best["text"]:
                            types = best["text"]["types"]
                        elif "type" in best:
                            types = str(best["type"])

                        is_extra = any(
                            x in types
                            for x in [
                                "Link",
                                "Fusion",
                                "Synchro",
                                "XYZ",
                                "Xyz",
                                "连接",
                                "融合",
                                "同调",
                                "超量",
                            ]
                        )

                        self.card_cache[clean_name] = {
                            "cn": cn_name,
                            "id": card_id,
                            "is_extra": is_extra,
                        }
                        self._save_json(self.card_cache_file, self.card_cache)
                        return cn_name, card_id, is_extra
            return clean_name, "", False
        except:
            return clean_name, "", False

    def resolve_deck_slug(self, query: str) -> Tuple[str, str]:
        query_lower = query.lower()
        for en, cn in self.deck_translations.items():
            if en.lower() == query_lower:
                return en, f"{cn} ({en})"
        for en, cn in self.deck_translations.items():
            if cn == query:
                return en, f"{cn} ({en})"
            if query in cn:
                return en, f"{cn} ({en})"
        return query, query

    def _extract_cards_from_api_obj(
        self, deck_obj: Dict
    ) -> Tuple[List[str], List[str]]:
        m_list, e_list = [], []
        # 兼容 main/mainDeck 写法
        src_m = deck_obj.get("main") or deck_obj.get("mainDeck") or []
        for item in src_m:
            name = item.get("card", {}).get("name") or item.get("name")
            qty = item.get("amount", 1)
            if name:
                for _ in range(qty):
                    m_list.append(html.unescape(name))

        src_e = deck_obj.get("extra") or deck_obj.get("extraDeck") or []
        for item in src_e:
            name = item.get("card", {}).get("name") or item.get("name")
            qty = item.get("amount", 1)
            if name:
                for _ in range(qty):
                    e_list.append(html.unescape(name))
        return m_list, e_list

    def _parse_html_sample(self, html_content: str) -> Tuple[List[str], List[str]]:
        """HTML 原地解析 (暴力 box-container)"""
        main_list, extra_list = [], []

        # 1. 切分 Box
        # 注意：源码里是 <div class="box-container ...">
        box_starts = [
            m.start() for m in re.finditer(r'class="[^"]*box-container', html_content)
        ]
        if len(box_starts) < 1:
            return [], []

        def parse_chunk(chunk):
            cards = []
            # 2. 切分 Card
            # 注意：源码里是 <div class="card-container ...">
            card_starts = [
                m.start() for m in re.finditer(r'class="[^"]*card-container', chunk)
            ]

            for i, start in enumerate(card_starts):
                end = card_starts[i + 1] if i < len(card_starts) - 1 else len(chunk)
                snippet = chunk[start:end]

                # 3. 提取数量 (如果存在)
                qty = 1
                q_match = re.search(r'alt="(\d+)\s*copies"', snippet)
                if q_match:
                    qty = int(q_match.group(1))

                # 4. 提取名字
                # 排除垃圾 Alt
                ignore = [
                    "copies",
                    "Rarity",
                    "Limited",
                    "gem-icon",
                    "Master Duel",
                    "Duel Links",
                    "object Object",
                    "placeholder",
                    "Avatar",
                    "Skill",
                ]

                alt_matches = re.findall(r'alt="([^"]+)"', snippet)
                for alt in alt_matches:
                    if any(x in alt for x in ignore):
                        continue

                    # 找到了有效名字
                    name = html.unescape(alt)
                    for _ in range(qty):
                        cards.append(name)
                    break
            return cards

        # 提取 Main (假设是第一个 Box)
        # 限制范围：到下一个 Box 或者 Side Deck
        main_end = box_starts[1] if len(box_starts) > 1 else len(html_content)
        main_list = parse_chunk(html_content[box_starts[0] : main_end])

        # 提取 Extra (假设是第二个 Box)
        if len(box_starts) > 1:
            extra_start = box_starts[1]
            extra_end = box_starts[2] if len(box_starts) > 2 else len(html_content)
            # 过滤 Side Deck
            side_idx = html_content.find("Side Deck", extra_start)
            if side_idx != -1 and side_idx < extra_end:
                extra_end = side_idx

            extra_list = parse_chunk(html_content[extra_start:extra_end])

        return main_list, extra_list

    async def fetch_deck_breakdown(
        self, query_name: str, game_type_input, session_id: str
    ) -> Dict:
        is_dl = False
        if hasattr(game_type_input, "value"):
            is_dl = game_type_input.value == "dl"
        elif str(game_type_input).lower() == "dl":
            is_dl = True

        game_type_str = "dl" if is_dl else "md"
        domain = "www.duellinksmeta.com" if is_dl else "www.masterduelmeta.com"

        deck_slug, display_name = self.resolve_deck_slug(query_name)

        debug_msg = []
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            }

            async with aiohttp.ClientSession(trust_env=True, headers=headers) as session:
                # 1. 抓取文字版主页 (为了获取 Sample Deck 的位置和 skill)
                # URL 必须手动编码
                page_url = f"https://{domain}/tier-list/deck-types/{urllib.parse.quote(deck_slug)}"
                logger.info(f"Fetching Page: {page_url}")

                async with session.get(page_url, timeout=15, ssl=False) as resp:
                    if resp.status == 404:
                        return {"text": f"❌ 未找到卡组: {deck_slug}"}
                    content = await resp.text()

                # --- 文字提取 ---
                top_main_start = content.find("Top Main Deck")
                if top_main_start == -1:
                    top_main_start = content.find("<b>Top Main Deck</b>")

                sample_idx = content.find("Sample Deck")
                if sample_idx == -1:
                    sample_idx = content.find("Recent Decks")
                if sample_idx == -1:
                    sample_idx = content.find('class="deck-container"')

                stats_end = sample_idx if sample_idx != -1 else len(content)

                text_msg = f"📚 {display_name} 核心配置\n{'=' * 20}\n"
                core_unique_cards = []

                if top_main_start != -1:
                    snippet = content[top_main_start:stats_end]
                    uniques = []
                    matches = re.findall(r'alt="([^"]+)"', snippet)
                    ignore = [
                        "Rarity",
                        "Limited",
                        "gem-icon",
                        "Master Duel",
                        "Duel Links",
                        "object Object",
                        "Sample Deck",
                        "Skill",
                    ]
                    for raw in matches:
                        if any(x in raw for x in ignore):
                            continue
                        n = html.unescape(raw)
                        if n and n not in uniques:
                            uniques.append(n)

                    core_unique_cards = uniques
                    if uniques:
                        text_msg += "🔹 [热门投入]\n"
                        # 这里的 get_card_info 只是为了显示中文名，为了速度可以不 await 或者并发
                        # 简单起见，这里我们并发获取前10张
                        tasks = [self.get_card_info(session, c) for c in uniques[:10]]
                        infos = await asyncio.gather(*tasks)
                        for i, info in enumerate(infos):
                            text_msg += f"{i + 1}. {info[0]}\n"

                if is_dl and sample_idx != -1:
                    skill_match = re.search(
                        r'href="/skills/([^"]+)"', content[sample_idx:]
                    )
                    if skill_match:
                        skill_name = urllib.parse.unquote(skill_match.group(1))
                        text_msg += f"\n✨ 技能: {skill_name}"

                text_msg += f"\n🔗 {domain}页面: {page_url}"

                # --- 2. 核心抓取逻辑 (目标：获取 m_list 和 e_list) ---
                m_list, e_list = [], []
                source_info = ""  # 用于记录来源信息

                # A. API (Top Decks) 优先尝试
                slug_variants = [
                    deck_slug,
                    urllib.parse.quote(deck_slug),
                    deck_slug.replace(" ", "-"),
                ]
                api_base = f"https://{domain}/api/v1/top-decks"

                for variant in slug_variants:
                    if m_list or e_list:
                        break  # 如果已经抓到了，就跳出

                    api_target = f"{api_base}?deckType={variant}&pageSize=1&sort=date"
                    try:
                        logger.info(f"DeckBreakdown: API Try: {api_target}")
                        async with session.get(api_target, timeout=10, ssl=False) as api_resp:
                            if api_resp.status == 200:
                                data = await api_resp.json()
                                if data and len(data) > 0:
                                    m_list, e_list = self._extract_cards_from_api_obj(
                                        data[0]
                                    )
                                    if m_list:
                                        author = (
                                            data[0]
                                            .get("author", {})
                                            .get("username", "Unknown")
                                        )
                                        source_info = f"最新上位 ({author}) [API]"
                    except Exception as ex:
                        debug_msg.append(f"API Error: {ex}")

                # B. 原地 HTML 解析 (如果 API 失败)
                if (not m_list and not e_list) and sample_idx != -1:
                    sample_area = content[sample_idx:]
                    m_list, e_list = self._parse_html_sample(sample_area)
                    if len(m_list) > 10:
                        source_info = "页面示例 (Sample Deck)"
                    else:
                        # 抓取失败或数量太少，视为无效
                        m_list, e_list = [], []
                        debug_msg.append("Local parse < 10 cards")

                # C. 兜底 (使用核心卡作为参考)
                if (not m_list and not e_list) and core_unique_cards:
                    debug_msg.append("Fallback Core")
                    source_info = "核心统计(无复数)"

                    # 异步获取类型信息进行分拣
                    tasks = [self.get_card_info(session, c) for c in core_unique_cards]
                    infos = await asyncio.gather(*tasks)

                    for c, info in zip(core_unique_cards, infos):
                        _, _, is_e = info
                        if is_e:
                            e_list.append(c)
                        else:
                            m_list.append(c)

                # --- 3. 后处理：转 ID -> 保存 YDK -> 绘图 ---

                # 如果依然为空，说明彻底失败
                if not m_list and not e_list:
                    text_msg += (
                        f"\n\n❌ 未找到有效卡组配置 [Debug: {'; '.join(debug_msg)}]"
                    )
                    return {"text": text_msg, "image_path": None}

                text_msg += f"\n\n📜 来源: {source_info}"
                text_msg += "\n🔄 正在转换卡密并生成文件..."

                # 3.1 卡名 -> ID 转换
                unique_names = list(set(m_list + e_list))
                tasks = [self.get_card_info(session, name) for name in unique_names]
                results = await asyncio.gather(*tasks)

                # ... (前面的 gathering results 不变) ...

                name_to_id = {}
                id_to_is_extra = {}  # 新增：记录 ID 是否属于额外卡组

                for name, res in zip(unique_names, results):
                    # res: (cn_name, card_id, is_extra)
                    if res[1]:
                        name_to_id[name] = res[1]
                        id_to_is_extra[res[1]] = res[2]  # 记录是否为额外

                # 1. 先把所有识别出来的 ID 混在一起
                raw_m_ids = [name_to_id.get(n) for n in m_list if name_to_id.get(n)]
                raw_e_ids = [name_to_id.get(n) for n in e_list if name_to_id.get(n)]
                all_ids = raw_m_ids + raw_e_ids

                # 2. 重新分配 (二次清洗)
                m_ids = []
                e_ids = []

                for cid in all_ids:
                    # 如果 API 说是额外(is_extra=True)，就强制塞进额外，不管它原来在哪
                    if id_to_is_extra.get(cid, False):
                        e_ids.append(cid)
                    else:
                        m_ids.append(cid)
                # 3.2 保存 YDK
                ydk_path = self.ydk_manager.save_ydk(m_ids, e_ids, [], session_id)

                # 3.3 绘图
                if ydk_path:
                    text_msg += "\n🎨 正在绘制预览图..."
                    image_path = await self.ydk_manager.draw_deck_image(
                        session_id, display_name
                    )
                else:
                    text_msg += "\n⚠️ YDK 文件生成失败"

                return {
                    "text": text_msg,
                    "image_path": image_path,
                    "ydk_path": ydk_path,
                }

        except Exception as e:
            logger.error(f"DeckBreakdown Error: {e}")  # 新增日志
            return {"text": f"Error: {str(e)}"}
