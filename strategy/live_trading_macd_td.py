# live_trading_macd_td_v6.py
"""
ETHUSDT 实盘交易系统 - MACD背驰 + TD信号组合策略V6
基于回测引擎逻辑，支持多级别仓位管理、移动止损、进程恢复
"""

import sys
import os
import time
import json
import logging
import signal
import pandas as pd
import numpy as np
import talib
import traceback
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, List, Tuple
from dataclasses import dataclass, asdict
from threading import Lock
import warnings
warnings.filterwarnings('ignore')

# 添加项目根目录到系统路径
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(BASE_DIR)

# 导入所需模块
from data.binance_api import get_klines
from trade.gate.trade import (
    open_long, open_short, close_long, close_short, 
    get_balance, get_position, get_mark_price, get_contracts_from_usdt,
    get_client, get_total_balance
)

# 导入Telegram模块
from utils.telegram_bot import get_telegram_bot, init_telegram

# ==============================
# 时区配置
# ==============================
UTC_TZ = timezone.utc
CST_TZ = timezone(timedelta(hours=8))


def utc_to_beijing(utc_time):
    if isinstance(utc_time, str):
        utc_time = pd.to_datetime(utc_time)
    if utc_time.tzinfo is None:
        utc_time = utc_time.replace(tzinfo=UTC_TZ)
    return utc_time.astimezone(CST_TZ)


# ==============================
# 日志配置
# ==============================
def setup_logger():
    """配置日志"""
    for handler in logging.root.handlers[:]:
        logging.root.removeHandler(handler)
    
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler('logs/live_trading_macd_td.log'),
            logging.StreamHandler()
        ],
        force=True
    )
    return logging.getLogger(__name__)


logger = setup_logger()


# ==============================
# 持仓状态数据类
# ==============================
@dataclass
class PositionState:
    """持仓状态"""
    type: str  # 'long' or 'short'
    entry_price: float
    entry_time: datetime
    size_usdt: float
    size_contracts: int
    remain_size_usdt: float
    remain_contracts: int
    stop_loss: float
    highest_price: float  # 多仓最高价
    lowest_price: float   # 空仓最低价
    tp_signals_triggered: List[str]  # 已触发的减仓级别
    initial_added: bool  # 是否已初始加仓
    entry_index: int  # 开仓时的K线索引
    entry_prob: float = 0.5  # 开仓时的AI概率
    trailing_stop_updated: bool = False  # 移动止损是否已更新
    
    def to_dict(self) -> dict:
        """转换为字典用于保存"""
        data = asdict(self)
        data['entry_time'] = self.entry_time.isoformat()
        return data
    
    @classmethod
    def from_dict(cls, data: dict) -> 'PositionState':
        """从字典恢复"""
        data['entry_time'] = datetime.fromisoformat(data['entry_time'])
        return cls(**data)


@dataclass
class TradeRecord:
    """交易记录"""
    action: str
    time: datetime
    price: float
    size_usdt: float
    size_contracts: int
    pnl: float = 0.0
    reason: str = ""
    signal_info: Dict = None
    
    def to_dict(self) -> dict:
        data = {
            'action': self.action,
            'time': self.time.isoformat(),
            'price': self.price,
            'size_usdt': self.size_usdt,
            'size_contracts': self.size_contracts,
            'pnl': self.pnl,
            'reason': self.reason
        }
        if self.signal_info:
            data['signal_info'] = self.signal_info
        return data
    
    @classmethod
    def from_dict(cls, data: dict) -> 'TradeRecord':
        data['time'] = datetime.fromisoformat(data['time'])
        return cls(**data)


# ==============================
# 实盘交易引擎
# ==============================
class LiveMACDTDStrategyV6:
    """
    实盘MACD背驰 + TD信号组合策略V6
    支持多级别仓位管理、移动止损、进程恢复
    """
    
    def __init__(self,
                 symbol: str = "ETHUSDT",
                 interval: str = "15m",
                 initial_balance: float = None,
                 risk_per_trade: float = 0.02,
                 min_divergence_strength: float = 0.25,
                 enable_buy_filter: bool = True,
                 buy_rsi_threshold: float = 40,
                 buy_volume_ratio: float = 0.8,
                 enable_30min_clear: bool = True,
                 min_bars_before_action: int = 1,
                 initial_add_size: float = 0.3,
                 trailing_stop_atr: float = 2.0,
                 trailing_stop_pct: float = 0.05,
                 min_profit_for_trailing: float = 0.01,
                 tp_ratios: Dict[str, float] = None,
                 klines_limit: int = 500,
                 check_interval: int = 60,
                 max_position_usdt: float = None,
                 min_position_usdt: float = None,
                 max_leverage: float = 2.0):
        """
        初始化实盘交易引擎
        
        Args:
            symbol: 交易对
            interval: K线周期
            initial_balance: 初始资金（None表示从交易所获取）
            risk_per_trade: 单笔风险比例
            min_divergence_strength: 最小背驰强度
            enable_buy_filter: 是否启用买入过滤
            buy_rsi_threshold: 买入RSI阈值
            buy_volume_ratio: 买入成交量比例
            enable_30min_clear: 是否启用30分钟TD清仓
            min_bars_before_action: 最小K线数保护
            initial_add_size: 初始加仓比例
            trailing_stop_atr: 移动止损ATR倍数
            trailing_stop_pct: 移动止损百分比
            min_profit_for_trailing: 最小盈利才能激活移动止损
            tp_ratios: 减仓比例配置
            klines_limit: K线数量限制
            check_interval: 检查间隔(秒)
            max_position_usdt: 最大仓位USDT
            min_position_usdt: 最小仓位USDT
            max_leverage: 最大杠杆倍数
        """
        self.symbol = symbol
        self.interval = interval
        
        # 获取实际余额
        real_balance = get_total_balance()
        self.initial_balance = initial_balance if initial_balance is not None else real_balance
        self.balance = self.initial_balance
        self.real_balance = real_balance
        
        # 策略参数
        self.risk_per_trade = risk_per_trade
        self.min_divergence_strength = min_divergence_strength
        self.enable_buy_filter = enable_buy_filter
        self.buy_rsi_threshold = buy_rsi_threshold
        self.buy_volume_ratio = buy_volume_ratio
        self.enable_30min_clear = enable_30min_clear
        self.min_bars_before_action = min_bars_before_action
        self.initial_add_size = initial_add_size
        self.trailing_stop_atr = trailing_stop_atr
        self.trailing_stop_pct = trailing_stop_pct
        self.min_profit_for_trailing = min_profit_for_trailing
        
        # 减仓比例
        self.tp_ratios = tp_ratios or {
            '1min': 0.25,
            '3min': 0.20,
            '5min': 0.25,
        }
        
        # 仓位限制
        self.max_leverage = max_leverage
        self.max_position_usdt = max_position_usdt or (self.balance * max_leverage)
        self.min_position_usdt = min_position_usdt or (self.balance * 0.01)
        
        self.klines_limit = klines_limit
        self.check_interval = check_interval
        
        # 持仓状态
        self.position: Optional[PositionState] = None
        self.trades: List[TradeRecord] = []
        
        # 资金曲线
        self.equity_curve = [self.initial_balance]
        self.equity_timestamps = [datetime.now()]
        
        # 多周期K线数据
        self.df_15m = None
        self.df_5m = None
        self.df_3m = None
        self.df_1m = None
        self.df_30m = None
        
        # 背驰记录
        self.last_bullish_index = -1
        self.last_bearish_index = -1
        
        # 运行状态
        self.running = False
        self.paused = False
        self.start_time = None
        
        # 线程锁
        self._lock = Lock()
        
        # 配置文件
        self.config_file = f"logs/live_trading_macd_td_{symbol.lower()}.json"
        
        # Telegram通知
        self.telegram_bot = get_telegram_bot()
        if not self.telegram_bot:
            self.telegram_bot = init_telegram(symbol=symbol)
        
        # 每日统计
        self.last_daily_report = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        self.daily_stats = {
            'initial_balance': self.initial_balance,
            'daily_pnl': 0,
            'daily_trades_count': 0,
            'daily_win_count': 0
        }
        
        # 加载配置恢复状态
        self.load_config()
        
        # 初始化数据
        self.update_all_data()
        
        # 注册信号处理
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
        
        self._log_init_info()
    
    def _log_init_info(self):
        """打印初始化信息"""
        logger.info("="*70)
        logger.info("实盘MACD背驰+TD信号策略V6初始化完成")
        logger.info("="*70)
        logger.info(f"交易对: {self.symbol}, 周期: {self.interval}")
        logger.info(f"初始资金: ${self.initial_balance:,.2f}")
        logger.info(f"实际余额: ${self.real_balance:,.2f}")
        logger.info(f"单笔风险: {self.risk_per_trade*100}%")
        logger.info(f"最小背驰强度: {self.min_divergence_strength}")
        logger.info(f"移动止损: ATR倍数={self.trailing_stop_atr}, 百分比={self.trailing_stop_pct*100}%")
        logger.info(f"最小盈利激活移动止损: {self.min_profit_for_trailing*100}%")
        logger.info(f"最大仓位: ${self.max_position_usdt:,.2f}, 最小仓位: ${self.min_position_usdt:,.2f}")
        logger.info(f"最大杠杆: {self.max_leverage}x")
        logger.info(f"检查间隔: {self.check_interval}秒")
        logger.info("="*70)
        
        if self.telegram_bot and self.telegram_bot.bot_token:
            logger.info("✅ Telegram通知已启用")
        else:
            logger.warning("⚠️ Telegram通知未配置")
        
        if self.position:
            logger.info(f"📊 恢复持仓: {self.position.type} 仓, 开仓价=${self.position.entry_price:.2f}")
    
    def _signal_handler(self, signum, frame):
        """信号处理器"""
        logger.info(f"收到信号 {signum}，正在停止...")
        self.stop()
    
    def load_config(self):
        """加载配置文件恢复状态"""
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, 'r') as f:
                    config = json.load(f)
                    
                self.balance = config.get('balance', self.initial_balance)
                self.trades = [TradeRecord.from_dict(t) for t in config.get('trades', [])]
                self.equity_curve = config.get('equity_curve', [self.initial_balance])
                self.last_bullish_index = config.get('last_bullish_index', -1)
                self.last_bearish_index = config.get('last_bearish_index', -1)
                
                if config.get('position'):
                    self.position = PositionState.from_dict(config['position'])
                    logger.info(f"✅ 加载配置成功，恢复持仓: {self.position.type}")
                else:
                    self.position = None
                
                logger.info(f"加载配置成功，当前资金: ${self.balance:,.2f}")
                logger.info(f"历史交易数: {len(self.trades)}")
                
            except Exception as e:
                logger.error(f"加载配置文件失败: {e}", exc_info=True)
        else:
            logger.info("无历史配置文件，使用初始状态")
            self.save_config()
    
    def save_config(self):
        """保存配置文件"""
        try:
            with self._lock:
                config = {
                    'balance': self.balance,
                    'position': self.position.to_dict() if self.position else None,
                    'trades': [t.to_dict() for t in self.trades[-200:]],  # 保存最近200笔
                    'equity_curve': self.equity_curve[-200:],
                    'last_bullish_index': self.last_bullish_index,
                    'last_bearish_index': self.last_bearish_index,
                    'last_update': datetime.now().isoformat()
                }
                with open(self.config_file, 'w') as f:
                    json.dump(config, f, indent=2, default=str)
                logger.debug("配置文件保存成功")
        except Exception as e:
            logger.error(f"保存配置文件失败: {e}")
    
    def update_all_data(self) -> bool:
        """更新所有周期K线数据"""
        try:
            logger.debug("正在获取K线数据...")
            
            df_15m = get_klines(self.symbol, "15m", total_limit=self.klines_limit)
            df_5m = get_klines(self.symbol, "5m", total_limit=self.klines_limit)
            df_3m = get_klines(self.symbol, "3m", total_limit=self.klines_limit)
            df_1m = get_klines(self.symbol, "1m", total_limit=self.klines_limit)
            df_30m = get_klines(self.symbol, "30m", total_limit=self.klines_limit)
            
            if any(df is None or len(df) == 0 for df in [df_15m, df_5m, df_3m, df_1m, df_30m]):
                logger.error("获取K线数据失败")
                return False
            
            # 数据预处理
            for df in [df_15m, df_5m, df_3m, df_1m, df_30m]:
                if 'timestamp' in df.columns:
                    df.rename(columns={'timestamp': 'time'}, inplace=True)
                df['time'] = pd.to_datetime(df['time'])
                df['time_beijing'] = df['time'].apply(utc_to_beijing)
            
            # 计算技术指标
            self.df_15m = self._calculate_indicators(df_15m).dropna().reset_index(drop=True)
            self.df_5m = self._calculate_indicators(df_5m).dropna().reset_index(drop=True)
            self.df_3m = self._calculate_indicators(df_3m).dropna().reset_index(drop=True)
            self.df_1m = self._calculate_indicators(df_1m).dropna().reset_index(drop=True)
            self.df_30m = self._calculate_indicators(df_30m).dropna().reset_index(drop=True)
            
            # 计算成交量比率
            self.df_15m['volume_ma20'] = self.df_15m['volume'].rolling(20).mean()
            self.df_15m['volume_ratio'] = self.df_15m['volume'] / self.df_15m['volume_ma20']
            
            logger.debug(f"数据更新成功: 15m={len(self.df_15m)}, 5m={len(self.df_5m)}, "
                        f"3m={len(self.df_3m)}, 1m={len(self.df_1m)}, 30m={len(self.df_30m)}")
            return True
            
        except Exception as e:
            logger.error(f"更新数据失败: {e}", exc_info=True)
            return False
    
    def _calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """计算技术指标"""
        df = df.copy()
        
        # MACD
        df['macd'], df['macd_signal'], df['macd_hist'] = talib.MACD(
            df['close'], fastperiod=12, slowperiod=26, signalperiod=9
        )
        df['macd_fast'], _, _ = talib.MACD(
            df['close'], fastperiod=8, slowperiod=17, signalperiod=6
        )
        
        # ATR
        df['atr'] = talib.ATR(df['high'], df['low'], df['close'], timeperiod=14)
        
        # EMA
        df['ema20'] = talib.EMA(df['close'], timeperiod=20)
        df['ema60'] = talib.EMA(df['close'], timeperiod=60)
        
        # RSI
        df['rsi'] = talib.RSI(df['close'], timeperiod=14)
        
        # 趋势判断
        df['trend'] = 'sideways'
        df.loc[df['ema20'] > df['ema60'], 'trend'] = 'up'
        df.loc[df['ema20'] < df['ema60'], 'trend'] = 'down'
        
        return df
    
    def find_local_extremes(self, data: np.ndarray, window: int = 3) -> Tuple[List, List]:
        """寻找局部极值点"""
        peaks = []
        troughs = []
        
        for i in range(window, len(data) - window):
            is_peak = all(data[i] > data[i - j] and data[i] > data[i + j] for j in range(1, window + 1))
            if is_peak:
                peaks.append((i, data[i]))
            
            is_trough = all(data[i] < data[i - j] and data[i] < data[i + j] for j in range(1, window + 1))
            if is_trough:
                troughs.append((i, data[i]))
        
        return peaks, troughs
    
    def detect_bullish_divergence(self, price_troughs: List, macd_troughs: List, 
                                   macd_fast_troughs: List = None) -> Tuple[bool, float, Dict]:
        """检测底背驰"""
        if len(price_troughs) < 2 or len(macd_troughs) < 2:
            return False, 0, None
        
        price1, price2 = price_troughs[-2], price_troughs[-1]
        macd1, macd2 = macd_troughs[-2], macd_troughs[-1]
        
        if price2[1] < price1[1] and macd2[1] > macd1[1]:
            strength = abs((price2[1] - price1[1]) / price1[1]) * 50 + \
                      ((macd2[1] - macd1[1]) / abs(macd1[1]) if macd1[1] != 0 else 1) * 50
            strength = min(1.0, strength)
            info = {'price_low': price2[1], 'price_prev_low': price1[1], 
                    'index': price2[0], 'strength': strength}
            return True, strength, info
        
        # 使用快线MACD
        if macd_fast_troughs and len(macd_fast_troughs) >= 2:
            macd_fast1, macd_fast2 = macd_fast_troughs[-2], macd_fast_troughs[-1]
            if price2[1] < price1[1] and macd_fast2[1] > macd_fast1[1]:
                strength = abs((price2[1] - price1[1]) / price1[1]) * 30 + \
                          ((macd_fast2[1] - macd_fast1[1]) / abs(macd_fast1[1]) if macd_fast1[1] != 0 else 1) * 30
                strength = min(0.8, strength)
                info = {'price_low': price2[1], 'price_prev_low': price1[1], 
                        'index': price2[0], 'strength': strength, 'type': 'fast_macd'}
                return True, strength, info
        
        return False, 0, None
    
    def detect_bearish_divergence(self, price_peaks: List, macd_peaks: List,
                                   macd_fast_peaks: List = None) -> Tuple[bool, float, Dict]:
        """检测顶背驰"""
        if len(price_peaks) < 2 or len(macd_peaks) < 2:
            return False, 0, None
        
        price1, price2 = price_peaks[-2], price_peaks[-1]
        macd1, macd2 = macd_peaks[-2], macd_peaks[-1]
        
        if price2[1] > price1[1] and macd2[1] < macd1[1]:
            strength = abs((price2[1] - price1[1]) / price1[1]) * 50 + \
                      abs((macd2[1] - macd1[1]) / abs(macd1[1]) if macd1[1] != 0 else 1) * 50
            strength = min(1.0, strength)
            info = {'price_high': price2[1], 'price_prev_high': price1[1], 
                    'index': price2[0], 'strength': strength}
            return True, strength, info
        
        if macd_fast_peaks and len(macd_fast_peaks) >= 2:
            macd_fast1, macd_fast2 = macd_fast_peaks[-2], macd_fast_peaks[-1]
            if price2[1] > price1[1] and macd_fast2[1] < macd_fast1[1]:
                strength = abs((price2[1] - price1[1]) / price1[1]) * 30 + \
                          abs((macd_fast2[1] - macd_fast1[1]) / abs(macd_fast1[1]) if macd_fast1[1] != 0 else 1) * 30
                strength = min(0.8, strength)
                info = {'price_high': price2[1], 'price_prev_high': price1[1], 
                        'index': price2[0], 'strength': strength, 'type': 'fast_macd'}
                return True, strength, info
        
        return False, 0, None
    
    def check_buy_filter(self, df: pd.DataFrame) -> Tuple[bool, str]:
        """检查买入过滤条件"""
        if not self.enable_buy_filter:
            return True, "无过滤"
        
        latest = df.iloc[-1]
        conditions = []
        
        if latest['rsi'] < self.buy_rsi_threshold:
            conditions.append(f"RSI={latest['rsi']:.1f}<{self.buy_rsi_threshold}")
        if latest['close'] < latest['ema60']:
            conditions.append(f"价格<EMA60")
        if latest.get('volume_ratio', 1) > self.buy_volume_ratio:
            conditions.append(f"放量{latest['volume_ratio']:.2f}倍")
        
        if len(conditions) >= 2:
            return True, "✓ " + ", ".join(conditions)
        return False, "✗ " + ", ".join(conditions) if conditions else "条件不足"
    
    def td_setup(self, df: pd.DataFrame, period: int) -> int:
        """TD序列检测"""
        closes = df["close"].values
        if len(closes) < period + 4:
            return 0
        
        buy = all(closes[-i] <= closes[-i - 4] for i in range(1, period + 1))
        sell = all(closes[-i] >= closes[-i - 4] for i in range(1, period + 1))
        
        if buy:
            return 1
        if sell:
            return -1
        return 0
    
    def get_td_signals(self, timestamp_beijing: datetime) -> Dict[str, int]:
        """获取各周期TD信号"""
        # 找到最接近的时间索引
        def find_nearest_index(df, target_time):
            time_diff = (df['time_beijing'] - target_time).abs()
            return time_diff.idxmin()
        
        idx_1m = find_nearest_index(self.df_1m, timestamp_beijing)
        idx_3m = find_nearest_index(self.df_3m, timestamp_beijing)
        idx_5m = find_nearest_index(self.df_5m, timestamp_beijing)
        idx_15m = find_nearest_index(self.df_15m, timestamp_beijing)
        idx_30m = find_nearest_index(self.df_30m, timestamp_beijing)
        
        # 获取窗口数据
        window_1m = self.df_1m.iloc[max(0, idx_1m-50):idx_1m+1]
        window_3m = self.df_3m.iloc[max(0, idx_3m-50):idx_3m+1]
        window_5m = self.df_5m.iloc[max(0, idx_5m-50):idx_5m+1]
        window_15m = self.df_15m.iloc[max(0, idx_15m-50):idx_15m+1]
        window_30m = self.df_30m.iloc[max(0, idx_30m-50):idx_30m+1]
        
        return {
            '1m': self.td_setup(window_1m, 9) if len(window_1m) >= 50 else 0,
            '3m': self.td_setup(window_3m, 9) if len(window_3m) >= 50 else 0,
            '5m': self.td_setup(window_5m, 9) if len(window_5m) >= 50 else 0,
            '15m': self.td_setup(window_15m, 9) if len(window_15m) >= 50 else 0,
            '30m': self.td_setup(window_30m, 9) if len(window_30m) >= 50 else 0,
        }
    
    def get_reentry_signal(self, td_signals: Dict, position_type: str) -> Tuple[bool, str]:
        """获取加仓信号"""
        if position_type == 'long':
            if td_signals['3m'] == 1:
                return True, "3min"
            if td_signals['5m'] == 1:
                return True, "5min"
        else:
            if td_signals['3m'] == -1:
                return True, "3min"
            if td_signals['5m'] == -1:
                return True, "5min"
        return False, None
    
    def get_initial_add_signal(self, td_signals: Dict, position_type: str) -> bool:
        """获取初始加仓信号"""
        if position_type == 'long':
            return td_signals['15m'] == 1
        else:
            return td_signals['15m'] == -1
    
    def should_clear(self, td_signals: Dict, position_type: str) -> Tuple[bool, str]:
        """检查清仓信号"""
        if position_type == 'long':
            if td_signals['15m'] == -1:
                return True, "15min"
            if self.enable_30min_clear and td_signals['30m'] == -1:
                return True, "30min"
        else:
            if td_signals['15m'] == 1:
                return True, "15min"
            if self.enable_30min_clear and td_signals['30m'] == 1:
                return True, "30min"
        return False, None
    
    def calculate_position_size(self, price: float, stop_loss: float, strength: float) -> float:
        """
        计算仓位大小（USDT）
        """
        try:
            # 更新余额
            self.real_balance = get_total_balance()
            if self.real_balance > 0:
                self.balance = self.real_balance
            
            # 计算风险金额
            risk_amount = self.balance * self.risk_per_trade
            
            # 止损距离
            stop_distance = abs(price - stop_loss)
            if stop_distance <= 0:
                stop_distance = price * 0.01  # 默认1%止损
            
            # 强度乘数
            strength_multiplier = 0.5 + strength
            
            # 计算仓位
            position_value = risk_amount * strength_multiplier / (stop_distance / price)
            
            # 限制最大仓位
            position_value = min(position_value, self.max_position_usdt)
            position_value = max(position_value, self.min_position_usdt)
            
            logger.info(f"仓位计算: 余额=${self.balance:.0f}, "
                       f"风险=${risk_amount:.0f}({self.risk_per_trade*100:.1f}%), "
                       f"止损距离={stop_distance/price:.2%}, "
                       f"强度乘数={strength_multiplier:.2f}, "
                       f"仓位=${position_value:.0f} USDT")
            
            return round(position_value, 2)
            
        except Exception as e:
            logger.error(f"仓位计算错误: {e}", exc_info=True)
            return 0
    
    def get_current_price(self) -> float:
        """获取当前价格"""
        try:
            price = get_mark_price(self.symbol)
            if price == 0:
                logger.error("获取当前价格失败, price is 0")
                return None
            return price
        except Exception as e:
            logger.error(f"获取当前价格失败: {e}")
            return None
    
    def execute_open_long(self, price: float, strength: float, filter_msg: str, 
                          stop_loss: float, td_signals: Dict) -> bool:
        """执行开多仓"""
        try:
            size_usdt = self.calculate_position_size(price, stop_loss, strength)
            
            if size_usdt <= 0:
                logger.warning("仓位计算为0，无法开多仓")
                return False
            
            # 转换为合约张数
            contracts = get_contracts_from_usdt(self.symbol, size_usdt)
            if contracts <= 0:
                logger.error(f"无法转换为合约张数: {size_usdt} USDT")
                return False
            
            logger.info(f"准备开多仓: 价格=${price:.2f}, "
                       f"仓位=${size_usdt:.2f} USDT, "
                       f"张数={contracts}, "
                       f"止损=${stop_loss:.2f}, "
                       f"{filter_msg}")
            
            # 发送开仓通知
            if self.telegram_bot and self.telegram_bot.bot_token:
                self.telegram_bot.send_message(
                    f"📈 准备开多仓\n"
                    f"价格: ${price:.2f}\n"
                    f"仓位: ${size_usdt:.2f} USDT\n"
                    f"张数: {contracts}\n"
                    f"止损: ${stop_loss:.2f}\n"
                    f"过滤条件: {filter_msg}"
                )
            
            # 调用交易所开仓
            result = open_long(self.symbol, size_usdt)
            
            if result and result.get("success"):
                actual_usdt = result.get("actual_usdt", size_usdt)
                actual_contracts = result.get("contracts", contracts)
                fill_price = result.get("fill_price", price)
                
                with self._lock:
                    self.position = PositionState(
                        type='long',
                        entry_price=fill_price,
                        entry_time=datetime.now(),
                        size_usdt=actual_usdt,
                        size_contracts=actual_contracts,
                        remain_size_usdt=actual_usdt,
                        remain_contracts=actual_contracts,
                        stop_loss=stop_loss,
                        highest_price=fill_price,
                        lowest_price=fill_price,
                        tp_signals_triggered=[],
                        initial_added=False,
                        entry_index=len(self.df_15m) - 1,
                        entry_prob=strength,
                        trailing_stop_updated=False
                    )
                    
                    self.trades.append(TradeRecord(
                        action='OPEN_LONG',
                        time=datetime.now(),
                        price=fill_price,
                        size_usdt=actual_usdt,
                        size_contracts=actual_contracts,
                        reason=filter_msg,
                        signal_info={'strength': strength, 'td_signals': td_signals}
                    ))
                
                # 发送成功通知
                if self.telegram_bot and self.telegram_bot.bot_token:
                    self.telegram_bot.send_open_position({
                        'type': 'long',
                        'entry_price': fill_price,
                        'size_usdt': actual_usdt,
                        'stop_loss': stop_loss
                    })
                
                logger.info(f"✅ 开多仓成功: 价格=${fill_price:.2f}, "
                           f"仓位=${actual_usdt:.2f} USDT, 张数={actual_contracts}")
                self.save_config()
                return True
            else:
                logger.error(f"开多仓失败: {result}")
                if self.telegram_bot:
                    self.telegram_bot.send_error_alert(f"开多仓失败: {result}", "ORDER_ERROR")
                return False
                
        except Exception as e:
            logger.error(f"开多仓异常: {e}", exc_info=True)
            if self.telegram_bot:
                self.telegram_bot.send_error_alert(f"开多仓异常: {e}", "ORDER_ERROR")
            return False
    
    def execute_open_short(self, price: float, strength: float, 
                           stop_loss: float, td_signals: Dict) -> bool:
        """执行开空仓"""
        try:
            size_usdt = self.calculate_position_size(price, stop_loss, strength)
            
            if size_usdt <= 0:
                logger.warning("仓位计算为0，无法开空仓")
                return False
            
            contracts = get_contracts_from_usdt(self.symbol, size_usdt)
            if contracts <= 0:
                logger.error(f"无法转换为合约张数: {size_usdt} USDT")
                return False
            
            logger.info(f"准备开空仓: 价格=${price:.2f}, "
                       f"仓位=${size_usdt:.2f} USDT, "
                       f"张数={contracts}, "
                       f"止损=${stop_loss:.2f}")
            
            if self.telegram_bot and self.telegram_bot.bot_token:
                self.telegram_bot.send_message(
                    f"📉 准备开空仓\n"
                    f"价格: ${price:.2f}\n"
                    f"仓位: ${size_usdt:.2f} USDT\n"
                    f"张数: {contracts}\n"
                    f"止损: ${stop_loss:.2f}"
                )
            
            result = open_short(self.symbol, size_usdt)
            
            if result and result.get("success"):
                actual_usdt = result.get("actual_usdt", size_usdt)
                actual_contracts = result.get("contracts", contracts)
                fill_price = result.get("fill_price", price)
                
                with self._lock:
                    self.position = PositionState(
                        type='short',
                        entry_price=fill_price,
                        entry_time=datetime.now(),
                        size_usdt=actual_usdt,
                        size_contracts=actual_contracts,
                        remain_size_usdt=actual_usdt,
                        remain_contracts=actual_contracts,
                        stop_loss=stop_loss,
                        highest_price=fill_price,
                        lowest_price=fill_price,
                        tp_signals_triggered=[],
                        initial_added=False,
                        entry_index=len(self.df_15m) - 1,
                        entry_prob=strength,
                        trailing_stop_updated=False
                    )
                    
                    self.trades.append(TradeRecord(
                        action='OPEN_SHORT',
                        time=datetime.now(),
                        price=fill_price,
                        size_usdt=actual_usdt,
                        size_contracts=actual_contracts,
                        signal_info={'strength': strength, 'td_signals': td_signals}
                    ))
                
                if self.telegram_bot and self.telegram_bot.bot_token:
                    self.telegram_bot.send_open_position({
                        'type': 'short',
                        'entry_price': fill_price,
                        'size_usdt': actual_usdt,
                        'stop_loss': stop_loss
                    })
                
                logger.info(f"✅ 开空仓成功: 价格=${fill_price:.2f}, "
                           f"仓位=${actual_usdt:.2f} USDT, 张数={actual_contracts}")
                self.save_config()
                return True
            else:
                logger.error(f"开空仓失败: {result}")
                if self.telegram_bot:
                    self.telegram_bot.send_error_alert(f"开空仓失败: {result}", "ORDER_ERROR")
                return False
                
        except Exception as e:
            logger.error(f"开空仓异常: {e}", exc_info=True)
            if self.telegram_bot:
                self.telegram_bot.send_error_alert(f"开空仓异常: {e}", "ORDER_ERROR")
            return False
    
    def execute_close_partial(self, current_price: float, close_pct: float, 
                               level: str) -> Tuple[bool, float]:
        """执行部分平仓"""
        if not self.position:
            return False, 0
        
        close_size_usdt = self.position.remain_size_usdt * close_pct
        close_contracts = int(self.position.remain_contracts * close_pct)
        
        if close_size_usdt <= 0 or close_contracts <= 0:
            return False, 0
        
        # 计算盈亏
        if self.position.type == 'long':
            pnl = (current_price - self.position.entry_price) * close_contracts
        else:
            pnl = (self.position.entry_price - current_price) * close_contracts
        
        # 执行平仓
        try:
            if self.position.type == 'long':
                result = close_long(self.symbol, close_size_usdt)
            else:
                result = close_short(self.symbol, close_size_usdt)
            
            if result and result.get("success"):
                with self._lock:
                    self.position.remain_size_usdt -= close_size_usdt
                    self.position.remain_contracts -= close_contracts
                    self.position.tp_signals_triggered.append(level)
                    
                    self.balance += pnl
                    
                    self.trades.append(TradeRecord(
                        action=f'PARTIAL_CLOSE_{level}',
                        time=datetime.now(),
                        price=current_price,
                        size_usdt=close_size_usdt,
                        size_contracts=close_contracts,
                        pnl=pnl,
                        reason=f'{level}TD9减仓{close_pct*100:.0f}%'
                    ))
                
                logger.info(f"🎯 {level}TD9减仓{close_pct*100:.0f}%: "
                           f"盈亏=${pnl:.2f}, 剩余=${self.position.remain_size_usdt:.2f} USDT")
                
                if self.telegram_bot and self.telegram_bot.bot_token:
                    self.telegram_bot.send_message(
                        f"📊 {level}TD9减仓\n"
                        f"方向: {self.position.type.upper()}\n"
                        f"平仓比例: {close_pct*100:.0f}%\n"
                        f"盈亏: ${pnl:.2f}\n"
                        f"剩余仓位: ${self.position.remain_size_usdt:.2f} USDT"
                    )
                
                self.save_config()
                return True, pnl
            else:
                logger.error(f"部分平仓失败: {result}")
                return False, 0
                
        except Exception as e:
            logger.error(f"部分平仓异常: {e}", exc_info=True)
            return False, 0
    
    def execute_full_close(self, current_price: float, reason: str) -> Tuple[bool, float]:
        """执行完全平仓"""
        if not self.position:
            return False, 0
        
        if self.position.type == 'long':
            pnl = (current_price - self.position.entry_price) * self.position.remain_contracts
        else:
            pnl = (self.position.entry_price - current_price) * self.position.remain_contracts
        
        try:
            if self.position.type == 'long':
                result = close_long(self.symbol, self.position.remain_size_usdt)
            else:
                result = close_short(self.symbol, self.position.remain_size_usdt)
            
            if result and result.get("success"):
                with self._lock:
                    self.balance += pnl
                    
                    self.trades.append(TradeRecord(
                        action='FULL_CLOSE',
                        time=datetime.now(),
                        price=current_price,
                        size_usdt=self.position.remain_size_usdt,
                        size_contracts=self.position.remain_contracts,
                        pnl=pnl,
                        reason=reason
                    ))
                    
                    # 更新资金曲线
                    self.equity_curve.append(self.balance)
                    self.equity_timestamps.append(datetime.now())
                    
                    # 更新每日统计
                    self.daily_stats['daily_pnl'] += pnl
                    self.daily_stats['daily_trades_count'] += 1
                    if pnl > 0:
                        self.daily_stats['daily_win_count'] += 1
                    
                    self.position = None
                
                logger.info(f"🏁 完全平仓: {reason}, 盈亏=${pnl:.2f}, 当前资金=${self.balance:.2f}")
                
                if self.telegram_bot and self.telegram_bot.bot_token:
                    close_info = {
                        'type': self.position.type if self.position else 'unknown',
                        'price': current_price,
                        'entry_price': self.position.entry_price if self.position else 0,
                        'size_usdt': self.position.remain_size_usdt if self.position else 0,
                        'pnl': pnl,
                        'reason': reason
                    }
                    self.telegram_bot.send_close_position(close_info)
                
                self.save_config()
                return True, pnl
            else:
                logger.error(f"完全平仓失败: {result}")
                return False, 0
                
        except Exception as e:
            logger.error(f"完全平仓异常: {e}", exc_info=True)
            return False, 0
    
    def update_trailing_stop(self, current_price: float, atr: float) -> bool:
        """更新移动止损"""
        if not self.position:
            return False
        
        updated = False
        
        if self.position.type == 'long':
            # 更新最高价
            if current_price > self.position.highest_price:
                self.position.highest_price = current_price
            
            # 计算当前盈利百分比
            profit_pct = (current_price - self.position.entry_price) / self.position.entry_price
            
            # 只有盈利超过阈值才移动止损
            if profit_pct > self.min_profit_for_trailing:
                # ATR移动止损
                atr_stop = self.position.highest_price - atr * self.trailing_stop_atr
                # 百分比移动止损
                pct_stop = self.position.highest_price * (1 - self.trailing_stop_pct)
                new_stop = max(atr_stop, pct_stop)
                
                if new_stop > self.position.stop_loss:
                    self.position.stop_loss = new_stop
                    self.position.trailing_stop_updated = True
                    updated = True
                    logger.debug(f"多仓移动止损更新: ${new_stop:.2f}, 盈利={profit_pct*100:.2f}%")
        
        else:  # short
            if current_price < self.position.lowest_price:
                self.position.lowest_price = current_price
            
            profit_pct = (self.position.entry_price - current_price) / self.position.entry_price
            
            if profit_pct > self.min_profit_for_trailing:
                atr_stop = self.position.lowest_price + atr * self.trailing_stop_atr
                pct_stop = self.position.lowest_price * (1 + self.trailing_stop_pct)
                new_stop = min(atr_stop, pct_stop)
                
                if new_stop < self.position.stop_loss:
                    self.position.stop_loss = new_stop
                    self.position.trailing_stop_updated = True
                    updated = True
                    logger.debug(f"空仓移动止损更新: ${new_stop:.2f}, 盈利={profit_pct*100:.2f}%")
        
        return updated
    
    def is_in_protection_period(self) -> bool:
        """检查是否在保护期内"""
        if not self.position:
            return False
        
        current_index = len(self.df_15m) - 1
        return (current_index - self.position.entry_index) < self.min_bars_before_action
    
    def is_in_profit(self, current_price: float) -> bool:
        """检查是否浮盈"""
        if not self.position:
            return False
        
        if self.position.type == 'long':
            return current_price > self.position.entry_price
        else:
            return current_price < self.position.entry_price
    
    def check_and_manage_position(self, current_price: float, atr: float, 
                                   td_signals: Dict) -> Dict:
        """检查和管理持仓"""
        if not self.position:
            return {'has_position': False}
        
        in_protection = self.is_in_protection_period()
        in_profit = self.is_in_profit(current_price)
        triggered = self.position.tp_signals_triggered
        
        # 更新移动止损（仅当浮盈且不在保护期）
        if in_profit and not in_protection:
            self.update_trailing_stop(current_price, atr)
        
        # 检查止损（先检查移动止损后的价格）
        if self.position.type == 'long' and current_price <= self.position.stop_loss:
            success, pnl = self.execute_full_close(current_price, "移动止损")
            if success:
                return {'has_position': False, 'closed': True, 'reason': 'trailing_stop', 'pnl': pnl}
        
        elif self.position.type == 'short' and current_price >= self.position.stop_loss:
            success, pnl = self.execute_full_close(current_price, "移动止损")
            if success:
                return {'has_position': False, 'closed': True, 'reason': 'trailing_stop', 'pnl': pnl}
        
        # 初始加仓逻辑
        if not self.position.initial_added and not triggered and not in_protection and in_profit:
            if self.get_initial_add_signal(td_signals, self.position.type):
                add_size_usdt = self.position.size_usdt * self.initial_add_size
                add_contracts = int(self.position.size_contracts * self.initial_add_size)
                
                if add_size_usdt > 0 and add_contracts > 0:
                    with self._lock:
                        self.position.remain_size_usdt += add_size_usdt
                        self.position.remain_contracts += add_contracts
                        self.position.initial_added = True
                    
                    direction = "多" if self.position.type == 'long' else "空"
                    logger.info(f"📈 初始加仓 ({direction}): +{add_size_usdt:.2f} USDT, 触发=15分钟TD9")
                    
                    if self.telegram_bot:
                        self.telegram_bot.send_message(
                            f"📈 初始加仓 ({direction})\n"
                            f"加仓金额: ${add_size_usdt:.2f}\n"
                            f"新增张数: {add_contracts}\n"
                            f"总仓位: ${self.position.remain_size_usdt:.2f} USDT"
                        )
                    
                    self.save_config()
        
        # 多级别减仓
        if not in_protection and in_profit:
            # 定义减仓条件
            if self.position.type == 'long':
                cond = {
                    '1min': td_signals['1m'] == -1,
                    '3min': td_signals['3m'] == -1,
                    '5min': td_signals['5m'] == -1,
                }
            else:
                cond = {
                    '1min': td_signals['1m'] == 1,
                    '3min': td_signals['3m'] == 1,
                    '5min': td_signals['5m'] == 1,
                }
            
            # 1分钟减仓
            if cond['1min'] and '1min' not in triggered:
                success, pnl = self.execute_close_partial(current_price, self.tp_ratios['1min'], '1min')
                if success:
                    triggered.append('1min')
            
            # 3分钟减仓
            if cond['3min'] and '3min' not in triggered:
                success, pnl = self.execute_close_partial(current_price, self.tp_ratios['3min'], '3min')
                if success:
                    triggered.append('3min')
            
            # 5分钟减仓
            if cond['5min'] and '5min' not in triggered:
                success, pnl = self.execute_close_partial(current_price, self.tp_ratios['5min'], '5min')
                if success:
                    triggered.append('5min')
        
        # 减仓后的加仓逻辑
        if triggered and not in_protection and in_profit:
            reentry, source = self.get_reentry_signal(td_signals, self.position.type)
            if reentry:
                total_closed_pct = sum(self.tp_ratios.get(s, 0) for s in triggered if s in self.tp_ratios)
                target_size_usdt = self.position.size_usdt * (1 - total_closed_pct)
                target_contracts = int(self.position.size_contracts * (1 - total_closed_pct))
                
                if self.position.remain_size_usdt < target_size_usdt:
                    add_size_usdt = min(target_size_usdt - self.position.remain_size_usdt,
                                       self.position.size_usdt * 0.3)
                    add_contracts = int(self.position.size_contracts * 0.3)
                    
                    if add_size_usdt > 0 and add_contracts > 0:
                        with self._lock:
                            self.position.remain_size_usdt += add_size_usdt
                            self.position.remain_contracts += add_contracts
                            self.position.tp_signals_triggered = []
                        
                        direction = "多" if self.position.type == 'long' else "空"
                        logger.info(f"🔄 减仓后加仓 ({direction}): +{add_size_usdt:.2f} USDT, 触发={source}")
                        
                        if self.telegram_bot:
                            self.telegram_bot.send_message(
                                f"🔄 减仓后加仓 ({direction})\n"
                                f"加仓金额: ${add_size_usdt:.2f}\n"
                                f"新张数: {add_contracts}"
                            )
                        
                        self.save_config()
        
        # TD清仓逻辑
        if not in_protection:
            clear, source = self.should_clear(td_signals, self.position.type)
            if clear:
                success, pnl = self.execute_full_close(current_price, f"{source}TD9清仓")
                if success:
                    return {'has_position': False, 'closed': True, 'reason': source, 'pnl': pnl}
        
        # 更新持仓中的触发记录
        if self.position:
            self.position.tp_signals_triggered = triggered
        
        return {'has_position': True, 'position_type': self.position.type,
                'entry_price': self.position.entry_price, 'current_price': current_price,
                'unrealized_pnl': self.calculate_unrealized_pnl(current_price),
                'stop_loss': self.position.stop_loss}
    
    def calculate_unrealized_pnl(self, current_price: float) -> float:
        """计算未实现盈亏"""
        if not self.position:
            return 0
        
        if self.position.type == 'long':
            return (current_price - self.position.entry_price) * self.position.remain_contracts
        else:
            return (self.position.entry_price - current_price) * self.position.remain_contracts
    
    def generate_signal(self, current_bar: pd.Series, i: int) -> Dict:
        """生成交易信号"""
        # 获取窗口数据
        window = self.df_15m.iloc[max(0, i-200):i+1]
        
        # 寻找极值点
        price_peaks, price_troughs = self.find_local_extremes(window['close'].values)
        macd_peaks, macd_troughs = self.find_local_extremes(window['macd'].values)
        macd_fast_peaks, macd_fast_troughs = self.find_local_extremes(window['macd_fast'].values)
        
        # 检测背驰
        bullish_div, bullish_strength, bullish_info = self.detect_bullish_divergence(
            price_troughs, macd_troughs, macd_fast_troughs
        )
        bearish_div, bearish_strength, bearish_info = self.detect_bearish_divergence(
            price_peaks, macd_peaks, macd_fast_peaks
        )
        
        # 获取TD信号
        td_signals = self.get_td_signals(current_bar['time_beijing'])
        
        return {
            'bullish': bullish_div,
            'bullish_strength': bullish_strength,
            'bullish_info': bullish_info,
            'bearish': bearish_div,
            'bearish_strength': bearish_strength,
            'bearish_info': bearish_info,
            'td_signals': td_signals
        }
    
    def run_once(self):
        """运行一次交易循环"""
        try:
            # 更新数据
            if not self.update_all_data():
                logger.error("数据更新失败，跳过本次循环")
                return
            
            current_price = self.get_current_price()
            if current_price is None:
                logger.error("获取当前价格失败")
                return
            
            # 获取最新K线
            latest_bar = self.df_15m.iloc[-1]
            current_index = len(self.df_15m) - 1
            atr = latest_bar['atr']
            
            # 生成信号
            signals = self.generate_signal(latest_bar, current_index)
            td_signals = signals['td_signals']
            
            # 检查并管理持仓
            if self.position:
                position_status = self.check_and_manage_position(
                    current_price, atr, td_signals
                )
                
                if position_status.get('has_position', False):
                    unrealized_pnl = self.calculate_unrealized_pnl(current_price)
                    logger.info(f"持仓状态: {self.position.type}仓, "
                               f"开仓价=${self.position.entry_price:.2f}, "
                               f"当前价=${current_price:.2f}, "
                               f"未实现盈亏=${unrealized_pnl:.2f}, "
                               f"止损=${self.position.stop_loss:.2f}")
            
            # 如果没有持仓，检查开仓信号
            else:
                # 检查做多信号
                if signals['bullish'] and signals['bullish_strength'] >= self.min_divergence_strength:
                    if signals['bullish_info'] and signals['bullish_info']['index'] != self.last_bullish_index:
                        filter_passed, filter_msg = self.check_buy_filter(self.df_15m)
                        
                        if filter_passed:
                            stop_loss = signals['bullish_info']['price_low'] - atr * 1.5
                            self.execute_open_long(
                                current_price, signals['bullish_strength'], 
                                filter_msg, stop_loss, td_signals
                            )
                            self.last_bullish_index = signals['bullish_info']['index']
                        else:
                            logger.info(f"做多被过滤: {filter_msg}")
                
                # 检查做空信号
                elif signals['bearish'] and signals['bearish_strength'] >= self.min_divergence_strength:
                    if signals['bearish_info'] and signals['bearish_info']['index'] != self.last_bearish_index:
                        stop_loss = signals['bearish_info']['price_high'] + atr * 1.5
                        self.execute_open_short(
                            current_price, signals['bearish_strength'], 
                            stop_loss, td_signals
                        )
                        self.last_bearish_index = signals['bearish_info']['index']
                
                else:
                    # 打印状态
                    logger.debug(f"无持仓, 价格=${current_price:.2f}, "
                                f"做多背驰={signals['bullish']}({signals['bullish_strength']:.2f}), "
                                f"做空背驰={signals['bearish']}({signals['bearish_strength']:.2f})")
            
            # 保存配置
            self.save_config()
            
        except Exception as e:
            logger.error(f"交易循环执行错误: {e}", exc_info=True)
            if self.telegram_bot:
                self.telegram_bot.send_error_alert(f"交易循环错误: {e}", "LOOP_ERROR")
    
    def send_daily_report(self):
        """发送每日统计报告"""
        if not self.telegram_bot or not self.telegram_bot.bot_token:
            return
        
        closed_trades = [t for t in self.trades if t.pnl != 0]
        
        total_pnl = sum(t.pnl for t in closed_trades)
        total_return = (self.balance - self.initial_balance) / self.initial_balance * 100
        
        winning_trades = [t for t in closed_trades if t.pnl > 0]
        total_win_rate = len(winning_trades) / len(closed_trades) * 100 if closed_trades else 0
        
        daily_win_rate = (self.daily_stats['daily_win_count'] / self.daily_stats['daily_trades_count'] * 100 
                          if self.daily_stats['daily_trades_count'] > 0 else 0)
        
        # 计算最大回撤
        peak = self.equity_curve[0]
        max_dd = 0
        for value in self.equity_curve:
            if value > peak:
                peak = value
            dd = (peak - value) / peak * 100 if peak > 0 else 0
            max_dd = max(max_dd, dd)
        
        runtime = datetime.now() - self.start_time if self.start_time else timedelta(0)
        
        stats = {
            'initial_balance': self.initial_balance,
            'current_balance': self.balance,
            'daily_pnl': self.daily_stats['daily_pnl'],
            'daily_return': (self.daily_stats['daily_pnl'] / self.daily_stats['initial_balance'] * 100
                            if self.daily_stats['initial_balance'] > 0 else 0),
            'total_return': total_return,
            'daily_trades': self.daily_stats['daily_trades_count'],
            'total_trades': len(closed_trades),
            'daily_win_rate': daily_win_rate,
            'total_win_rate': total_win_rate,
            'max_drawdown': max_dd,
            'runtime': str(runtime).split('.')[0]
        }
        
        self.telegram_bot.send_daily_report(stats)
        logger.info("每日统计报告已发送")
    
    def start(self):
        """启动实盘交易"""
        logger.info("="*70)
        logger.info("启动实盘MACD背驰+TD信号策略V6")
        logger.info("="*70)
        
        self.running = True
        self.start_time = datetime.now()
        
        # 发送启动通知
        if self.telegram_bot and self.telegram_bot.bot_token:
            start_msg = (
                f"🚀 MACD背驰+TD信号策略V6已启动\n\n"
                f"交易对: {self.symbol}\n"
                f"初始资金: ${self.initial_balance:,.2f}\n"
                f"单笔风险: {self.risk_per_trade*100}%\n"
                f"移动止损: {self.trailing_stop_pct*100}% / {self.trailing_stop_atr}倍ATR\n"
                f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )
            self.telegram_bot.send_message(start_msg)
        
        try:
            while self.running:
                if self.paused:
                    logger.info("系统已暂停，等待恢复...")
                    time.sleep(10)
                    continue
                
                logger.info(f"\n{'='*50}")
                logger.info(f"交易循环开始 - {datetime.now()}")
                
                self.run_once()
                
                # 检查是否需要发送每日报告
                now = datetime.now()
                today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
                
                if self.last_daily_report.date() != today_start.date():
                    self.send_daily_report()
                    self.last_daily_report = today_start
                    self.daily_stats = {
                        'initial_balance': self.balance,
                        'daily_pnl': 0,
                        'daily_trades_count': 0,
                        'daily_win_count': 0
                    }
                
                logger.info(f"等待 {self.check_interval} 秒...")
                for _ in range(self.check_interval):
                    if not self.running:
                        break
                    time.sleep(1)
                
        except KeyboardInterrupt:
            logger.info("收到中断信号，正在停止...")
        finally:
            self.stop()
    
    def stop(self):
        """停止交易系统"""
        logger.info("正在停止交易系统...")
        self.running = False
        
        if self.telegram_bot and self.telegram_bot.bot_token:
            stop_msg = (
                f"🛑 交易系统已停止\n\n"
                f"最终资金: ${self.balance:,.2f}\n"
                f"总收益率: {(self.balance - self.initial_balance) / self.initial_balance * 100:.2f}%\n"
                f"运行时间: {datetime.now() - self.start_time if self.start_time else 'N/A'}"
            )
            self.telegram_bot.send_message(stop_msg)
        
        if self.position:
            logger.warning(f"系统停止时仍有持仓: {self.position}")
            logger.warning("请手动处理持仓或重新启动系统")
        
        self.save_config()
        self.print_statistics()
        logger.info("交易系统已停止")
    
    def pause(self):
        """暂停交易"""
        logger.info("交易系统已暂停")
        self.paused = True
        if self.telegram_bot:
            self.telegram_bot.send_message("⏸️ 交易系统已暂停")
    
    def resume(self):
        """恢复交易"""
        logger.info("交易系统已恢复")
        self.paused = False
        if self.telegram_bot:
            self.telegram_bot.send_message("▶️ 交易系统已恢复")
    
    def print_statistics(self):
        """打印交易统计信息"""
        logger.info("\n" + "="*70)
        logger.info("交易统计报告")
        logger.info("="*70)
        
        closed_trades = [t for t in self.trades if t.pnl != 0]
        
        if not closed_trades:
            logger.info("暂无已平仓交易")
            return
        
        total_pnl = sum(t.pnl for t in closed_trades)
        total_return = (self.balance - self.initial_balance) / self.initial_balance * 100
        
        winning_trades = [t for t in closed_trades if t.pnl > 0]
        win_rate = len(winning_trades) / len(closed_trades) * 100
        
        avg_win = np.mean([t.pnl for t in winning_trades]) if winning_trades else 0
        losing_trades = [t for t in closed_trades if t.pnl <= 0]
        avg_loss = abs(np.mean([t.pnl for t in losing_trades])) if losing_trades else 1
        profit_factor = avg_win / avg_loss if avg_loss != 0 else 0
        
        # 计算最大回撤
        peak = self.equity_curve[0]
        max_dd = 0
        for value in self.equity_curve:
            if value > peak:
                peak = value
            dd = (peak - value) / peak * 100 if peak > 0 else 0
            max_dd = max(max_dd, dd)
        
        logger.info(f"初始资金: ${self.initial_balance:>15,.2f}")
        logger.info(f"当前资金: ${self.balance:>15,.2f}")
        logger.info(f"总收益率: {total_return:>14.2f}%")
        logger.info(f"总盈亏: ${total_pnl:>16,.2f}")
        logger.info("-" * 70)
        logger.info(f"总交易次数: {len(closed_trades):>13}")
        logger.info(f"盈利次数: {len(winning_trades):>13}")
        logger.info(f"亏损次数: {len(losing_trades):>13}")
        logger.info(f"胜率: {win_rate:>17.2f}%")
        logger.info(f"平均盈利: ${avg_win:>13,.2f}")
        logger.info(f"平均亏损: ${avg_loss:>13,.2f}")
        logger.info(f"盈亏比: {profit_factor:>16.2f}")
        logger.info(f"最大回撤: {max_dd:>14.2f}%")
        logger.info("="*70)


# ==============================
# 主函数
# ==============================
def main():
    """主函数"""
    print("="*70)
    print("ETHUSDT 实盘交易系统 - MACD背驰 + TD信号组合策略V6")
    print("="*70)
    print("\n注意事项:")
    print("1. 本系统已集成Telegram通知功能")
    print("2. 支持多级别仓位管理（1m/3m/5m TD9减仓）")
    print("3. 支持移动止损保护利润")
    print("4. 支持进程重启恢复（自动保存状态）")
    print("5. 建议先在模拟环境测试")
    print("\n策略特点:")
    print("✅ MACD底背驰/顶背驰检测")
    print("✅ 多周期TD9信号（1m/3m/5m/15m/30m）")
    print("✅ 分级别止盈（25%+20%+25%）")
    print("✅ 移动止损（ATR+百分比）")
    print("✅ 初始加仓 + 减仓后加仓")
    print("\n提示: 按 Ctrl+C 停止交易（会自动保存状态）")
    print("="*70)
    
    # 配置参数
    config = {
        "symbol": "ETHUSDT",
        "interval": "15m",
        "initial_balance": None,  # None表示从交易所获取
        "risk_per_trade": 0.02,   # 单笔风险2%
        "min_divergence_strength": 0.25,
        "enable_buy_filter": True,
        "buy_rsi_threshold": 40,
        "buy_volume_ratio": 0.8,
        "enable_30min_clear": True,
        "min_bars_before_action": 1,
        "initial_add_size": 0.3,
        "trailing_stop_atr": 2.0,
        "trailing_stop_pct": 0.05,
        "min_profit_for_trailing": 0.01,
        "tp_ratios": {
            '1min': 0.25,
            '3min': 0.20,
            '5min': 0.25,
        },
        "klines_limit": 500,
        "check_interval": 2,  # 60秒检查一次
        "max_position_usdt": None,
        "min_position_usdt": None,
        "max_leverage": 2.0
    }
    
    # 初始化Telegram
    init_telegram(symbol="ETH")
    print("✅ Telegram Bot已配置")
    
    # 创建交易引擎
    engine = LiveMACDTDStrategyV6(**config)
    
    # 启动交易
    try:
        engine.start()
    except KeyboardInterrupt:
        engine.stop()
    except Exception as e:
        logger.error(f"系统错误: {e}", exc_info=True)
        if engine.telegram_bot:
            engine.telegram_bot.send_error_alert(f"系统致命错误: {e}", "FATAL_ERROR")
        engine.stop()


if __name__ == "__main__":
    main()