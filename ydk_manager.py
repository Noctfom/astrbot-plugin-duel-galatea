# -*- coding: utf-8 -*-
import os
import aiohttp
import asyncio
import time
from io import BytesIO
from typing import List, Tuple, Optional, Dict
from astrbot.api.all import logger
import urllib.parse
import base64
import struct
import random

try:
    from PIL import Image, ImageDraw, ImageFont
    HAS_PILLOW = True
except ImportError:
    HAS_PILLOW = False
    logger.warning("YDKManager: Pillow not installed.")

class YDKManager:
    def __init__(self, data_dir: str, plugin_dir: str):
        self.data_dir = data_dir
        self.plugin_dir = plugin_dir
        self.images_dir = os.path.join(data_dir, "temp_images")
        self.cache_dir = os.path.join(data_dir, "deck_cache") # 新增：专门存放ydk的文件夹

        if not os.path.exists(self.images_dir):
            os.makedirs(self.images_dir)
        if not os.path.exists(self.cache_dir): # 确保缓存目录存在
            os.makedirs(self.cache_dir)
            
    def parse_ydk(self, text: str) -> Tuple[List[str], List[str], List[str]]:
        """解析 YDK 文本内容为 ID 列表"""
        main, extra, side = [], [], []
        mode = "none"
        
        for line in text.splitlines():
            line = line.strip()
            if not line: continue
            if line.startswith("#main"):
                mode = "main"
                continue
            elif line.startswith("#extra"):
                mode = "extra"
                continue
            elif line.startswith("!side"):
                mode = "side"
                continue
            elif line.startswith("#"):
                continue
                
            if line.isdigit():
                if mode == "main": main.append(line)
                elif mode == "extra": extra.append(line)
                elif mode == "side": side.append(line)
                
        return main, extra, side
    
    def parse_ourocg_url(self, url: str) -> Tuple[List[str], List[str], List[str]]:
        """
        解析 Ourocg 卡组分享链接
        返回: (main_list, extra_list, side_list)
        """
        try:
            # 1. 解析 URL 参数
            parsed = urllib.parse.urlparse(url)
            params = urllib.parse.parse_qs(parsed.query)
            
            # 获取 d 参数 (加密数据)
            d_param = params.get('d', [''])[0]
            if not d_param:
                logger.error("URL missing 'd' parameter")
                return [], [], []

            # 2. 调用核心解码逻辑
            deck_data = self._decode_ourocg_data(d_param)
            
            return deck_data["main"], deck_data["extra"], deck_data["side"]

        except Exception as e:
            logger.error(f"Ourocg Parse Error: {e}")
            return [], [], []
    
    def _decode_ourocg_data(self, encoded_str: str) -> Dict[str, List[str]]:
        """
        核心解码逻辑 (Ourocg V1 算法)
        基于 JS 源码逆向：Base64 -> Binary String -> 29-bit chunks
        修正：ID = Low 27 bits, Count = High 2 bits
        """
        main_ids, extra_ids, side_ids = [], [], []
        
        try:
            # 1. URL Safe Base64 还原
            safe_str = encoded_str.replace('-', '+').replace('_', '/')
            
            # 补全 Padding (=)
            padding = len(safe_str) % 4
            if padding:
                safe_str += '=' * (4 - padding)
            
            # 2. Base64 解码为字节
            byte_data = base64.b64decode(safe_str)
            
            # 3. 转为二进制字符串 (每个字节转8位01)
            bin_str = "".join([format(b, '08b') for b in byte_data])
            
            # 4. 解析头部
            # Main(8 bit) + Extra(4 bit) + Side(4 bit) = 16 bits
            if len(bin_str) < 16:
                return {"main": [], "extra": [], "side": []}
                
            main_count = int(bin_str[0:8], 2)
            extra_count = int(bin_str[8:12], 2)
            side_count = int(bin_str[12:16], 2)
            
            offset = 16
            
            # 辅助解析函数
            def parse_section(count):
                nonlocal offset
                ids = []
                for _ in range(count):
                    if offset + 29 > len(bin_str): break
                    
                    # 截取 29 位
                    chunk = bin_str[offset : offset + 29]
                    offset += 29
                    
                    val = int(chunk, 2)
                    
                    # [关键修正]
                    # Ourocg V1: 前2位是数量，后27位是ID
                    card_count = val >> 27
                    card_id = str(val & 0x7FFFFFF)
                    
                    # 添加 card_count 次 ID
                    for _ in range(card_count):
                        ids.append(card_id)
                return ids

            # 5. 依次解析三个区域
            main_ids = parse_section(main_count)
            extra_ids = parse_section(extra_count)
            side_ids = parse_section(side_count)
            
            logger.info(f"Ourocg Decode: M:{len(main_ids)} E:{len(extra_ids)} S:{len(side_ids)}")
            
        except Exception as e:
            logger.error(f"Ourocg Decode Failed: {e}")
            
        return {"main": main_ids, "extra": extra_ids, "side": side_ids}
            
    
    def parse_ydke_url(self, url: str) -> Tuple[List[str], List[str], List[str]]:
        """
        解析 YDKe 链接 (ydke://...)
        格式: ydke://Base64(Main)!Base64(Extra)!Base64(Side)!
        """
        try:
            # 去掉前缀
            clean_url = url.replace("ydke://", "").strip()
            
            # 按 ! 分割
            parts = clean_url.split('!')
            
            # YDKe 标准通常有3个部分，最后可能有个空字符串
            # Main ! Extra ! Side !
            main_str = parts[0] if len(parts) > 0 else ""
            extra_str = parts[1] if len(parts) > 1 else ""
            side_str = parts[2] if len(parts) > 2 else ""
            
            main_ids = self._decode_ydke_ids(main_str)
            extra_ids = self._decode_ydke_ids(extra_str)
            side_ids = self._decode_ydke_ids(side_str)
            
            logger.info(f"YDKe Decode: M:{len(main_ids)} E:{len(extra_ids)} S:{len(side_ids)}")
            return main_ids, extra_ids, side_ids
            
        except Exception as e:
            logger.error(f"YDKe Parse Error: {e}")
            return [], [], []

    def _decode_ydke_ids(self, b64_str: str) -> List[str]:
        """
        YDKe 核心解码: Base64 -> Bytes -> Int32 (Little Endian)
        """
        if not b64_str: return []
        ids = []
        try:
            # 1. 补全 Base64 Padding
            padding = len(b64_str) % 4
            if padding:
                b64_str += '=' * (4 - padding)
            
            # 2. 解码为字节
            byte_data = base64.b64decode(b64_str)
            
            # 3. 每4个字节转为一个整数 (ID)
            count = len(byte_data) // 4
            # '<' 表示小端序，'I' 表示无符号整数 (4 bytes)
            # unpack 返回的是元组，所以需要解包或者用 iter_unpack
            for i in range(count):
                chunk = byte_data[i*4 : (i+1)*4]
                card_id = struct.unpack('<I', chunk)[0]
                ids.append(str(card_id))
                
        except Exception as e:
            logger.error(f"YDKe Chunk Decode Error: {e}")
            
        return ids
    
    def save_ydk(self, main: List[str], extra: List[str], side: List[str], session_id: str) -> str:
        """保存 YDK (区分会话)"""
        self._cleanup_old_files() # 顺手清理过期文件
        
        content = ["#created by DuelGalatea", "#main"]
        content.extend(main)
        content.append("#extra")
        content.extend(extra)
        content.append("!side")
        content.extend(side)
        content.append("")
        
        # 文件名带上 session_id
        file_path = os.path.join(self.cache_dir, f"deck_{session_id}.ydk")
        
        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write("\n".join(content))
            return file_path
        except Exception as e:
            logger.error(f"YDK Save Error: {e}")
            return ""

    def load_last_ydk(self, session_id: str) -> Tuple[List[str], List[str], List[str]]:
        """读取指定会话的 YDK"""
        file_path = os.path.join(self.cache_dir, f"deck_{session_id}.ydk")
        
        if not os.path.exists(file_path):
            return [], [], []
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                return self.parse_ydk(f.read())
        except Exception as e:
            logger.error(f"YDK Load Error: {e}")
            return [], [], []

    async def _download_image(self, session: aiohttp.ClientSession, card_id: str) -> Optional[Image.Image]:
        """按 ID 下载图片"""
        url = f"https://cdn.233.momobako.com/ygopro/pics/{card_id}.jpg!thumb2"
        try:
            async with session.get(url, timeout=10, ssl=False) as resp:
                if resp.status == 200:
                    data = await resp.read()
                    return Image.open(BytesIO(data))
        except: pass
        return None

    async def draw_deck_image(self, session_id: str, deck_name: str = "YDK Deck") -> Optional[str]:
        """根据当前缓存的 YDK 绘制图片 (异步非阻塞版)"""
        if not HAS_PILLOW: return None
        
        main, extra, side = self.load_last_ydk(session_id)
        if not main and not extra: return None

        logger.info(f"🎨 Drawing YDK: Main({len(main)}) Extra({len(extra)}) Side({len(side)})")

        # 1. 异步下载图片 (IO 密集型，保持在主循环)
        images_cache = {} # 格式: { "card_id": ImageObject }
        unique_ids = set(main + extra + side)
        
        async with aiohttp.ClientSession(trust_env=True) as session:
            tasks = []
            id_list = list(unique_ids)
            for cid in id_list:
                tasks.append(self._download_image(session, cid))
            
            results = await asyncio.gather(*tasks)
            for cid, img in zip(id_list, results):
                if img: 
                    images_cache[cid] = img

        if not images_cache:
            return None

        # 2. 将绘图逻辑放入线程池 (CPU 密集型)
        loop = asyncio.get_running_loop()
        
        # run_in_executor(None, 函数, 参数1, 参数2...)
        # None 表示使用默认的 ThreadPoolExecutor
        output_path = await loop.run_in_executor(
            None, 
            self._sync_draw_logic, # 刚才那个新函数
            deck_name, 
            main, 
            extra, 
            side, 
            images_cache
        )
        
        return output_path
    
    def _sync_draw_logic(self, deck_name: str, main: List[str], extra: List[str], side: List[str], images_cache: dict) -> Optional[str]:
        """
        [同步方法] 纯 CPU 密集的绘图逻辑，供 run_in_executor 调用
        """
        try:
            # --- 以下代码完全来自原来的 draw_deck_image 后半部分 ---
            card_w, card_h, gap, cols = 82, 120, 4, 10
            # 计算高度
            main_rows = (len(main) + cols - 1) // cols if main else 0
            extra_rows = (len(extra) + cols - 1) // cols if extra else 0
            side_rows = (len(side) + cols - 1) // cols if side else 0
            
            header_h, section_gap = 40, 20
            
            total_h = header_h + (main_rows * (card_h + gap))
            if extra: total_h += section_gap + (extra_rows * (card_h + gap))
            if side: total_h += section_gap + (side_rows * (card_h + gap))
            total_h += 20 # Padding
            
            total_w = max((card_w + gap) * cols + gap, 600)

            canvas = Image.new("RGB", (total_w, total_h), (25, 25, 30))
            draw = ImageDraw.Draw(canvas)

            # 加载字体
            font = self._load_font()
            
            # 绘制标题
            draw.text((10, 8), f"Deck: {deck_name}", font=font, fill=(255, 255, 255))
            draw.text((total_w - 200, 12), f"M:{len(main)} E:{len(extra)} S:{len(side)}", font=font, fill=(200, 200, 200))

            current_y = header_h
            
            def draw_section(ids_list, start_y):
                for i, cid in enumerate(ids_list):
                    if cid in images_cache:
                        row, col = i // cols, i % cols
                        x = gap + col * (card_w + gap)
                        y = start_y + row * (card_h + gap)
                        canvas.paste(images_cache[cid], (x, y))
                rows = (len(ids_list) + cols - 1) // cols if ids_list else 0
                return start_y + rows * (card_h + gap)

            # Main
            if main:
                current_y = draw_section(main, current_y)
            
            # Extra
            if extra:
                current_y += section_gap
                draw.line([(gap, current_y - section_gap/2), (total_w-gap, current_y - section_gap/2)], fill=(60,60,60), width=2)
                current_y = draw_section(extra, current_y)

            # Side
            if side:
                current_y += section_gap
                draw.line([(gap, current_y - section_gap/2), (total_w-gap, current_y - section_gap/2)], fill=(60,60,60), width=2)
                draw.text((gap, current_y - section_gap + 2), "!Side Deck", font=font, fill=(200, 200, 200))
                current_y = draw_section(side, current_y)

            output_path = os.path.join(self.images_dir, f"deck_{int(time.time())}.jpg")
            canvas.save(output_path, quality=90)
            return output_path

        except Exception as e:
            logger.error(f"Draw Logic Error: {e}")
            return None

    def _load_font(self):
        """简单的字体加载封装"""
        valid_extensions = {".ttf", ".ttc", ".otf"}
        priority_files = ["msyh.ttc", "msyh.ttf", "simhei.ttf"]
        
        font_path = None
        for f in priority_files:
            p = os.path.join(self.plugin_dir, f)
            if os.path.exists(p):
                font_path = p
                break
        
        if not font_path:
             for filename in os.listdir(self.plugin_dir):
                if os.path.splitext(filename)[1].lower() in valid_extensions:
                    font_path = os.path.join(self.plugin_dir, filename)
                    break
        
        if font_path:
            try:
                return ImageFont.truetype(font_path, 24)
            except: pass
        return ImageFont.load_default()
    
    def _cleanup_old_files(self):
        """清理超过 24 小时的缓存文件"""
        now = time.time()
        expiration = 24 * 60 * 60 # 24小时
        
        # 清理 YDK
        for f in os.listdir(self.cache_dir):
            path = os.path.join(self.cache_dir, f)
            if os.path.isfile(path) and (now - os.path.getmtime(path) > expiration):
                try: os.remove(path)
                except: pass
                
        # 顺便清理一下图片缓存
        for f in os.listdir(self.images_dir):
            path = os.path.join(self.images_dir, f)
            if os.path.isfile(path) and (now - os.path.getmtime(path) > expiration):
                try: os.remove(path)
                except: pass

    # [新增功能] 复制 YDK 文件 (用于卡组转存)
    def copy_ydk_from_session(self, src_session_id: str, target_session_id: str) -> bool:
        """将源会话的 YDK 复制给目标会话"""
        src_path = os.path.join(self.cache_dir, f"deck_{src_session_id}.ydk")
        target_path = os.path.join(self.cache_dir, f"deck_{target_session_id}.ydk")

        if not os.path.exists(src_path):
            return False
        
        try:
            # 读取源文件
            with open(src_path, 'r', encoding='utf-8') as f:
                content = f.read()
            # 写入目标文件
            with open(target_path, 'w', encoding='utf-8') as f:
                f.write(content)
            return True
        except Exception as e:
            logger.error(f"Copy YDK Error: {e}")
            return False

    # [新增功能] 通用卡片绘图 (用于起手/抽卡展示)
    async def draw_cards_image(self, card_ids: List[str], title: str = "Cards") -> Optional[str]:
        """绘制指定的一组卡片 ID"""
        if not HAS_PILLOW or not card_ids: return None

        # 1. 下载图片
        images_cache = {}
        unique_ids = list(set(card_ids))
        
        async with aiohttp.ClientSession(trust_env=True) as session:
            tasks = [self._download_image(session, cid) for cid in unique_ids]
            results = await asyncio.gather(*tasks)
            for cid, img in zip(unique_ids, results):
                if img: images_cache[cid] = img

        if not images_cache: return None

        # 2. 同步绘图逻辑
        def _sync_draw():
            try:
                card_w, card_h, gap = 82, 120, 6
                # 动态计算画布宽度：最多一行 10 张，或者根据卡片数量自适应
                cols = min(len(card_ids), 10)
                rows = (len(card_ids) + cols - 1) // cols
                
                total_w = (card_w + gap) * cols + gap
                total_h = (card_h + gap) * rows + gap + 30 # +30 for title
                
                # 黑色背景
                canvas = Image.new("RGB", (total_w, total_h), (20, 20, 20))
                draw = ImageDraw.Draw(canvas)
                
                # 绘制标题
                font = self._load_font()
                draw.text((gap, 5), title, font=font, fill=(255, 255, 255))
                
                start_y = 30
                for i, cid in enumerate(card_ids):
                    if cid in images_cache:
                        r = i // cols
                        c = i % cols
                        x = gap + c * (card_w + gap)
                        y = start_y + gap + r * (card_h + gap)
                        canvas.paste(images_cache[cid], (x, y))
                
                output_path = os.path.join(self.images_dir, f"draw_{int(time.time())}_{random.randint(100,999)}.jpg")
                canvas.save(output_path, quality=90)
                return output_path
            except Exception as e:
                logger.error(f"Draw Cards Error: {e}")
                return None

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _sync_draw)