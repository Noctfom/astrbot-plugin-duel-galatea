# -*- coding: utf-8 -*-
import aiohttp
import re
import html
import time
import json
import os
import asyncio
from astrbot.api.all import logger

class RotKManager:
    def __init__(self, data_dir: str): # 1. 参数名改为 data_dir
        self.data_dir = data_dir       # 2. 属性名改为 self.data_dir
        # 3. 下面使用 self.data_dir
        self.data_file = os.path.join(self.data_dir, "ocg_report_data.json")
        self.img_dir = os.path.join(self.data_dir, "ocg_images")
        
        if not os.path.exists(self.img_dir):
            os.makedirs(self.img_dir)
            
        self.base_url = "https://roadoftheking.com/"
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8'
        }

    def save_local_data(self, data):
        try:
            with open(self.data_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            return True
        except Exception as e:
            logger.error(f"[RotK] Save failed: {e}")
            return False

    def load_local_data(self):
        if not os.path.exists(self.data_file): return None
        try:
            with open(self.data_file, 'r', encoding='utf-8') as f: return json.load(f)
        except Exception: return None

    def _clear_image_cache(self):
        if os.path.exists(self.img_dir):
            for filename in os.listdir(self.img_dir):
                try: os.unlink(os.path.join(self.img_dir, filename))
                except: pass

    async def _fetch_html(self, session, url):
        try:
            async with session.get(url, timeout=20) as resp:
                if resp.status == 200: return await resp.text()
        except Exception as e:
            logger.error(f"[RotK] Fetch error {url}: {e}")
        return None

    async def _download_single_image(self, session, url, index):
        try:
            ext = url.split('.')[-1].split('?')[0]
            if len(ext) > 4 or '/' in ext: ext = "jpg"
            filename = f"ocg_img_{index}.{ext}"
            save_path = os.path.join(self.img_dir, filename)
            
            async with session.get(url, timeout=15) as resp:
                if resp.status == 200:
                    data = await resp.read()
                    with open(save_path, 'wb') as f: f.write(data)
                    return save_path
        except: pass
        return None

    async def _download_images(self, url_list):
        logger.info("[RotK] Clearing cache and downloading images...")
        self._clear_image_cache()
        local_paths = []
        async with aiohttp.ClientSession(headers=self.headers) as session:
            tasks = []
            for i, url in enumerate(url_list):
                tasks.append(self._download_single_image(session, url, i))
            results = await asyncio.gather(*tasks)
            local_paths = [p for p in results if p]
        return local_paths

    async def _fetch_article_content_images(self, session, article_url):
        """二级跳：精准提取正文图片"""
        logger.info(f"[RotK] Deep fetching: {article_url}")
        content = await self._fetch_html(session, article_url)
        if not content: return []

        # 1. 锁定正文区域 (entry-inner)
        start_idx = content.find('<div class="entry-inner">')
        if start_idx == -1: 
            # 备选: entry-content
            start_idx = content.find('class="entry-content')
        
        if start_idx == -1: return []

        # 2. 锁定结束区域 (关键修复！)
        # 必须在 "related-posts" 之前停止，否则会抓到底部的推荐缩略图
        end_idx = content.find('class="related-posts', start_idx)
        
        # 如果没找到 related-posts，尝试找 sharedaddy 或 comments
        if end_idx == -1: end_idx = content.find('class="sharedaddy"', start_idx)
        if end_idx == -1: end_idx = content.find('id="comments"', start_idx)
        if end_idx == -1: end_idx = len(content)

        # 截取纯净的正文
        clean_html = content[start_idx:end_idx]

        # 3. 提取图片
        raw_imgs = re.findall(r'src="([^"]+)"', clean_html)
        
        valid_imgs = []
        seen = set()
        
        ignore = ["gravatar", "icon", "logo", "facebook", "twitter", "share", "pixel", "emoji"]
        
        for img in raw_imgs:
            if "wp-content/uploads" not in img: continue
            if any(kw in img.lower() for kw in ignore): continue
            
            clean_url = html.unescape(img)
            
            # 【核心过滤】剔除缩略图
            # 缩略图特征：文件名以 -520x245.jpg, -150x150.jpg 结尾
            # 原图特征：image.jpg 或 image-scaled.jpg
            if re.search(r'-\d{3}x\d{2,3}\.', clean_url):
                logger.debug(f"[RotK] Skipping thumbnail: {clean_url}")
                continue

            if clean_url not in seen:
                valid_imgs.append(clean_url)
                seen.add(clean_url)
        
        logger.info(f"[RotK] Found {len(valid_imgs)} content images")
        return valid_imgs

    async def fetch_latest_report(self):
        logger.info(f"[RotK] Starting fetch from {self.base_url}")
        async with aiohttp.ClientSession(headers=self.headers) as session:
            content = await self._fetch_html(session, self.base_url)
            if not content: return {"error": "Main page fetch failed"}

            articles = content.split('<article')
            for raw_article in articles[1:]:
                title_match = re.search(r'entry-title.*?<a[^>]+>([^<]+)</a>', raw_article, re.DOTALL)
                if not title_match: continue
                
                raw_title = title_match.group(1).strip()
                title = html.unescape(raw_title)
                
                if "metagame report" not in title.lower(): continue

                link_match = re.search(r'href="([^"]+)"', title_match.group(0))
                url = link_match.group(1) if link_match else ""
                
                date_match = re.search(r'<time[^>]+>([^<]+)</time>', raw_article)
                date = date_match.group(1) if date_match else "Unknown"

                # 封面图 (Cover)
                cover_img = None
                cover_match = re.search(r'<img[^>]+src="([^"]+)"', raw_article)
                if cover_match: 
                    raw_cover = html.unescape(cover_match.group(1))
                    # 尝试还原封面高清图 (去掉 -520x245 等后缀)
                    cover_img = re.sub(r'-\d+x\d+\.(jpg|png|webp)$', r'.\1', raw_cover)

                logger.info(f"[RotK] Target: {title}")

                # 深层抓取 (Pie Chart + Decks)
                image_urls = []
                if cover_img: image_urls.append(cover_img)
                
                if url:
                    body_imgs = await self._fetch_article_content_images(session, url)
                    for img in body_imgs:
                        if img not in image_urls:
                            image_urls.append(img)

                # 下载
                local_paths = await self._download_images(image_urls)

                return {
                    "title": title,
                    "url": url,
                    "local_paths": local_paths,
                    "date": date,
                    "update_time": time.strftime("%Y-%m-%d %H:%M:%S")
                }
            
        return {"error": "No report found"}