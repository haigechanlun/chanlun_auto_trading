import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(__file__)))


import pandas as pd
import numpy as np
import talib
from datetime import datetime, timedelta, timezone
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import warnings
warnings.filterwarnings('ignore')
from data.binance_api import get_klines


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


class OptimizedMACDTDStrategyV6:
    """
    优化版MACD背驰 + TD信号组合策略V6
    多级别仓位管理 + 移动止损
    
    新增功能：
    - 移动止损（追踪止损）保护利润
    - 分级别移动止损：减仓前/后不同参数
    """
    
    def __init__(self, df_15m, df_5m, df_3m, df_1m, df_30m, initial_balance=100000, 
                 risk_per_trade=0.02, min_divergence_strength=0.25,
                 enable_buy_filter=True, buy_rsi_threshold=40, 
                 buy_volume_ratio=0.8,
                 enable_30min_clear=True,
                 min_bars_before_action=1,
                 initial_add_size=0.3,
                 trailing_stop_atr=1.5,      # 新增：移动止损ATR倍数
                 trailing_stop_pct=0.02):     # 新增：移动止损百分比（2%）
        """
        初始化回测引擎
        
        Args:
            trailing_stop_atr: 移动止损ATR倍数
            trailing_stop_pct: 移动止损百分比（从最高点回撤）
        """
        self.df_15m = df_15m.copy()
        self.df_5m = df_5m.copy()
        self.df_3m = df_3m.copy()
        self.df_1m = df_1m.copy()
        self.df_30m = df_30m.copy()
        self.initial_balance = initial_balance
        self.balance = initial_balance
        self.risk_per_trade = risk_per_trade
        self.min_divergence_strength = min_divergence_strength
        self.enable_30min_clear = enable_30min_clear
        self.min_bars_before_action = min_bars_before_action
        self.initial_add_size = initial_add_size
        self.trailing_stop_atr = trailing_stop_atr
        self.trailing_stop_pct = trailing_stop_pct
        
        # 仓位管理比例
        self.tp_ratios = {
            '1min': 0.25,
            '3min': 0.20,
            '5min': 0.25,
        }
        
        # 优化参数
        self.enable_buy_filter = enable_buy_filter
        self.buy_rsi_threshold = buy_rsi_threshold
        self.buy_volume_ratio = buy_volume_ratio
        
        # 交易记录
        self.trades = []
        self.equity_curve = [initial_balance]
        self.equity_timestamps = []
        
        # 统计
        self.total_signals = 0
        self.buy_signals = 0
        self.sell_signals = 0
        self.filtered_buy_signals = 0
        self.confirmed_trades = 0
        
        # TD信号触发统计
        self.td1_trigger_count = 0
        self.td3_trigger_count = 0
        self.td5_trigger_count = 0
        self.td15_clear_count = 0
        self.td30_clear_count = 0
        self.reentry_trigger_count = 0
        self.initial_add_count = 0
        self.trailing_stop_trigger_count = 0  # 新增：移动止损触发次数
        self.skipped_actions = 0
        
        # 准备数据
        self.prepare_data()
        
    def prepare_data(self):
        """准备数据，转换时间为北京时间"""
        print("正在计算技术指标...")
        
        for df in [self.df_15m, self.df_5m, self.df_3m, self.df_1m, self.df_30m]:
            if 'timestamp' in df.columns:
                df.rename(columns={'timestamp': 'time'}, inplace=True)
            df['time'] = pd.to_datetime(df['time'])
            df['time_beijing'] = df['time'].apply(utc_to_beijing)
        
        self.df_15m = self.calculate_indicators(self.df_15m).dropna().reset_index(drop=True)
        self.df_5m = self.calculate_indicators(self.df_5m).dropna().reset_index(drop=True)
        self.df_3m = self.calculate_indicators(self.df_3m).dropna().reset_index(drop=True)
        self.df_1m = self.calculate_indicators(self.df_1m).dropna().reset_index(drop=True)
        self.df_30m = self.calculate_indicators(self.df_30m).dropna().reset_index(drop=True)
        
        self.df_15m['volume_ma20'] = self.df_15m['volume'].rolling(20).mean()
        self.df_15m['volume_ratio'] = self.df_15m['volume'] / self.df_15m['volume_ma20']
        
        print(f"15分钟数据: {len(self.df_15m)} 根")
        print(f"5分钟数据: {len(self.df_5m)} 根")
        print(f"3分钟数据: {len(self.df_3m)} 根")
        print(f"1分钟数据: {len(self.df_1m)} 根")
        print(f"30分钟数据: {len(self.df_30m)} 根")
        print(f"\n新增功能: 移动止损")
        print(f"  - ATR移动止损: {self.trailing_stop_atr}倍ATR")
        print(f"  - 百分比移动止损: {self.trailing_stop_pct*100}%")
        
    def calculate_indicators(self, df):
        df = df.copy()
        df['macd'], df['macd_signal'], df['macd_hist'] = talib.MACD(
            df['close'], fastperiod=12, slowperiod=26, signalperiod=9
        )
        df['macd_fast'], _, _ = talib.MACD(
            df['close'], fastperiod=8, slowperiod=17, signalperiod=6
        )
        df['atr'] = talib.ATR(df['high'], df['low'], df['close'], timeperiod=14)
        df['ema20'] = talib.EMA(df['close'], timeperiod=20)
        df['ema60'] = talib.EMA(df['close'], timeperiod=60)
        df['rsi'] = talib.RSI(df['close'], timeperiod=14)
        
        df['trend'] = 'sideways'
        df.loc[df['ema20'] > df['ema60'], 'trend'] = 'up'
        df.loc[df['ema20'] < df['ema60'], 'trend'] = 'down'
        
        df['macd_status'] = 'neutral'
        df.loc[df['macd_hist'] > 0, 'macd_status'] = 'bullish'
        df.loc[df['macd_hist'] < 0, 'macd_status'] = 'bearish'
        df.loc[(df['macd_hist'] > 0) & (df['macd_hist'] > df['macd_hist'].shift(1)), 'macd_status'] = 'bullish_strong'
        df.loc[(df['macd_hist'] < 0) & (df['macd_hist'] < df['macd_hist'].shift(1)), 'macd_status'] = 'bearish_strong'
        
        return df
    
    def find_local_extremes(self, data, window=3):
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
    
    def detect_bullish_divergence(self, price_troughs, macd_troughs, macd_fast_troughs=None):
        if len(price_troughs) < 2 or len(macd_troughs) < 2:
            return False, 0, None
        
        price1, price2 = price_troughs[-2], price_troughs[-1]
        macd1, macd2 = macd_troughs[-2], macd_troughs[-1]
        
        if price2[1] < price1[1] and macd2[1] > macd1[1]:
            strength = abs((price2[1] - price1[1]) / price1[1]) * 50 + \
                      ((macd2[1] - macd1[1]) / abs(macd1[1]) if macd1[1] != 0 else 1) * 50
            strength = min(1.0, strength)
            info = {'price_low': price2[1], 'price_prev_low': price1[1], 'index': price2[0], 'strength': strength}
            return True, strength, info
        
        if macd_fast_troughs and len(macd_fast_troughs) >= 2:
            macd_fast1, macd_fast2 = macd_fast_troughs[-2], macd_fast_troughs[-1]
            if price2[1] < price1[1] and macd_fast2[1] > macd_fast1[1]:
                strength = abs((price2[1] - price1[1]) / price1[1]) * 30 + \
                          ((macd_fast2[1] - macd_fast1[1]) / abs(macd_fast1[1]) if macd_fast1[1] != 0 else 1) * 30
                strength = min(0.8, strength)
                info = {'price_low': price2[1], 'price_prev_low': price1[1], 'index': price2[0], 'strength': strength, 'type': 'fast_macd'}
                return True, strength, info
        
        return False, 0, None
    
    def detect_bearish_divergence(self, price_peaks, macd_peaks, macd_fast_peaks=None):
        if len(price_peaks) < 2 or len(macd_peaks) < 2:
            return False, 0, None
        
        price1, price2 = price_peaks[-2], price_peaks[-1]
        macd1, macd2 = macd_peaks[-2], macd_peaks[-1]
        
        if price2[1] > price1[1] and macd2[1] < macd1[1]:
            strength = abs((price2[1] - price1[1]) / price1[1]) * 50 + \
                      abs((macd2[1] - macd1[1]) / abs(macd1[1]) if macd1[1] != 0 else 1) * 50
            strength = min(1.0, strength)
            info = {'price_high': price2[1], 'price_prev_high': price1[1], 'index': price2[0], 'strength': strength}
            return True, strength, info
        
        if macd_fast_peaks and len(macd_fast_peaks) >= 2:
            macd_fast1, macd_fast2 = macd_fast_peaks[-2], macd_fast_peaks[-1]
            if price2[1] > price1[1] and macd_fast2[1] < macd_fast1[1]:
                strength = abs((price2[1] - price1[1]) / price1[1]) * 30 + \
                          abs((macd_fast2[1] - macd_fast1[1]) / abs(macd_fast1[1]) if macd_fast1[1] != 0 else 1) * 30
                strength = min(0.8, strength)
                info = {'price_high': price2[1], 'price_prev_high': price1[1], 'index': price2[0], 'strength': strength, 'type': 'fast_macd'}
                return True, strength, info
        
        return False, 0, None
    
    def check_buy_filter(self, df):
        if not self.enable_buy_filter:
            return True, "无过滤"
        
        latest = df.iloc[-1]
        conditions = []
        
        if latest['rsi'] < self.buy_rsi_threshold:
            conditions.append(f"RSI={latest['rsi']:.1f}<{self.buy_rsi_threshold}")
        if latest['close'] < latest['ema60']:
            conditions.append(f"价格={latest['close']:.2f}<EMA60={latest['ema60']:.2f}")
        if latest['volume_ratio'] > self.buy_volume_ratio:
            conditions.append(f"放量{latest['volume_ratio']:.2f}倍")
        
        if len(conditions) >= 2:
            return True, "✓ " + ", ".join(conditions)
        return False, "✗ " + ", ".join(conditions) if conditions else "条件不足"
    
    def td_setup(self, df, period):
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
    
    def get_td_signals(self, idx_1m, idx_3m, idx_5m, idx_15m, idx_30m):
        window_1m = self.df_1m.iloc[max(0, idx_1m-50):idx_1m+1]
        td9_1m = self.td_setup(window_1m, 9) if len(window_1m) >= 50 else 0
        
        window_3m = self.df_3m.iloc[max(0, idx_3m-50):idx_3m+1]
        td9_3m = self.td_setup(window_3m, 9) if len(window_3m) >= 50 else 0
        
        window_5m = self.df_5m.iloc[max(0, idx_5m-50):idx_5m+1]
        td9_5m = self.td_setup(window_5m, 9) if len(window_5m) >= 50 else 0
        
        window_15m = self.df_15m.iloc[max(0, idx_15m-50):idx_15m+1]
        td9_15m = self.td_setup(window_15m, 9) if len(window_15m) >= 50 else 0
        
        window_30m = self.df_30m.iloc[max(0, idx_30m-50):idx_30m+1]
        td9_30m = self.td_setup(window_30m, 9) if len(window_30m) >= 50 else 0
        
        return td9_1m, td9_3m, td9_5m, td9_15m, td9_30m
    
    def get_reentry_signal(self, td9_3m, td9_5m, position_type):
        if position_type == 'long':
            if td9_3m == 1:
                return True, "3min"
            if td9_5m == 1:
                return True, "5min"
        else:
            if td9_3m == -1:
                return True, "3min"
            if td9_5m == -1:
                return True, "5min"
        return False, None
    
    def get_initial_add_signal(self, td9_15m, position_type):
        if position_type == 'long':
            return td9_15m == 1
        else:
            return td9_15m == -1
    
    def should_clear(self, td9_15m, td9_30m, position_type):
        if position_type == 'long':
            if td9_15m == -1:
                return True, "15min"
            if self.enable_30min_clear and td9_30m == -1:
                return True, "30min"
        else:
            if td9_15m == 1:
                return True, "15min"
            if self.enable_30min_clear and td9_30m == 1:
                return True, "30min"
        return False, None
    
    def update_trailing_stop(self, position, current_price, atr, highest_price, lowest_price):
        """
        更新移动止损
        
        Args:
            position: 持仓对象
            current_price: 当前价格
            atr: ATR值
            highest_price: 持仓以来的最高价（多仓）
            lowest_price: 持仓以来的最低价（空仓）
        """
        if position['type'] == 'long':
            # 多仓移动止损：从最高点回撤
            # 方法1: 基于ATR
            trailing_atr_stop = highest_price - atr * self.trailing_stop_atr
            # 方法2: 基于百分比
            trailing_pct_stop = highest_price * (1 - self.trailing_stop_pct)
            # 取两者中较高的（更保守的止损）
            new_stop = max(trailing_atr_stop, trailing_pct_stop)
            
            # 只向上移动止损
            if new_stop > position.get('stop_loss', 0):
                position['stop_loss'] = new_stop
                return True
                
        else:  # short
            # 空仓移动止损：从最低点反弹
            trailing_atr_stop = lowest_price + atr * self.trailing_stop_atr
            trailing_pct_stop = lowest_price * (1 + self.trailing_stop_pct)
            # 取两者中较低的（更保守的止损）
            new_stop = min(trailing_atr_stop, trailing_pct_stop)
            
            # 只向下移动止损
            if new_stop < position.get('stop_loss', float('inf')):
                position['stop_loss'] = new_stop
                return True
                
        return False
    
    def is_in_protection_period(self, entry_index, current_index):
        return (current_index - entry_index) < self.min_bars_before_action
    
    def is_in_profit(self, position_type, current_price, entry_price):
        if position_type == 'long':
            return current_price > entry_price
        else:
            return current_price < entry_price
    
    def calculate_position_size(self, price, stop_loss, balance, strength):
        risk_amount = balance * self.risk_per_trade
        stop_distance = abs(price - stop_loss)
        if stop_distance == 0:
            return 0
        strength_multiplier = 0.5 + strength
        position_value = risk_amount * strength_multiplier / (stop_distance / price)
        size = position_value / price
        max_size = (balance * 0.2) / price
        size = min(size, max_size)
        return round(size, 4)
    
    def run(self):
        print("\n开始回测...")
        print("="*60)
        print("新增功能: 移动止损（追踪止损）")
        print("  - 从最高点回撤{:.0f}%或{}倍ATR触发".format(self.trailing_stop_pct*100, self.trailing_stop_atr))
        print("="*60)
        
        position = None
        last_bullish_index = -1
        last_bearish_index = -1
        total_bars_15m = len(self.df_15m)
        self.equity_timestamps.append(self.df_15m['time_beijing'].iloc[0])
        
        for i in range(100, total_bars_15m):
            if i % (total_bars_15m // 10) == 0 and i > 0:
                progress = i / total_bars_15m * 100
                print(f"回测进度: {progress:.1f}%...")
            
            current_bar = self.df_15m.iloc[i]
            price = current_bar['close']
            high = current_bar['high']
            low = current_bar['low']
            timestamp_beijing = current_bar['time_beijing']
            atr = current_bar['atr']
            
            window = self.df_15m.iloc[max(0, i-200):i+1]
            price_peaks, price_troughs = self.find_local_extremes(window['close'].values)
            macd_peaks, macd_troughs = self.find_local_extremes(window['macd'].values)
            macd_fast_peaks, macd_fast_troughs = self.find_local_extremes(window['macd_fast'].values)
            
            bullish_div, bullish_strength, bullish_info = self.detect_bullish_divergence(
                price_troughs, macd_troughs, macd_fast_troughs
            )
            bearish_div, bearish_strength, bearish_info = self.detect_bearish_divergence(
                price_peaks, macd_peaks, macd_fast_peaks
            )
            
            time_diff_1m = (self.df_1m['time_beijing'] - timestamp_beijing).abs()
            idx_1m = time_diff_1m.idxmin()
            time_diff_3m = (self.df_3m['time_beijing'] - timestamp_beijing).abs()
            idx_3m = time_diff_3m.idxmin()
            time_diff_5m = (self.df_5m['time_beijing'] - timestamp_beijing).abs()
            idx_5m = time_diff_5m.idxmin()
            time_diff_30m = (self.df_30m['time_beijing'] - timestamp_beijing).abs()
            idx_30m = time_diff_30m.idxmin()
            
            td9_1m, td9_3m, td9_5m, td9_15m, td9_30m = self.get_td_signals(idx_1m, idx_3m, idx_5m, i, idx_30m)
            
            # ========== 开仓逻辑 ==========
            if position is None:
                if bullish_div and bullish_strength >= self.min_divergence_strength:
                    if bullish_info and bullish_info['index'] != last_bullish_index:
                        self.total_signals += 1
                        self.buy_signals += 1
                        filter_passed, filter_msg = self.check_buy_filter(self.df_15m.iloc[:i+1])
                        
                        if filter_passed:
                            stop_loss = bullish_info['price_low'] - atr * 1.5
                            size = self.calculate_position_size(price, stop_loss, self.balance, bullish_strength)
                            
                            if size > 0:
                                position = {
                                    'type': 'long',
                                    'entry_price': price,
                                    'entry_index': i,
                                    'entry_time': timestamp_beijing,
                                    'size': size,
                                    'remain_size': size,
                                    'stop_loss': stop_loss,
                                    'highest_price': price,  # 记录最高价
                                    'tp_signals_triggered': [],
                                    'initial_added': False,
                                }
                                last_bullish_index = bullish_info['index']
                                self.confirmed_trades += 1
                                self.trades.append({
                                    'time': timestamp_beijing, 'type': 'long', 'action': 'OPEN',
                                    'price': price, 'size': size, 'filter': filter_msg
                                })
                                print(f"\n[{timestamp_beijing}] 🟢 开多仓: 价格={price:.2f}, 数量={size:.4f}, {filter_msg}")
                        else:
                            self.filtered_buy_signals += 1
                            print(f"[{timestamp_beijing}] ⚠️ 做多被过滤: {filter_msg}")
                
                elif bearish_div and bearish_strength >= self.min_divergence_strength:
                    if bearish_info and bearish_info['index'] != last_bearish_index:
                        self.total_signals += 1
                        self.sell_signals += 1
                        stop_loss = bearish_info['price_high'] + atr * 1.5
                        size = self.calculate_position_size(price, stop_loss, self.balance, bearish_strength)
                        
                        if size > 0:
                            position = {
                                'type': 'short',
                                'entry_price': price,
                                'entry_index': i,
                                'entry_time': timestamp_beijing,
                                'size': size,
                                'remain_size': size,
                                'stop_loss': stop_loss,
                                'lowest_price': price,  # 记录最低价
                                'tp_signals_triggered': [],
                                'initial_added': False,
                            }
                            last_bearish_index = bearish_info['index']
                            self.confirmed_trades += 1
                            self.trades.append({
                                'time': timestamp_beijing, 'type': 'short', 'action': 'OPEN',
                                'price': price, 'size': size
                            })
                            print(f"\n[{timestamp_beijing}] 🔴 开空仓: 价格={price:.2f}, 数量={size:.4f}")
            
            # ========== 持仓管理 ==========
            if position:
                current_price = self.df_5m.iloc[idx_5m]['close']
                position_type = position['type']
                triggered = position.get('tp_signals_triggered', [])
                entry_index = position['entry_index']
                initial_added = position.get('initial_added', False)
                
                # 更新最高价/最低价
                if position_type == 'long':
                    if current_price > position.get('highest_price', price):
                        position['highest_price'] = current_price
                else:
                    if current_price < position.get('lowest_price', price):
                        position['lowest_price'] = current_price
                
                # 检查是否在保护期内
                in_protection = self.is_in_protection_period(entry_index, i)
                # 检查是否浮盈
                in_profit = self.is_in_profit(position_type, current_price, position['entry_price'])
                
                # ========== 移动止损（保护利润） ==========
                if in_profit and not in_protection:
                    if position_type == 'long':
                        highest = position.get('highest_price', position['entry_price'])
                        stop_updated = self.update_trailing_stop(position, current_price, atr, highest, None)
                    else:
                        lowest = position.get('lowest_price', position['entry_price'])
                        stop_updated = self.update_trailing_stop(position, current_price, atr, None, lowest)
                    
                    if stop_updated:
                        print(f"[{timestamp_beijing}] 📍 移动止损: 新止损={position['stop_loss']:.2f}")
                
                # 根据多空判断减仓条件
                if position_type == 'long':
                    cond_1min = td9_1m == -1
                    cond_3min = td9_3m == -1
                    cond_5min = td9_5m == -1
                else:
                    cond_1min = td9_1m == 1
                    cond_3min = td9_3m == 1
                    cond_5min = td9_5m == 1
                
                # 止损检查（先检查移动止损后的价格）
                if position_type == 'long' and low <= position['stop_loss']:
                    pnl = (position['stop_loss'] - position['entry_price']) * position['remain_size']
                    self.balance += pnl
                    self.trailing_stop_trigger_count += 1
                    self.trades.append({
                        'time': timestamp_beijing, 'type': 'long', 'action': 'TRAILING_STOP',
                        'price': position['stop_loss'], 'pnl': pnl
                    })
                    print(f"[{timestamp_beijing}] 📍 移动止损触发: {pnl:.2f}")
                    position = None
                    self.equity_curve.append(self.balance)
                    self.equity_timestamps.append(timestamp_beijing)
                    continue
                    
                elif position_type == 'short' and high >= position['stop_loss']:
                    pnl = (position['entry_price'] - position['stop_loss']) * position['remain_size']
                    self.balance += pnl
                    self.trailing_stop_trigger_count += 1
                    self.trades.append({
                        'time': timestamp_beijing, 'type': 'short', 'action': 'TRAILING_STOP',
                        'price': position['stop_loss'], 'pnl': pnl
                    })
                    print(f"[{timestamp_beijing}] 📍 移动止损触发: {pnl:.2f}")
                    position = None
                    self.equity_curve.append(self.balance)
                    self.equity_timestamps.append(timestamp_beijing)
                    continue
                
                # ========== 初始加仓逻辑 ==========
                if not initial_added and not triggered and not in_protection and in_profit:
                    if self.get_initial_add_signal(td9_15m, position_type):
                        add_size = position['size'] * self.initial_add_size
                        if add_size > 0:
                            position['remain_size'] += add_size
                            position['initial_added'] = True
                            self.initial_add_count += 1
                            direction = "多" if position_type == 'long' else "空"
                            print(f"[{timestamp_beijing}] 📈 初始加仓 ({direction}): +{add_size:.4f}, 触发=15分钟TD9")
                            self.equity_curve.append(self.balance)
                            self.equity_timestamps.append(timestamp_beijing)
                
                # ========== 多级别减仓 ==========
                if not in_protection and in_profit:
                    # 1分钟减仓
                    if cond_1min and '1min' not in triggered:
                        close_pct = self.tp_ratios['1min']
                        close_size = position['size'] * close_pct
                        if close_size > 0 and close_size <= position['remain_size']:
                            if position_type == 'long':
                                pnl = (current_price - position['entry_price']) * close_size
                            else:
                                pnl = (position['entry_price'] - current_price) * close_size
                            self.balance += pnl
                            position['remain_size'] -= close_size
                            triggered.append('1min')
                            self.td1_trigger_count += 1
                            direction = "多" if position_type == 'long' else "空"
                            self.trades.append({
                                'time': timestamp_beijing, 'type': position_type, 'action': f'TP_{close_pct*100:.0f}%',
                                'price': current_price, 'pnl': pnl, 'source': '1min'
                            })
                            print(f"[{timestamp_beijing}] 🎯 1分钟TD9减仓{close_pct*100:.0f}% ({direction}): 盈利={pnl:.2f}")
                    
                    # 3分钟减仓
                    if cond_3min and '3min' not in triggered:
                        close_pct = self.tp_ratios['3min']
                        close_size = position['size'] * close_pct
                        if close_size > 0 and close_size <= position['remain_size']:
                            if position_type == 'long':
                                pnl = (current_price - position['entry_price']) * close_size
                            else:
                                pnl = (position['entry_price'] - current_price) * close_size
                            self.balance += pnl
                            position['remain_size'] -= close_size
                            triggered.append('3min')
                            self.td3_trigger_count += 1
                            direction = "多" if position_type == 'long' else "空"
                            self.trades.append({
                                'time': timestamp_beijing, 'type': position_type, 'action': f'TP_{close_pct*100:.0f}%',
                                'price': current_price, 'pnl': pnl, 'source': '3min'
                            })
                            print(f"[{timestamp_beijing}] 🎯 3分钟TD9减仓{close_pct*100:.0f}% ({direction}): 盈利={pnl:.2f}")
                    
                    # 5分钟减仓
                    if cond_5min and '5min' not in triggered:
                        close_pct = self.tp_ratios['5min']
                        close_size = position['size'] * close_pct
                        if close_size > 0 and close_size <= position['remain_size']:
                            if position_type == 'long':
                                pnl = (current_price - position['entry_price']) * close_size
                            else:
                                pnl = (position['entry_price'] - current_price) * close_size
                            self.balance += pnl
                            position['remain_size'] -= close_size
                            triggered.append('5min')
                            self.td5_trigger_count += 1
                            direction = "多" if position_type == 'long' else "空"
                            self.trades.append({
                                'time': timestamp_beijing, 'type': position_type, 'action': f'TP_{close_pct*100:.0f}%',
                                'price': current_price, 'pnl': pnl, 'source': '5min'
                            })
                            print(f"[{timestamp_beijing}] 🎯 5分钟TD9减仓{close_pct*100:.0f}% ({direction}): 盈利={pnl:.2f}")
                else:
                    if (cond_1min or cond_3min or cond_5min) and in_profit is False:
                        self.skipped_actions += 1
                
                position['tp_signals_triggered'] = triggered
                
                # ========== 减仓后的加仓逻辑 ==========
                reentry, source = self.get_reentry_signal(td9_3m, td9_5m, position_type)
                if reentry and triggered and not in_protection and in_profit:
                    total_closed = sum(position['size'] * self.tp_ratios[s] for s in triggered if s in self.tp_ratios)
                    current_remain = position['remain_size']
                    target_size = position['size'] - total_closed
                    
                    if current_remain < target_size:
                        add_size = min(target_size - current_remain, position['size'] * 0.3)
                        if add_size > 0:
                            position['remain_size'] += add_size
                            position['tp_signals_triggered'] = []
                            self.reentry_trigger_count += 1
                            direction = "多" if position_type == 'long' else "空"
                            print(f"[{timestamp_beijing}] 🔄 加仓拿回仓位 ({direction}): +{add_size:.4f}, 触发={source}")
                
                # ========== TD清仓逻辑 ==========
                clear, source = self.should_clear(td9_15m, td9_30m, position_type)
                if clear and not in_protection:
                    if source == '15min':
                        self.td15_clear_count += 1
                    else:
                        self.td30_clear_count += 1
                    
                    if position_type == 'long':
                        pnl = (current_price - position['entry_price']) * position['remain_size']
                    else:
                        pnl = (position['entry_price'] - current_price) * position['remain_size']
                    self.balance += pnl
                    direction = "多" if position_type == 'long' else "空"
                    self.trades.append({
                        'time': timestamp_beijing, 'type': position_type, 'action': 'FULL_CLOSE',
                        'price': current_price, 'pnl': pnl, 'source': source
                    })
                    print(f"[{timestamp_beijing}] 🏁 {direction}仓清仓: {pnl:.2f}, 触发={source}")
                    position = None
                    self.equity_curve.append(self.balance)
                    self.equity_timestamps.append(timestamp_beijing)
                    continue
                
                if position:
                    self.equity_curve.append(self.balance)
                    self.equity_timestamps.append(timestamp_beijing)
        
        if position:
            last_price = self.df_15m['close'].iloc[-1]
            last_time = self.df_15m['time_beijing'].iloc[-1]
            if position['type'] == 'long':
                pnl = (last_price - position['entry_price']) * position['remain_size']
            else:
                pnl = (position['entry_price'] - last_price) * position['remain_size']
            self.balance += pnl
        
        min_len = min(len(self.equity_curve), len(self.equity_timestamps))
        self.equity_curve = self.equity_curve[:min_len]
        self.equity_timestamps = self.equity_timestamps[:min_len]
        
        print(f"\n回测完成!")
        print(f"总背驰信号: {self.total_signals} (做多: {self.buy_signals}, 做空: {self.sell_signals})")
        print(f"被过滤的做多信号: {self.filtered_buy_signals}")
        print(f"实际交易: {self.confirmed_trades}")
        print(f"移动止损触发: {self.trailing_stop_trigger_count} 次")
        print(f"初始加仓次数: {self.initial_add_count} 次")
        print(f"减仓触发统计:")
        print(f"  - 1分钟TD9减仓: {self.td1_trigger_count} 次")
        print(f"  - 3分钟TD9减仓: {self.td3_trigger_count} 次")
        print(f"  - 5分钟TD9减仓: {self.td5_trigger_count} 次")
        print(f"加仓触发统计:")
        print(f"  - 减仓后加仓: {self.reentry_trigger_count} 次")
        print(f"清仓触发统计:")
        print(f"  - 15分钟TD9清仓: {self.td15_clear_count} 次")
        print(f"  - 30分钟TD9清仓: {self.td30_clear_count} 次")
        
        return self.trades
    
    def calculate_metrics(self):
        closed_trades = [t for t in self.trades if 'pnl' in t and t['action'] in ['STOP_LOSS', 'FULL_CLOSE', 'TRAILING_STOP']]
        
        if not closed_trades:
            return self._empty_metrics()
        
        total_pnl = sum(t['pnl'] for t in closed_trades)
        total_return = (self.balance - self.initial_balance) / self.initial_balance * 100
        
        long_trades = [t for t in closed_trades if t['type'] == 'long']
        short_trades = [t for t in closed_trades if t['type'] == 'short']
        winning_trades = [t for t in closed_trades if t['pnl'] > 0]
        win_rate = len(winning_trades) / len(closed_trades) * 100
        
        avg_win = np.mean([t['pnl'] for t in winning_trades]) if winning_trades else 0
        losing_trades = [t for t in closed_trades if t['pnl'] <= 0]
        avg_loss = abs(np.mean([t['pnl'] for t in losing_trades])) if losing_trades else 1
        profit_factor = avg_win / avg_loss if avg_loss != 0 else 0
        
        total_long_pnl = sum(t['pnl'] for t in long_trades) if long_trades else 0
        total_short_pnl = sum(t['pnl'] for t in short_trades) if short_trades else 0
        
        peak = self.equity_curve[0]
        max_dd = 0
        for value in self.equity_curve:
            if value > peak:
                peak = value
            dd = (peak - value) / peak if peak > 0 else 0
            max_dd = max(max_dd, dd)
        
        returns = []
        for i in range(1, len(self.equity_curve)):
            if self.equity_curve[i-1] > 0:
                ret = (self.equity_curve[i] - self.equity_curve[i-1]) / self.equity_curve[i-1]
                returns.append(ret)
        
        sharpe = 0
        if len(returns) > 1:
            mean_ret = np.mean(returns)
            std_ret = np.std(returns)
            if std_ret > 0:
                sharpe = mean_ret / std_ret * np.sqrt(252 * 24 * 4)
        
        return {
            'initial_balance': self.initial_balance,
            'final_balance': self.balance,
            'total_return': total_return,
            'total_pnl': total_pnl,
            'total_trades': len(closed_trades),
            'long_trades': len(long_trades),
            'short_trades': len(short_trades),
            'winning_trades': len(winning_trades),
            'losing_trades': len(losing_trades),
            'win_rate': win_rate,
            'profit_factor': profit_factor,
            'max_drawdown': max_dd * 100,
            'sharpe_ratio': sharpe,
            'avg_win': avg_win,
            'avg_loss': avg_loss,
            'total_long_pnl': total_long_pnl,
            'total_short_pnl': total_short_pnl,
            'total_signals': self.total_signals,
            'buy_signals': self.buy_signals,
            'sell_signals': self.sell_signals,
            'filtered_buy_signals': self.filtered_buy_signals,
            'confirmed_trades': self.confirmed_trades,
            'trailing_stop_trigger': self.trailing_stop_trigger_count,
            'initial_add_count': self.initial_add_count,
            'td1_trigger': self.td1_trigger_count,
            'td3_trigger': self.td3_trigger_count,
            'td5_trigger': self.td5_trigger_count,
            'reentry_trigger': self.reentry_trigger_count,
            'td15_clear': self.td15_clear_count,
            'td30_clear': self.td30_clear_count
        }
    
    def _empty_metrics(self):
        return {
            'initial_balance': self.initial_balance,
            'final_balance': self.balance,
            'total_return': 0,
            'total_pnl': 0,
            'total_trades': 0,
            'long_trades': 0,
            'short_trades': 0,
            'winning_trades': 0,
            'losing_trades': 0,
            'win_rate': 0,
            'profit_factor': 0,
            'max_drawdown': 0,
            'sharpe_ratio': 0,
            'avg_win': 0,
            'avg_loss': 0,
            'total_long_pnl': 0,
            'total_short_pnl': 0,
            'total_signals': self.total_signals,
            'buy_signals': self.buy_signals,
            'sell_signals': self.sell_signals,
            'filtered_buy_signals': self.filtered_buy_signals,
            'confirmed_trades': self.confirmed_trades,
            'trailing_stop_trigger': self.trailing_stop_trigger_count,
            'initial_add_count': self.initial_add_count,
            'td1_trigger': self.td1_trigger_count,
            'td3_trigger': self.td3_trigger_count,
            'td5_trigger': self.td5_trigger_count,
            'reentry_trigger': self.reentry_trigger_count,
            'td15_clear': self.td15_clear_count,
            'td30_clear': self.td30_clear_count
        }
    
    def plot_results(self):
        fig, axes = plt.subplots(4, 1, figsize=(16, 12))
        
        ax1 = axes[0]
        ax1.plot(self.df_15m['time_beijing'], self.df_15m['close'], 'b-', linewidth=1, alpha=0.7, label='Price')
        open_trades = [t for t in self.trades if t['action'] == 'OPEN']
        if open_trades:
            buy_times = [t['time'] for t in open_trades if t['type'] == 'long']
            buy_prices = [t['price'] for t in open_trades if t['type'] == 'long']
            sell_times = [t['time'] for t in open_trades if t['type'] == 'short']
            sell_prices = [t['price'] for t in open_trades if t['type'] == 'short']
            if buy_times:
                ax1.scatter(buy_times, buy_prices, color='green', marker='^', s=100, label='Long Open', zorder=5)
            if sell_times:
                ax1.scatter(sell_times, sell_prices, color='red', marker='v', s=100, label='Short Open', zorder=5)
        ax1.set_title('Price and Trading Signals', fontsize=14)
        ax1.legend()
        ax1.grid(True, alpha=0.3)
        
        ax2 = axes[1]
        ax2.plot(self.df_15m['time_beijing'], self.df_15m['macd'], 'b-', linewidth=1, label='MACD')
        ax2.plot(self.df_15m['time_beijing'], self.df_15m['macd_signal'], 'r-', linewidth=1, label='Signal')
        colors = ['green' if x > 0 else 'red' for x in self.df_15m['macd_hist']]
        ax2.bar(self.df_15m['time_beijing'], self.df_15m['macd_hist'], color=colors, alpha=0.3)
        ax2.axhline(y=0, color='black', linestyle='-', linewidth=0.5)
        ax2.set_title('MACD')
        ax2.legend()
        
        ax3 = axes[2]
        min_len = min(len(self.equity_timestamps), len(self.equity_curve))
        timestamps_plot = self.equity_timestamps[:min_len]
        equity_plot = self.equity_curve[:min_len]
        ax3.plot(timestamps_plot, equity_plot, 'g-', linewidth=2, label='Equity Curve')
        ax3.axhline(y=self.initial_balance, color='gray', linestyle='--', alpha=0.7)
        ax3.set_title('Equity Curve')
        ax3.set_ylabel('Balance (USDT)')
        ax3.legend()
        
        ax4 = axes[3]
        if len(equity_plot) > 1:
            peak = equity_plot[0]
            drawdowns = []
            for value in equity_plot:
                if value > peak:
                    peak = value
                dd = (peak - value) / peak * 100 if peak > 0 else 0
                drawdowns.append(dd)
            ax4.fill_between(timestamps_plot, 0, drawdowns, color='red', alpha=0.3)
            ax4.plot(timestamps_plot, drawdowns, 'r-', linewidth=1)
            ax4.set_title('Drawdown')
            ax4.set_ylabel('Drawdown (%)')
            ax4.set_xlabel('Time (Beijing Time)')
        
        plt.xticks(rotation=45)
        plt.tight_layout()
        
        metrics = self.calculate_metrics()
        self.print_report(metrics)
        plt.show()
        return fig
    
    def print_report(self, metrics):
        print("\n" + "="*70)
        print("优化版MACD背驰 + 多级别TD信号策略回测报告（含移动止损）")
        print("="*70)
        print(f"初始资金: ${metrics['initial_balance']:,.2f}")
        print(f"最终资金: ${metrics['final_balance']:,.2f}")
        print(f"总收益率: {metrics['total_return']:.2f}%")
        print(f"总盈亏: ${metrics['total_pnl']:,.2f}")
        print("-"*70)
        print(f"总交易次数: {metrics['total_trades']}")
        print(f"  - 做多交易: {metrics['long_trades']}")
        print(f"  - 做空交易: {metrics['short_trades']}")
        print(f"盈利交易: {metrics['winning_trades']}")
        print(f"亏损交易: {metrics['losing_trades']}")
        print(f"胜率: {metrics['win_rate']:.2f}%")
        print(f"盈亏比: {metrics['profit_factor']:.2f}")
        print("-"*70)
        print(f"做多总盈亏: ${metrics['total_long_pnl']:,.2f}")
        print(f"做空总盈亏: ${metrics['total_short_pnl']:,.2f}")
        print(f"平均盈利: ${metrics['avg_win']:,.2f}")
        print(f"平均亏损: ${metrics['avg_loss']:,.2f}")
        print(f"最大回撤: {metrics['max_drawdown']:.2f}%")
        print(f"夏普比率: {metrics['sharpe_ratio']:.2f}")
        print("-"*70)
        print(f"总背驰信号: {metrics['total_signals']}")
        print(f"  - 做多信号: {metrics['buy_signals']}")
        print(f"  - 做空信号: {metrics['sell_signals']}")
        print(f"被过滤的做多信号: {metrics['filtered_buy_signals']}")
        print(f"实际交易次数: {metrics['confirmed_trades']}")
        print(f"移动止损触发: {metrics['trailing_stop_trigger']} 次")
        print(f"初始加仓次数: {metrics['initial_add_count']} 次")
        print(f"减仓触发统计:")
        print(f"  - 1分钟TD9减仓25%: {metrics['td1_trigger']} 次")
        print(f"  - 3分钟TD9减仓20%: {metrics['td3_trigger']} 次")
        print(f"  - 5分钟TD9减仓25%: {metrics['td5_trigger']} 次")
        print(f"加仓触发统计:")
        print(f"  - 减仓后加仓: {metrics['reentry_trigger']} 次")
        print(f"清仓触发统计:")
        print(f"  - 15分钟TD9清仓: {metrics['td15_clear']} 次")
        print(f"  - 30分钟TD9清仓: {metrics['td30_clear']} 次")
        print("="*70)


def run_backtest():
    print("="*70)
    print("多级别TD信号仓位管理策略回测（含移动止损）")
    print("="*70)
    
    symbol = "ETHUSDT"
    
    print("\n正在获取K线数据...")
    df_15m = get_klines(symbol, "15m", total_limit=1000)
    df_5m = get_klines(symbol, "5m", total_limit=1000)
    df_3m = get_klines(symbol, "3m", total_limit=1000)
    df_1m = get_klines(symbol, "1m", total_limit=1000)
    df_30m = get_klines(symbol, "30m", total_limit=1000)
    
    if df_15m is None or len(df_15m) < 200:
        print("数据不足")
        return None
    
    print(f"获取到15分钟数据: {len(df_15m)} 根")
    print(f"获取到5分钟数据: {len(df_5m)} 根")
    print(f"获取到3分钟数据: {len(df_3m)} 根")
    print(f"获取到1分钟数据: {len(df_1m)} 根")
    print(f"获取到30分钟数据: {len(df_30m)} 根")
    
    strategy = OptimizedMACDTDStrategyV6(
        df_15m=df_15m,
        df_5m=df_5m,
        df_3m=df_3m,
        df_1m=df_1m,
        df_30m=df_30m,
        initial_balance=100000,
        risk_per_trade=0.05,
        enable_buy_filter=True,
        buy_rsi_threshold=40,
        buy_volume_ratio=0.8,
        enable_30min_clear=True,
        min_bars_before_action=1,
        initial_add_size=0.3,
        trailing_stop_atr=2,
        trailing_stop_pct=0.05
    )
    
    strategy.run()
    metrics = strategy.calculate_metrics()
    strategy.print_report(metrics)
    strategy.plot_results()
    
    return strategy, metrics


if __name__ == "__main__":
    strategy, metrics = run_backtest()
    print("\n✅ 回测完成！")