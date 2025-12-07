# -*- coding: utf-8 -*-
import os
import json
import aiohttp
import asyncio
import re
import time
import html
from io import BytesIO
from typing import List, Dict, Optional, Tuple
import urllib.parse

try:
    from PIL import Image, ImageDraw, ImageFont

    HAS_PILLOW = True
except ImportError:
    HAS_PILLOW = False


class DeckBreakdownManager:
    def __init__(self, plugin_path: str):
        self.plugin_path = plugin_path
        self.deck_trans_file = os.path.join(plugin_path, "deck_translations.json")
        self.card_cache_file = os.path.join(plugin_path, "card_cache.json")
        self.images_dir = os.path.join(plugin_path, "temp_images")

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
            print(f"Save failed {path}: {e}")

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
            async with session.get(search_url, params=params, timeout=5) as response:
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

    async def _download_card_image(
        self, session: aiohttp.ClientSession, card_id: str
    ) -> Optional[Image.Image]:
        if not card_id:
            return None
        url = f"https://cdn.233.momobako.com/ygopro/pics/{card_id}.jpg!thumb2"
        try:
            async with session.get(url, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.read()
                    return Image.open(BytesIO(data))
        except:
            pass
        return None

    # --- 绘图逻辑 (智能字体加载) ---
    async def generate_full_deck_view(
        self,
        deck_name: str,
        main_deck_list: List[str],
        extra_deck_list: List[str],
        game_type_str: str,
        source_type: str = "Sample",
    ) -> Optional[str]:
        if not HAS_PILLOW:
            return None
        print(
            f"🎨 绘制: {deck_name} (Main:{len(main_deck_list)} Extra:{len(extra_deck_list)})"
        )

        unique_cards = set(main_deck_list + extra_deck_list)
        card_id_map = {}

        headers = {"User-Agent": "Mozilla/5.0"}
        async with aiohttp.ClientSession(headers=headers) as session:
            tasks = [self.get_card_info(session, c) for c in unique_cards]
            results = await asyncio.gather(*tasks)

            for c_name, res in zip(unique_cards, results):
                _, cid, _ = res
                if cid:
                    card_id_map[c_name] = cid

            download_tasks = []
            download_cids = []
            for c_name in unique_cards:
                cid = card_id_map.get(c_name)
                if cid:
                    download_tasks.append(self._download_card_image(session, cid))
                    download_cids.append(cid)

            images_data = await asyncio.gather(*download_tasks)

            images_cache = {}
            for cid, img in zip(download_cids, images_data):
                if img:
                    images_cache[cid] = img

        if not images_cache:
            return None

        try:
            card_w, card_h, gap, cols = 82, 120, 4, 10
            main_rows = (
                (len(main_deck_list) + cols - 1) // cols if main_deck_list else 0
            )
            extra_rows = (
                (len(extra_deck_list) + cols - 1) // cols if extra_deck_list else 0
            )
            header_h, section_gap = 40, 20
            total_h = (
                header_h
                + (main_rows * (card_h + gap))
                + section_gap
                + (extra_rows * (card_h + gap))
                + 20
            )
            total_w = max((card_w + gap) * cols + gap, 600)

            canvas = Image.new("RGB", (total_w, total_h), (25, 25, 30))
            draw = ImageDraw.Draw(canvas)

            # --- 智能字体加载逻辑 (支持 .otf, .ttf, .ttc) ---
            title_font = None
            count_font = None

            # 1. 扫描插件目录下所有的字体文件
            font_path = None
            valid_extensions = {".ttf", ".ttc", ".otf"}  # 支持 OTF

            # 优先查找列表
            priority_files = [
                "msyh.ttc",
                "msyh.ttf",
                "SourceHanSansSC-Regular.otf",
                "simhei.ttf",
            ]

            # 先找优先列表里的
            for f in priority_files:
                p = os.path.join(self.plugin_path, f)
                if os.path.exists(p):
                    font_path = p
                    break

            # 如果没找到，扫描整个目录找任意一个字体
            if not font_path:
                for filename in os.listdir(self.plugin_path):
                    if os.path.splitext(filename)[1].lower() in valid_extensions:
                        font_path = os.path.join(self.plugin_path, filename)
                        break

            # 加载字体
            if font_path:
                try:
                    title_font = ImageFont.truetype(font_path, 24)
                    count_font = ImageFont.truetype(font_path, 18)
                    print(f"✅ Loaded font: {os.path.basename(font_path)}")
                except Exception as e:
                    print(f"⚠️ Font load error: {e}")

            # 2. 兜底：如果还是没找到，使用默认
            if title_font is None:
                print("⚠️ No font found, using default (Chinese may fail)")
                title_font = ImageFont.load_default()
                count_font = ImageFont.load_default()
            # ------------------

            site_prefix = "DLM" if game_type_str == "dl" else "MDM"
            title_text = f"{site_prefix} {source_type}: {deck_name}"
            draw.text((10, 8), title_text, font=title_font, fill=(255, 255, 255))
            draw.text(
                (total_w - 200, 12),
                f"Main:{len(main_deck_list)} / Extra:{len(extra_deck_list)}",
                font=count_font,
                fill=(200, 200, 200),
            )

            def draw_section(card_list, start_y):
                for i, c_name in enumerate(card_list):
                    cid = card_id_map.get(c_name)
                    if cid and cid in images_cache:
                        row, col = i // cols, i % cols
                        x = gap + col * (card_w + gap)
                        y = start_y + row * (card_h + gap)
                        canvas.paste(images_cache[cid], (x, y))
                return start_y + ((len(card_list) + cols - 1) // cols) * (card_h + gap)

            next_y = draw_section(main_deck_list, header_h)
            draw.line(
                [
                    (gap, next_y + section_gap / 2),
                    (total_w - gap, next_y + section_gap / 2),
                ],
                fill=(60, 60, 60),
                width=2,
            )
            draw_section(extra_deck_list, next_y + section_gap)

            output_filename = f"{deck_name}_{int(time.time())}.jpg"
            output_path = os.path.join(self.images_dir, output_filename)
            canvas.save(output_path, quality=90)
            return os.path.abspath(output_path)
        except Exception as e:
            print(f"Draw error: {e}")
            return None

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

    async def fetch_deck_breakdown(self, query_name: str, game_type_input) -> Dict:
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

            async with aiohttp.ClientSession(headers=headers) as session:
                # 1. 抓取文字版主页 (为了获取 Sample Deck 的位置和 skill)
                # URL 必须手动编码
                page_url = f"https://{domain}/tier-list/deck-types/{urllib.parse.quote(deck_slug)}"
                print(f"Fetching Page: {page_url}")

                async with session.get(page_url, timeout=15) as resp:
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

                # --- 2. 图片生成 ---
                image_path = None

                # A. API (Top Decks)
                # 尝试多种编码方式，直到成功
                slug_variants = [
                    deck_slug,
                    urllib.parse.quote(deck_slug),
                    deck_slug.replace(" ", "-"),
                ]
                api_base = f"https://{domain}/api/v1/top-decks"

                for variant in slug_variants:
                    if image_path:
                        break
                    # 直接拼接 URL，防止 requests/aiohttp 的 params 自动编码导致 double encoding
                    api_target = f"{api_base}?deckType={variant}&pageSize=1&sort=date"

                    try:
                        print(f"🔍 API Try: {api_target}")
                        async with session.get(api_target, timeout=10) as api_resp:
                            if api_resp.status == 200:
                                data = await api_resp.json()
                                if data and len(data) > 0:
                                    m, e = self._extract_cards_from_api_obj(data[0])
                                    if m:
                                        image_path = await self.generate_full_deck_view(
                                            display_name,
                                            m,
                                            e,
                                            game_type_str,
                                            "最新上位(API)",
                                        )
                                        author = (
                                            data[0]
                                            .get("author", {})
                                            .get("username", "Unknown")
                                        )
                                        text_msg += (
                                            f"\n\n📜 来源: 最新上位 ({author}) [API]"
                                        )
                    except Exception as ex:
                        debug_msg.append(f"API Error: {ex}")

                # B. 原地 HTML 解析 (如果 API 失败)
                if not image_path and sample_idx != -1:
                    sample_area = content[sample_idx:]
                    m, e = self._parse_html_sample(sample_area)
                    if len(m) > 10:
                        image_path = await self.generate_full_deck_view(
                            display_name, m, e, game_type_str, "页面示例"
                        )
                        text_msg += "\n\n📜 来源: 页面示例 (Sample Deck)"
                    else:
                        debug_msg.append(f"Local parse < 10 cards (got {len(m)})")

                # C. 兜底
                if not image_path and core_unique_cards:
                    debug_msg.append("Fallback Core")
                    fb_main, fb_extra = [], []
                    # 异步获取类型信息进行分拣
                    tasks = [self.get_card_info(session, c) for c in core_unique_cards]
                    infos = await asyncio.gather(*tasks)

                    for c, info in zip(core_unique_cards, infos):
                        _, _, is_e = info
                        if is_e:
                            fb_extra.append(c)
                        else:
                            fb_main.append(c)

                    image_path = await self.generate_full_deck_view(
                        display_name,
                        fb_main,
                        fb_extra,
                        game_type_str,
                        "核心统计(无复数)",
                    )
                    text_msg += "\n\n🖼️ 图片来源: 核心统计兜底"

                if not image_path:
                    text_msg += f"\n\n⚠️ 未生成图片 [Debug: {'; '.join(debug_msg)}]"

                return {"text": text_msg, "image_path": image_path}

        except Exception as e:
            return {"text": f"Error: {str(e)}"}
