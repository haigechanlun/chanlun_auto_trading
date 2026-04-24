
"""
Gate.io 永续合约交易模块 
统一使用 USDT 作为价值单位，正确处理合约张数转换
"""

import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

import logging
import time
from decimal import Decimal as D
from typing import Optional, Dict, Any, Tuple, List
from dataclasses import dataclass
from gate_api import ApiClient, Configuration, FuturesApi, FuturesOrder
from gate_api.exceptions import GateApiException
from config import GATE_API_KEY,GATE_API_SECRET

logger = logging.getLogger(__name__)

# ====== 配置 ======
HOST = "https://api.gateio.ws/api/v4"
SETTLE = "usdt"

@dataclass
class PositionInfo:
    """持仓信息"""
    symbol: str
    side: str  # 'long' or 'short'
    size_contract: int  # 合约张数
    size_base: float  # 基础货币数量 (BTC/ETH)
    size_usdt: float  # USDT价值
    entry_price: float
    mark_price: float
    unrealized_pnl: float
    leverage: int
    mode: str
    
    @property
    def pnl_percent(self) -> float:
        """盈亏百分比"""
        if self.size_usdt == 0:
            return 0
        return (self.unrealized_pnl / self.size_usdt) * 100


class GateFuturesClient:
    """Gate.io 永续合约客户端"""
    
    def __init__(self, api_key: str = None, api_secret: str = None):
        self.api_key = api_key or GATE_API_KEY
        self.api_secret = api_secret or GATE_API_SECRET
        self._client = None
        self._contract_cache = {}
    
    def _get_client(self):
        """获取API客户端"""
        if self._client is None:
            config = Configuration(
                key=self.api_key,
                secret=self.api_secret,
                host=HOST
            )
            api_client = ApiClient(config)
            self._client = type("Client", (), {
                "futures_api": FuturesApi(api_client),
            })()
        return self._client
    
    def _format_symbol(self, symbol: str) -> str:
        """格式化交易对符号"""
        symbol = symbol.upper()
        if "_" in symbol:
            return symbol
        if "/" in symbol:
            return symbol.replace("/", "_")
        if symbol.endswith("USDT"):
            return symbol.replace("USDT", "_USDT")
        return f"{symbol}_USDT"
    
    def get_contract_info(self, symbol: str) -> Dict[str, Any]:
        """获取合约信息（带缓存）"""
        formatted = self._format_symbol(symbol)
        
        if formatted in self._contract_cache:
            return self._contract_cache[formatted]
        
        try:
            client = self._get_client()
            contract = client.futures_api.get_futures_contract(SETTLE, formatted)
            print(contract)
            info = {
                "quanto_multiplier": float(contract.quanto_multiplier),  # 面值
                "min_size": float(contract.order_size_min),  # 最小下单张数
                "max_size": float(contract.order_size_max),  # 最大下单张数
                # "mark_price": float(contract.mark_price),
                # "last_price": float(contract.last_price),
                # "leverage_min": int(contract.leverage_min),
                # "leverage_max": int(contract.leverage_max),
            }
            self._contract_cache[formatted] = info
            return info
        except Exception as e:
            logger.error(f"获取合约信息失败 {symbol}: {e}")
            return {
                "quanto_multiplier": 0.0001 if "BTC" in symbol else 0.001,
                "min_size": 1,
                "max_size": 1000000,
                "mark_price": 0,
                "last_price": 0,
                "leverage_min": 1,
                "leverage_max": 100,
            }
    
    def get_mark_price(self, symbol: str) -> float:
        """获取标记价格"""
        try:
            client = self._get_client()
            formatted = self._format_symbol(symbol)
            tickers = client.futures_api.list_futures_tickers(SETTLE, contract=formatted)
            
            if tickers:
                ticker = tickers[0]
                if ticker.mark_price:
                    return float(ticker.mark_price)
                if ticker.last:
                    return float(ticker.last)
            return 0.0
        except Exception as e:
            logger.error(f"获取标记价格失败: {e}")
            return 0.0
    
    def get_balance(self) -> float:
        """获取可用余额 (USDT)"""
        try:
            client = self._get_client()
            account = client.futures_api.list_futures_accounts(SETTLE)
            return float(account.available) if account else 0.0
        except Exception as e:
            logger.error(f"获取余额失败: {e}")
            return 0.0
    
    def get_total_balance(self) -> float:
        """获取总资产 (USDT)"""
        try:
            client = self._get_client()
            account = client.futures_api.list_futures_accounts(SETTLE)
            return float(account.total) if account else 0.0
        except Exception as e:
            logger.error(f"获取总资产失败: {e}")
            return 0.0
    
    def get_positions(self, symbol: str = None) -> List[PositionInfo]:
        """获取持仓信息"""
        try:
            client = self._get_client()
            positions = client.futures_api.list_positions(SETTLE)
            
            result = []
            for pos in positions:
                size = float(pos.size)
                if size == 0:
                    continue
                
                pos_symbol = pos.contract.replace("_", "")
                if symbol and pos_symbol != symbol:
                    continue
                
                side = "long" if size > 0 else "short"
                size_contract = abs(int(size))
                
                contract_info = self.get_contract_info(pos_symbol)
                multiplier = contract_info["quanto_multiplier"]
                
                size_base = size_contract * multiplier
                entry_price = float(pos.entry_price)
                mark_price = float(pos.mark_price)
                size_usdt = size_base * entry_price
                
                result.append(PositionInfo(
                    symbol=pos_symbol,
                    side=side,
                    size_contract=size_contract,
                    size_base=size_base,
                    size_usdt=size_usdt,
                    entry_price=entry_price,
                    mark_price=mark_price,
                    unrealized_pnl=float(pos.unrealised_pnl),
                    leverage=int(pos.leverage),
                    mode=pos.mode
                ))
            
            return result
        except Exception as e:
            logger.error(f"获取持仓失败: {e}")
            return []
    
    def usdt_to_contracts(self, symbol: str, usdt_amount: float, price: float = None) -> int:
        """
        将USDT金额转换为合约张数
        
        公式: 合约张数 = USDT金额 / (价格 × 面值)
        
        Args:
            symbol: 交易对
            usdt_amount: USDT金额
            price: 价格（可选，默认使用当前标记价格）
        
        Returns:
            合约张数
        """
        if price is None:
            price = self.get_mark_price(symbol)
        
        if price <= 0 or usdt_amount <= 0:
            return 0
        
        contract_info = self.get_contract_info(symbol)
        multiplier = contract_info["quanto_multiplier"]
        min_size = contract_info["min_size"]
        print(contract_info)
        
        # 计算张数
        contracts = int(usdt_amount / (price * multiplier))
        
        # 确保最小下单量
        contracts = max(contracts, min_size)
        
        return contracts
    
    def contracts_to_usdt(self, symbol: str, contracts: int, price: float = None) -> float:
        """将合约张数转换为USDT价值"""
        if price is None:
            price = self.get_mark_price(symbol)
        
        if price <= 0 or contracts <= 0:
            return 0
        
        contract_info = self.get_contract_info(symbol)
        multiplier = contract_info["quanto_multiplier"]
        
        return contracts * multiplier * price
    
    def contracts_to_base(self, symbol: str, contracts: int) -> float:
        """将合约张数转换为基础货币数量（BTC/ETH）"""
        contract_info = self.get_contract_info(symbol)
        multiplier = contract_info["quanto_multiplier"]
        return contracts * multiplier
    
    def usdt_to_base(self, symbol: str, usdt_amount: float, price: float = None) -> float:
        """
        将USDT金额转换为基础货币数量（BTC/ETH）
        
        公式: 基础货币数量 = USDT金额 / 价格
        
        Args:
            symbol: 交易对
            usdt_amount: USDT金额
            price: 价格（可选）
        
        Returns:
            基础货币数量
        """
        if price is None:
            price = self.get_mark_price(symbol)
        
        if price <= 0 or usdt_amount <= 0:
            return 0.0
        
        return usdt_amount / price
    
    def place_order(self, symbol: str, side: str, contracts: int, 
                    price: float = None, reduce_only: bool = False) -> Optional[Dict]:
        """
        下单
        
        Args:
            symbol: 交易对
            side: 'BUY' 或 'SELL'
            contracts: 合约张数
            price: 限价价格（None表示市价）
            reduce_only: 是否只减仓
        
        Returns:
            订单结果
        """
        try:
            client = self._get_client()
            formatted = self._format_symbol(symbol)
            
            # Gate规则: BUY为正数，SELL为负数
            size = contracts if side == "BUY" else -contracts
            
            order = FuturesOrder(
                contract=formatted,
                size=size,
                price="0",
                tif="ioc",
                reduce_only=reduce_only
            )
            
            result = client.futures_api.create_futures_order(SETTLE, order)
            print(result)

            # 计算实际成交信息
            contract_info = self.get_contract_info(symbol)
            multiplier = contract_info["quanto_multiplier"]
            fill_price = float(result.fill_price) if hasattr(result, 'fill_price') and result.fill_price else price or self.get_mark_price(symbol)
            
            if fill_price is None or fill_price == 0:
                fill_price = price or self.get_mark_price(symbol)

            actual_base = contracts * multiplier
            actual_usdt = actual_base * fill_price
            
            logger.info(f"下单成功: {symbol} {side} {contracts}张, "
                       f"成交价=${fill_price:.2f}, "
                       f"实际BTC={actual_base:.8f}, "
                       f"实际USDT=${actual_usdt:.2f}")
            
            return {
                "success": True,
                "order_id": result.id,
                "contracts": contracts,
                "fill_price": fill_price,
                "actual_base": actual_base,
                "actual_usdt": actual_usdt
            }
        except GateApiException as e:
            logger.error(f"Gate API错误: {e}")
            return {"success": False, "error": str(e)}
        except Exception as e:
            logger.error(f"下单失败: {e}")
            return {"success": False, "error": str(e)}
    
    def mock_open_position(self, symbol: str, usdt_amount: float, price: float = None) -> Optional[Dict]:
        """
        开多仓
        
        Args:
            symbol: 交易对
            usdt_amount: USDT金额（想要开仓的价值）
            price: 价格（可选）
        
        Returns:
            订单结果，包含实际成交信息
        """
        if price is None:
            price = self.get_mark_price(symbol)
        
        # 转换为合约张数
        contracts = self.usdt_to_contracts(symbol, usdt_amount, price)
        
        if contracts <= 0:
            logger.error(f"开模拟仓失败: 无效张数 {contracts}")
            return None
        
        # 计算实际开仓信息
        multiplier = self.get_contract_info(symbol)["quanto_multiplier"]
        actual_base = contracts * multiplier
        actual_usdt = actual_base * price
        
        logger.info(f"开模拟仓: 目标USDT={usdt_amount:.2f}, "
                   f"实际张数={contracts}, "
                   f"实际BTC={actual_base:.8f}, "
                   f"实际USDT={actual_usdt:.2f}")
        
        return {
            "success":1,
            "actual_usdt":actual_usdt,
            "fill_price":price,
            "actual_base":actual_base
        }

    def open_long(self, symbol: str, usdt_amount: float, price: float = None) -> Optional[Dict]:
        """
        开多仓
        
        Args:
            symbol: 交易对
            usdt_amount: USDT金额（想要开仓的价值）
            price: 价格（可选）
        
        Returns:
            订单结果，包含实际成交信息
        """
        if price is None:
            price = self.get_mark_price(symbol)
        
        # 转换为合约张数
        contracts = self.usdt_to_contracts(symbol, usdt_amount, price)
        
        if contracts <= 0:
            logger.error(f"开多仓失败: 无效张数 {contracts}")
            return None
        
        # 计算实际开仓信息
        multiplier = self.get_contract_info(symbol)["quanto_multiplier"]
        actual_base = contracts * multiplier
        actual_usdt = actual_base * price
        
        logger.info(f"开多仓: 目标USDT={usdt_amount:.2f}, "
                   f"实际张数={contracts}, "
                   f"实际BTC={actual_base:.8f}, "
                   f"实际USDT={actual_usdt:.2f}")
        
        return self.place_order(symbol, "BUY", contracts, price)
    
    def open_short(self, symbol: str, usdt_amount: float, price: float = None) -> Optional[Dict]:
        """
        开空仓
        
        Args:
            symbol: 交易对
            usdt_amount: USDT金额（想要开仓的价值）
            price: 价格（可选）
        
        Returns:
            订单结果，包含实际成交信息
        """
        if price is None:
            price = self.get_mark_price(symbol)
        
        # 转换为合约张数
        contracts = self.usdt_to_contracts(symbol, usdt_amount, price)
        
        if contracts <= 0:
            logger.error(f"开空仓失败: 无效张数 {contracts}")
            return None
        
        # 计算实际开仓信息
        multiplier = self.get_contract_info(symbol)["quanto_multiplier"]
        actual_base = contracts * multiplier
        actual_usdt = actual_base * price
        
        logger.info(f"开空仓: 目标USDT={usdt_amount:.2f}, "
                   f"实际张数={contracts}, "
                   f"实际BTC={actual_base:.8f}, "
                   f"实际USDT={actual_usdt:.2f}")
        
        return self.place_order(symbol, "SELL", contracts, price)
    
    def close_long(self, symbol: str, contracts: int = None) -> Optional[Dict]:
        """平多仓"""
        if contracts is None:
            # 获取当前持仓
            positions = self.get_positions(symbol)
            for pos in positions:
                if pos.side == "long":
                    contracts = pos.size_contract
                    break
        
        if contracts <= 0:
            logger.warning(f"平多仓: 无持仓或无效张数")
            return None
        
        logger.info(f"平多仓: {symbol} 平仓 {contracts}张")
        return self.place_order(symbol, "SELL", contracts, reduce_only=True)
    
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
        return self.place_order(symbol, "BUY", contracts, reduce_only=True)


# 全局客户端实例
_client = None


def get_client() -> GateFuturesClient:
    """获取全局客户端实例"""
    global _client
    if _client is None:
        _client = GateFuturesClient()
    return _client


# ====== 便捷函数（保持接口兼容） ======

def get_mark_price(symbol: str) -> float:
    """获取标记价格"""
    return get_client().get_mark_price(symbol)


def get_balance() -> float:
    """获取可用余额"""
    return get_client().get_balance()


def get_total_balance() -> float:
    """获取总资产"""
    return get_client().get_total_balance()


def get_position(symbol: str) -> Optional[Dict]:
    """获取持仓（兼容旧格式）"""
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
    """
    获取USDT金额对应的基础货币数量（BTC/ETH）
    
    公式: 基础货币数量 = USDT金额 / 价格
    
    示例:
        symbol=BTCUSDT, usdt=2000, price=67000
        -> 2000 / 67000 = 0.02985 BTC
    
    Args:
        symbol: 交易对
        usdt_amount: USDT金额
        price: 当前价格（可选）
    
    Returns:
        基础货币数量
    """
    return get_client().usdt_to_base(symbol, usdt_amount, price)

def mock_open_position(symbol: str, usdt_amount: float, price: float = None) -> Optional[Dict]:
    return get_client().mock_open_position(symbol, usdt_amount, price)

def get_contracts_from_usdt(symbol: str, usdt_amount: float, price: float = None) -> int:
    """从USDT金额获取合约张数"""
    return get_client().usdt_to_contracts(symbol, usdt_amount, price)


def open_long(symbol: str, usdt_amount: float) -> Optional[Dict]:
    """开多仓（使用USDT金额）"""
    return get_client().open_long(symbol, usdt_amount)


def open_short(symbol: str, usdt_amount: float) -> Optional[Dict]:
    """开空仓（使用USDT金额）"""
    return get_client().open_short(symbol, usdt_amount)


def close_long(symbol: str, usdt_amount: float = None) -> Optional[Dict]:
    """平多仓"""
    if usdt_amount is not None:
        # 将USDT金额转换为张数
        price = get_mark_price(symbol)
        contracts = get_client().usdt_to_contracts(symbol, usdt_amount, price)
        return get_client().close_long(symbol, contracts)
    return get_client().close_long(symbol)


def close_short(symbol: str, usdt_amount: float = None) -> Optional[Dict]:
    """平空仓"""
    if usdt_amount is not None:
        price = get_mark_price(symbol)
        contracts = get_client().usdt_to_contracts(symbol, usdt_amount, price)
        return get_client().close_short(symbol, contracts)
    return get_client().close_short(symbol)


if __name__ == "__main__":
    # 测试代码
    print("="*50)
    print("测试 Gate.io 交易模块")
    print("="*50)
    
    client = get_client()
    
    # 测试获取余额
    balance = get_balance()
    print(f"可用余额: ${balance:.2f}")
    
    total = get_total_balance()
    print(f"总资产: ${total:.2f}")
    
    # # 测试获取价格
    # price = get_mark_price("BTCUSDT")
    # print(f"BTC价格: ${price:.2f}")
    
    # # 测试单位转换
    # usdt_amount = 2000
    # btc_amount = get_real_size("BTCUSDT", usdt_amount, price)
    # print(f"\n单位转换测试:")
    # print(f"  {usdt_amount} USDT = {btc_amount:.8f} BTC (价格: ${price:.2f})")
    
    # contracts = get_contracts_from_usdt("BTCUSDT", usdt_amount, price)
    # print(f"  {usdt_amount} USDT = {contracts} 张合约")
    
    # # 测试获取持仓
    # positions = client.get_positions("BTCUSDT")
    # print(f"\n当前持仓: {len(positions)}个")
    # for pos in positions:
    #     print(f"  {pos.symbol} {pos.side}: {pos.size_contract}张, "
    #           f"{pos.size_base:.8f} BTC, ${pos.size_usdt:.2f}, "
    #           f"盈亏: ${pos.unrealized_pnl:.2f}")

    # close_short("BTCUSDT")
    # open_short("ETHUSDT", 3000)
    # close_short("ETHUSDT")

    # close_short("ETHUSDT", 2000)
    # open_short("RAVEUSDT", 500)

    # close_short("RAVEUSDT", 600)

    positions = client.get_positions("ETHUSDT")
    print(f"\n当前持仓: {positions}个")
    positions = client.get_positions("RAVEUSDT")
    print(f"\n当前持仓: {positions}个")
    # close_short("ETHUSDT", 3000)

