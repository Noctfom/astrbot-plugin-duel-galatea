# -*- coding: utf-8 -*-
import os
import json
import aiohttp
import asyncio
import math
import re # 新增正则
from typing import Dict, List, Tuple, Optional, Any
from aiohttp import TCPConnector 
from astrbot.api.all import logger


class BanlistManager:
    def __init__(self, data_dir: str):
        self.data_dir = data_dir
        self.banlist_cache_file = os.path.join(self.data_dir, "banlist_cache.json")
        self.name_map_file = os.path.join(self.data_dir, "banlist_name_map.json")
        self.genesys_file = os.path.join(self.data_dir, "genesys_cache.json")
        
        self.banlist_data = {"ocg": {}, "sc": {}}
        self.genesys_data = {} 
        self.name_map = {} 

        self.load_local_data()

    def load_local_data(self):
        try:
            if os.path.exists(self.banlist_cache_file):
                with open(self.banlist_cache_file, "r", encoding="utf-8") as f:
                    self.banlist_data = json.load(f)
            if os.path.exists(self.name_map_file):
                with open(self.name_map_file, "r", encoding="utf-8") as f:
                    self.name_map = json.load(f)
            if os.path.exists(self.genesys_file):
                with open(self.genesys_file, "r", encoding="utf-8") as f:
                    self.genesys_data = json.load(f)
        except Exception as e:
            logger.error(f"加载禁卡表数据失败: {e}")

    def save_data(self):
        try:
            with open(self.banlist_cache_file, "w", encoding="utf-8") as f:
                json.dump(self.banlist_data, f, ensure_ascii=False, indent=2)
            with open(self.name_map_file, "w", encoding="utf-8") as f:
                json.dump(self.name_map, f, ensure_ascii=False, indent=2)
            with open(self.genesys_file, "w", encoding="utf-8") as f:
                json.dump(self.genesys_data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存数据失败: {e}")

    # ================= Genesys 更新逻辑 (稳定版) =================
    async def update_genesys(self, card_searcher) -> Tuple[bool, str, List[str]]:
        main_page_url = "https://registration.yugioh-card.com/genesys/CardList/"
        api_url = "https://registration.yugioh-card.com/genesys/CardListSearch/PointsList"
        
        # 限制百鸽查询并发，防止崩掉查卡功能
        sem_search = asyncio.Semaphore(5)

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Connection": "keep-alive",
        }
        api_headers = headers.copy()
        api_headers.update({
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "X-Requested-With": "XMLHttpRequest",
            "Origin": "https://registration.yugioh-card.com",
            "Referer": "https://registration.yugioh-card.com/genesys/CardList/"
        })

        try:
            connector = aiohttp.TCPConnector(ssl=False, force_close=True)
            # 使用单独的 Session，不要和 card_searcher 混用
            async with aiohttp.ClientSession(headers=headers, connector=connector, trust_env=True, timeout=aiohttp.ClientTimeout(total=60)) as session:
                
                # 1. Session 预热
                logger.info("Genesys: 正在连接服务器...")
                try:
                    async with session.get(main_page_url) as r: await r.read()
                except: pass

                # 2. 获取总数
                pl = {"currentPage": 1, "resultsPerPage": 100, "searchTerm": ""}
                async with session.post(api_url, data=pl, headers=api_headers) as resp:
                    if resp.status != 200: return False, f"API 连接失败 {resp.status}", []
                    try:
                        res_json = await resp.json()
                    except:
                        return False, "API 返回非 JSON，可能被拦截", []
                
                if res_json.get("Success") != "Success":
                    return False, f"API 逻辑错误", []

                total_results = res_json["Result"]["TotalResults"]
                total_pages = math.ceil(total_results / 100)
                logger.info(f"Genesys: 发现 {total_results} 条数据，开始下载 (共 {total_pages} 页)...")

                # 3. 顺序抓取 (解决数量变少问题)
                all_raw_items = []
                for page in range(1, total_pages + 1):
                    # 稍微延时，防封 + 减轻服务器压力
                    await asyncio.sleep(0.5) 
                    try:
                        pl = {"currentPage": page, "resultsPerPage": 100, "searchTerm": ""}
                        async with session.post(api_url, data=pl, headers=api_headers) as p_resp:
                            if p_resp.status == 200:
                                d = await p_resp.json()
                                items = d.get("Result", {}).get("Results", [])
                                if items:
                                    all_raw_items.extend(items)
                                    logger.info(f"Genesys: 第 {page}/{total_pages} 页下载成功 (当前共 {len(all_raw_items)} 条)")
                                else:
                                    logger.warning(f"Genesys: 第 {page} 页为空")
                    except Exception as e:
                        logger.error(f"Genesys: 第 {page} 页抓取失败: {e}")

            # 4. 解析 ID 并获取中文名 (解决英文名问题)
            logger.info(f"Genesys: 下载完毕，开始解析 {len(all_raw_items)} 条数据的 ID 和中文名...")
            
            new_genesys = {}
            report_list = []
            
            # 准备待处理列表
            pending_tasks = []
            
            for card in all_raw_items:
                points = int(card.get("Points", 0))
                en_name = card.get("Name")
                if points == 0 or not en_name: continue
                
                pending_tasks.append((en_name, points))

            # 内部函数：处理单个卡片
            async def process_card(en_name, points):
                async with sem_search: # 限制并发
                    try:
                        cn_name = en_name # 默认英文
                        final_id = None
                        
                        # A. 查本地缓存
                        if en_name in self.name_map:
                            final_id = self.name_map[en_name]
                            # 如果缓存命中了ID，为了报告好看，我们尝试查一下中文名(非必须，但体验好)
                            # 如果不想拖慢速度，可以跳过这一步，直接显示英文
                            # 这里为了体验，我们还是查一下详情
                            try:
                                detail = await card_searcher.get_card_detail(final_id)
                                if detail: cn_name = detail.get("cn_name", en_name)
                            except: pass
                        
                        # B. 查百鸽 API (如果本地没ID)
                        else:
                            await asyncio.sleep(0.2) # 避嫌
                            res = await card_searcher.search_card(en_name)
                            if res and res.get("result"):
                                first = res["result"][0]
                                final_id = str(first["id"])
                                cn_name = first.get("cn_name", en_name)
                                # 存入缓存
                                self.name_map[en_name] = final_id
                        
                        if final_id:
                            return (final_id, points, cn_name)
                    except Exception as e:
                        logger.warning(f"解析 {en_name} 失败: {e}")
                    return None

            # 并发执行 ID 解析
            results = await asyncio.gather(*[process_card(n, p) for n, p in pending_tasks])
            
            for res in results:
                if res:
                    fid, fpts, fname = res
                    new_genesys[fid] = fpts
                    report_list.append(f"{fname} ({fid}): {fpts}pt")

            self.genesys_data = new_genesys
            self.save_data()
            
            msg = f"Genesys 更新完毕! 原始 {len(all_raw_items)} 条，有效解析 {len(new_genesys)} 条。"
            return True, msg, report_list

        except Exception as e:
            import traceback
            logger.error(traceback.format_exc())
            return False, f"异常: {e}", []
        
    # ================= 禁卡表 更新逻辑 (含中文名优化) =================
    async def update_banlist(self, env_type: str, card_searcher) -> Tuple[bool, str, List[str]]:
        api_type = 1 if env_type == "ocg" else 2
        headers = {"User-Agent": "Mozilla/5.0 ..."} # 简略

        try:
            async with aiohttp.ClientSession(headers=headers) as session:
                # 1. 获取列表
                list_url = f"https://gamekingapi.windoent.com/forbidden/forbbidengroup/webList?type={api_type}"
                async with session.get(list_url) as resp:
                    if resp.status != 200: return False, f"列表请求失败 {resp.status}", []
                    list_data = await resp.json()
                
                if not list_data.get("list"): return False, "列表为空", []
                latest_meta = list_data["list"][0]
                latest_id = latest_meta["id"]
                version_name = latest_meta["name"]

                # 2. 获取详情
                detail_url = f"https://gamekingapi.windoent.com/forbidden/forbbidengroup/webinfo/{latest_id}"
                async with session.get(detail_url) as resp:
                    if resp.status != 200: return False, f"详情请求失败 {resp.status}", []
                    detail_data = await resp.json()

            # 3. 解析
            new_cards = {}
            changes = []
            
            for group in detail_data.get("list", []):
                group_name = group.get("name", "")
                
                status = "无限制"
                if "禁止" in group_name: status = "禁止"
                elif "准限制" in group_name: status = "准限制"
                elif "限制" in group_name and "解除" not in group_name: status = "限制"
                
                for card in group.get("list", []):
                    jp_name = card.get("name")
                    en_name = card.get("enName")
                    note = card.get("note")
                    if not jp_name: continue

                    # === 解析 ID ===
                    card_code = self.name_map.get(jp_name) or self.name_map.get(en_name)
                    
                    if not card_code:
                        # 没缓存，查百鸽
                        search_res = await card_searcher.search_card(jp_name)
                        if not search_res.get("result") and en_name:
                            search_res = await card_searcher.search_card(en_name)
                        
                        if search_res.get("result"):
                            card_code = str(search_res["result"][0]["id"])
                            self.name_map[jp_name] = card_code # 缓存
                            await asyncio.sleep(0.05) # 避嫌

                    # === 核心修改：如果是变动卡，获取中文名 ===
                    if note:
                        display_name = jp_name # 默认日文
                        if card_code:
                            # 尝试获取详情里的中文名
                            # 为了不让 update 太慢，我们可以直接用 card_searcher 的 get_card_detail
                            # 这是一个异步请求，但变动卡一般不多(10-20张)，可以接受
                            try:
                                detail = await card_searcher.get_card_detail(card_code)
                                if detail and "cn_name" in detail:
                                    display_name = detail["cn_name"]
                            except:
                                pass # 获取失败就用日文
                        
                        arrow = "➡️"
                        clean_note = note.replace("⇒", arrow)
                        changes.append(f"{display_name} ({clean_note})")

                    # 如果不是解除限制，则记录状态
                    if "解除" not in group_name and card_code:
                        new_cards[card_code] = status

            self.banlist_data[env_type] = {
                "version": version_name,
                "cards": new_cards,
                "changes": changes
            }
            self.save_data()
            return True, f"更新成功！版本：{version_name}", changes

        except Exception as e:
            logger.error(f"Banlist update failed: {e}")
            import traceback
            traceback.print_exc()
            return False, f"更新异常: {e}", []

    def get_card_status(self, card_id: str) -> Dict[str, Any]:
        """获取一张卡在所有环境的状态"""
        cid = str(card_id)
        # 即使数据为空，也要用 get 防止报错
        sc = self.banlist_data.get("sc", {}).get("cards", {}).get(cid, "无限制")
        ocg = self.banlist_data.get("ocg", {}).get("cards", {}).get(cid, "无限制")
        # 读取 Genesys 点数
        points = self.genesys_data.get(cid, 0)
        
        return {
            "sc": sc,
            "ocg": ocg,
            "genesys": points
        }

    def check_deck_legality(self, env: str, main: List[str], extra: List[str], side: List[str]) -> Dict:
        """全面检查卡组 (含Genesys)"""
        result = {
            "banlist_issues": [],
            "genesys_points": 0,
            "genesys_details": []
        }
        
        all_cards = main + extra + side
        from collections import Counter
        counts = Counter(all_cards)
        
        # 1. 检查禁限表
        cards_map = self.banlist_data.get(env, {}).get("cards", {})
        
        for cid, count in counts.items():
            cid_str = str(cid)
            status = cards_map.get(cid_str, "无限制")
            
            limit = 3
            if status == "禁止": limit = 0
            elif status == "限制": limit = 1
            elif status == "准限制": limit = 2
            
            if count > limit:
                result["banlist_issues"].append((cid_str, status, count, limit))

            # 2. 计算 Genesys 点数
            pts = self.genesys_data.get(cid_str, 0)
            if pts > 0:
                result["genesys_points"] += pts * count
                result["genesys_details"].append((cid_str, pts, count))
        
        return result