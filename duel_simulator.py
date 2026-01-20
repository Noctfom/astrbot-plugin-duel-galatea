# -*- coding: utf-8 -*-
import random
from typing import Dict, List, Optional, Tuple

class DuelSimulator:
    def __init__(self):
        # 存储结构: 
        # { 
        #   "user_12345": { 
        #       "deck": ["89631139", ...], 
        #       "hand": ["12345678", ...] 
        #   } 
        # }
        self.states: Dict[str, Dict[str, List[str]]] = {}

    def init_duel(self, user_id: str, main_deck_ids: List[str]):
        """初始化决斗状态：重置手牌，洗切卡组"""
        deck = main_deck_ids.copy()
        random.shuffle(deck)  # 洗牌
        self.states[user_id] = {
            "deck": deck,
            "hand": []
        }

    def draw_card(self, user_id: str, count: int = 1) -> List[str]:
        """从卡组顶端抽卡"""
        state = self.states.get(user_id)
        if not state:
            return []
        
        drawn = []
        for _ in range(count):
            if state["deck"]:
                card = state["deck"].pop(0)  # 移除列表第一个元素
                state["hand"].append(card)
                drawn.append(card)
            else:
                break
        return drawn

    def remove_from_deck_to_hand(self, user_id: str, card_id: str) -> bool:
        """精准检索：将指定ID从卡组移到手牌"""
        state = self.states.get(user_id)
        if not state:
            return False
        
        # 检查卡片是否在卡组中
        if card_id in state["deck"]:
            state["deck"].remove(card_id)  # 移除指定ID
            state["hand"].append(card_id)  # 加入手牌
            return True
        return False

    def get_state(self, user_id: str) -> Optional[Dict[str, List[str]]]:
        """获取用户当前的决斗状态"""
        return self.states.get(user_id)

    def check_deck_contains(self, user_id: str, card_id: str) -> bool:
        """检查卡组里是否有某张卡（不移动）"""
        state = self.states.get(user_id)
        if not state:
            return False
        return card_id in state["deck"]