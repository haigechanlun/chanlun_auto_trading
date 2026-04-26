"""
OKX 永续合约交易模块
"""

import os
import sys 

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

import logging
import time
from typing import Optional, Dict, Any, List
from dataclasses import dataclass

# 导入您的 SDK
from okx.api.account import Account
from okx.api.trade import Trade
from okx.api.market import Market

from config import OKX_API_KEY, OKX_SECRET_KEY,OKX_PASSPHRASE

logger = logging.getLogger(__name__)


# 实盘: "0", 模拟盘: "1"
FLAG = "0"

# 合约面值配置 (每张合约代表的基础货币数量)
CONTRACT_MULTIPLIER = {
    "BTC-USDT-SWAP": 0.01,  # 1张 = 0.0001 BTC
    "ETH-USDT-SWAP": 0.1,    # 1张 = 0.01 ETH
    "DEFAULT": 0.0001
}


@dataclass
class PositionInfo:
    """持仓信息"""
    symbol: str
    side: str
    size_contract: int
    size_base: float
    size_usdt: float
    entry_price: float
    mark_price: float
    unrealized_pnl: float
    leverage: int
    mode: str


class OKXFuturesClient:
    """OKX 永续合约客户端 - 适配您的 SDK"""
    
    def __init__(self, api_key: str = None, secret_key: str = None, 
                 passphrase: str = None, flag: str = "1"):
        self.api_key = api_key or OKX_API_KEY
        self.secret_key = secret_key or OKX_SECRET_KEY
        self.passphrase = passphrase or OKX_PASSPHRASE
        self.flag = flag or FLAG
        
        # 初始化 API（根据您的 SDK 格式）
        self.account_api = Account(self.api_key, self.secret_key, self.passphrase)
        self.trade_api = Trade(self.api_key, self.secret_key, self.passphrase)
        self.market_api = Market(self.api_key, self.secret_key, self.passphrase)
        
        self._contract_cache = {}
    
    def _format_symbol(self, symbol: str) -> str:
        """格式化交易对符号为OKX永续合约格式"""
        symbol = symbol.upper()
        
        # 如果已经是正确格式
        if "-USDT-SWAP" in symbol:
            return symbol
        
        # 移除 USDT 后缀
        if symbol.endswith("USDT"):
            symbol = symbol.replace("USDT", "")
        
        return f"{symbol}-USDT-SWAP"
    
    def get_contract_info(self, symbol: str) -> Dict[str, Any]:
        """获取合约信息（使用缓存，避免频繁API调用）"""
        formatted = self._format_symbol(symbol)
        
        if formatted in self._contract_cache:
            return self._contract_cache[formatted]
        
        # 由于您的 Account 类没有 get_instruments 方法，使用默认值
        # 可以从市场数据中获取，或者使用固定值
        info = {
            "quanto_multiplier": get_multiplier(symbol),
            "min_size": 1,
            "max_size": 1000000,
            "mark_price": 0,
            "last_price": 0,
            "leverage_min": 1,
            "leverage_max": 100,
        }
        self._contract_cache[formatted] = info
        return info
    
    def get_mark_price(self, symbol: str) -> float:
        """获取最新成交价"""
        try:
            formatted = self._format_symbol(symbol)
            print(formatted)
            # formatted = "XAU-USDT"
            # 使用 market_api.get_ticker()
            result = self.market_api.get_ticker(instId=formatted)
            
            if result and result.get("code") == "0":
                data = result.get("data", [])
                if data:
                    # 使用最新成交价
                    last = data[0].get("last")
                    if last:
                        return float(last)
            return 0.0
        except Exception as e:
            logger.error(f"获取价格失败: {e}")
            return 0.0
    
    def get_balance(self) -> float:
        """获取可用余额 (USDT)"""
        try:
            result = self.account_api.get_balance(ccy="USDT")
            
            if result and result.get("code") == "0":
                data = result.get("data", [])
                if data:
                    details = data[0].get("details", [])
                    for detail in details:
                        if detail.get("ccy") == "USDT":
                            return float(detail.get("availBal", 0))
            return 0.0
        except Exception as e:
            logger.error(f"获取余额失败: {e}")
            return 0.0
    
    def get_total_balance(self) -> float:
        """获取总资产 (USDT)"""
        try:
            result = self.account_api.get_balance(ccy="USDT")
            print(result)
            if result and result.get("code") == "0":
                data = result.get("data", [])
                if data:
                    details = data[0].get("details", [])
                    for detail in details:
                        if detail.get("ccy") == "USDT":
                            return float(detail.get("eq", 0))
            return 0.0
        except Exception as e:
            logger.error(f"获取总资产失败: {e}")
            return 0.0
    
    def get_positions(self, symbol: str = None) -> List[PositionInfo]:
        """获取持仓信息"""
        try:
            result = self.account_api.get_positions()
            
            positions = []
            if result and result.get("code") == "0":
                for pos in result.get("data", []):
                    # 只处理永续合约
                    if "-SWAP" not in pos.get("instId", ""):
                        continue
                    
                    size = float(pos.get("pos", 0))
                    if size == 0:
                        continue
                    
                    pos_symbol = pos.get("instId", "")
                    if symbol and pos_symbol != symbol:
                        continue
                    
                    side = "long" if size > 0 else "short"
                    size_contract = abs(int(size))
                    
                    multiplier = 0.0001 if "BTC" in pos_symbol else 0.01
                    size_base = size_contract * multiplier
                    entry_price = float(pos.get("avgPx", 0))
                    mark_price = float(pos.get("markPx", 0))
                    size_usdt = size_base * entry_price if entry_price > 0 else 0
                    
                    positions.append(PositionInfo(
                        symbol=pos_symbol,
                        side=side,
                        size_contract=size_contract,
                        size_base=size_base,
                        size_usdt=size_usdt,
                        entry_price=entry_price,
                        mark_price=mark_price,
                        unrealized_pnl=float(pos.get("upl", 0)),
                        leverage=int(float(pos.get("lever", 1))),
                        mode=pos.get("posSide", "net")
                    ))
            
            return positions
        except Exception as e:
            logger.error(f"获取持仓失败: {e}")
            return []
    
    def get_multiplier(self, symbol):
        return 0.01 if "BTC" in symbol else 0.1



    def usdt_to_contracts(self, symbol: str, usdt_amount: float, price: float = None) -> int:
        """将USDT金额转换为合约张数"""
        if price is None:
            price = self.get_mark_price(symbol)
        
        if price <= 0 or usdt_amount <= 0:
            return 0
        
        multiplier = self.get_multiplier(symbol)
        min_size = 1
        
        contracts = int(usdt_amount / (price * multiplier))
        contracts = max(contracts, min_size)
        
        return contracts
    
    def usdt_to_base(self, symbol: str, usdt_amount: float, price: float = None) -> float:
        """将USDT金额转换为基础货币数量"""
        if price is None:
            price = self.get_mark_price(symbol)
        
        if price <= 0 or usdt_amount <= 0:
            return 0.0
        
        return usdt_amount / price
    
    def place_order(self, symbol: str, side: str, contracts: int, 
                    price: float = None, reduce_only: bool = False) -> Optional[Dict]:
        """
        下单 - 使用 trade_api.set_order()
        """
        try:
            formatted = self._format_symbol(symbol)
            print(formatted)
            print(contracts)
            
            # 确定订单类型
            ord_type = "market"
            
            # 确定持仓方向
            # side: 'buy' 或 'sell'
            # 对于开平仓模式，需要指定 posSide
            if side == "buy":
                pos_side = "long" if not reduce_only else "short"
            else:
                pos_side = "short" if not reduce_only else "long"
            
            # 调用 set_order
            result = self.trade_api.set_order(
                instId=formatted,
                tdMode="cross",  # 全仓模式
                side=side,
                posSide=pos_side,
                ordType=ord_type,
                sz=str(contracts),
                px=str(price) if price is not None else "",
                reduceOnly=reduce_only
            )

            print(result)
            
            if result and result.get("code") == "0":
                multiplier = self.get_multiplier(symbol)
                fill_price = price or self.get_mark_price(symbol)
                actual_base = contracts * multiplier
                actual_usdt = actual_base * fill_price
                
                logger.info(f"下单成功: {symbol} {side} {contracts}张, "
                           f"成交价≈${fill_price:.2f}")
                
                return {
                    "success": True,
                    "order_id": result.get("data", [{}])[0].get("ordId") if result.get("data") else None,
                    "contracts": contracts,
                    "fill_price": fill_price,
                    "actual_base": actual_base,
                    "actual_usdt": actual_usdt
                }
            else:
                error_msg = result.get("msg", "未知错误") if result else "请求失败"
                logger.error(f"下单失败: {error_msg} res:{result}")
                return {"success": False, "error": error_msg}
            
        except Exception as e:
            logger.error(f"下单失败: {e}")
            return {"success": False, "error": str(e)}
    
    def open_long(self, symbol: str, usdt_amount: float, price: float = None) -> Optional[Dict]:
        """开多仓"""
        if price is None:
            price = self.get_mark_price(symbol)
        
        contracts = self.usdt_to_contracts(symbol, usdt_amount, price)
        
        if contracts <= 0:
            logger.error(f"开多仓失败: 无效张数 {contracts}")
            return None
        
        logger.info(f"开多仓: 目标USDT={usdt_amount:.2f}, 张数={contracts}")
        return self.place_order(symbol, "buy", contracts, price, reduce_only=False)
    
    def open_short(self, symbol: str, usdt_amount: float, price: float = None) -> Optional[Dict]:
        """开空仓"""
        if price is None:
            price = self.get_mark_price(symbol)
        
        contracts = self.usdt_to_contracts(symbol, usdt_amount, price)
        
        if contracts <= 0:
            logger.error(f"开空仓失败: 无效张数 {contracts}")
            return None
        
        logger.info(f"开空仓: 目标USDT={usdt_amount:.2f}, 张数={contracts}")
        return self.place_order(symbol, "sell", contracts, price, reduce_only=False)
    
    def close_long(self, symbol: str, contracts: int = None) -> Optional[Dict]:
        """平多仓"""
        if contracts is None:
            positions = self.get_positions(symbol)
            for pos in positions:
                if pos.side == "long":
                    contracts = pos.size_contract
                    break
        
        if contracts <= 0:
            logger.warning(f"平多仓: 无持仓或无效张数")
            return None
        
        logger.info(f"平多仓: {symbol} 平仓 {contracts}张")
        return self.place_order(symbol, "sell", contracts, reduce_only=True)
    
    def close_short(self, symbol: str, contracts: int = None) -> Optional[Dict]:
        """平空仓"""
        if contracts is None:
            positions = self.get_positions(symbol)
            for pos in positions:
                if pos.side == "short":
                    contracts = pos.size_contract
                    break
        
        if contracts <= 0:
            logger.warning(f"平空仓: 无持仓或无效张数")
            return None
        
        logger.info(f"平空仓: {symbol} 平仓 {contracts}张")
        return self.place_order(symbol, "buy", contracts, reduce_only=True)
    
    def mock_open_position(self, symbol: str, usdt_amount: float, price: float = None) -> Optional[Dict]:
        """模拟开仓"""
        if price is None:
            price = self.get_mark_price(symbol)
        
        contracts = self.usdt_to_contracts(symbol, usdt_amount, price)
        
        if contracts <= 0:
            logger.error(f"模拟开仓失败: 无效张数 {contracts}")
            return None
        
        multiplier = self.get_multiplier(symbol)
        actual_base = contracts * multiplier
        actual_usdt = actual_base * price
        
        logger.info(f"📊 [模拟] 开仓: {symbol}, 目标USDT={usdt_amount:.2f}, "
                   f"张数={contracts}, 数量={actual_base:.8f}")
        
        return {
            "success": True,
            "order_id": f"mock_{int(time.time())}",
            "contracts": contracts,
            "fill_price": price,
            "actual_base": actual_base,
            "actual_usdt": actual_usdt
        }


# 全局客户端实例
_client = None


def get_client() -> OKXFuturesClient:
    global _client
    if _client is None:
        _client = OKXFuturesClient(flag=FLAG)
    return _client


# ====== 便捷函数 ======

def get_mark_price(symbol: str) -> float:
    return get_client().get_mark_price(symbol)


def get_balance() -> float:
    return get_client().get_balance()


def get_total_balance() -> float:
    return get_client().get_total_balance()


def get_position(symbol: str) -> Optional[Dict]:
    positions = get_client().get_positions(symbol)
    if not positions:
        return None
    
    pos = positions[0]
    return {
        "symbol": pos.symbol,
        "side": pos.side,
        "size_contract": pos.size_contract,
        "size_btc": pos.size_base,
        "size_usdt": pos.size_usdt,
        "entry_price": pos.entry_price,
        "mark_price": pos.mark_price,
        "pnl": pos.unrealized_pnl,
        "leverage": pos.leverage,
        "mode": pos.mode
    }


def get_real_size(symbol: str, usdt_amount: float, price: float = None) -> float:
    return get_client().usdt_to_base(symbol, usdt_amount, price)


def get_contracts_from_usdt(symbol: str, usdt_amount: float, price: float = None) -> int:
    return get_client().usdt_to_contracts(symbol, usdt_amount, price)


def open_long(symbol: str, usdt_amount: float) -> Optional[Dict]:
    return get_client().open_long(symbol, usdt_amount)


def open_short(symbol: str, usdt_amount: float) -> Optional[Dict]:
    return get_client().open_short(symbol, usdt_amount)


def close_long(symbol: str, usdt_amount: float = None) -> Optional[Dict]:
    if usdt_amount is not None:
        price = get_mark_price(symbol)
        contracts = get_client().usdt_to_contracts(symbol, usdt_amount, price)
        return get_client().close_long(symbol, contracts)
    return get_client().close_long(symbol)


def close_short(symbol: str, usdt_amount: float = None) -> Optional[Dict]:
    if usdt_amount is not None:
        price = get_mark_price(symbol)
        contracts = get_client().usdt_to_contracts(symbol, usdt_amount, price)
        return get_client().close_short(symbol, contracts)
    return get_client().close_short(symbol)


def mock_open_position(symbol: str, usdt_amount: float, price: float = None) -> Optional[Dict]:
    return get_client().mock_open_position(symbol, usdt_amount, price)


if __name__ == "__main__":
    print("="*50)
    print("测试 OKX 交易模块")
    print("="*50)
    
    # # 测试价格获取
    # price = get_mark_price("BTC-USDT-SWAP")
    # print(f"BTC价格: ${price:.2f}")
    
    # # 测试余额
    balance = get_balance()
    print(f"可用余额: ${balance:.2f}")

    # open_short("BTC-USDT-SWAP", 1000)
    # print(get_position("BTC-USDT-SWAP"))

    # close_short("BTC-USDT-SWAP", 1000)

    # print(get_position("BTC-USDT-SWAP"))

    print(get_total_balance())

    print(get_mark_price("XAUUSDT"))
    print(get_mark_price("ETHUSDT"))
