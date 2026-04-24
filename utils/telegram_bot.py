"""
Telegram Bot 通知模块
用于发送交易信号和统计信息
"""

import requests
import logging
from datetime import datetime
from typing import Dict, Any, List
import json
import os

logger = logging.getLogger(__name__)


def get_beijing_time():
    """获取北京时间"""
    from datetime import datetime, timezone, timedelta
    
    beijing_tz = timezone(timedelta(hours=8))
    beijing_time = datetime.now(beijing_tz).strftime('%Y-%m-%d %H:%M:%S')
    return beijing_time


class TelegramBot:
    """Telegram Bot 通知类"""
    
    def __init__(self, bot_token: str = None, chat_id: str = None, config_file: str = "telegram_config.json", symbol="ETH"):
        """
        初始化Telegram Bot
        
        Args:
            bot_token: Bot Token (从 @BotFather 获取)
            chat_id: 聊天ID (可以是个人或群组)
            config_file: 配置文件路径
            symbol: 交易对符号
        """
        self.config_file = config_file
        self.symbol = symbol
        self.balance = 0
        self.total_trades = 0
        self.win_rate = 0
        
        # 配置Telegram（请替换为您的实际token和chat_id）
        self.bot_token = bot_token
        self.chat_id = chat_id
        
        self.base_url = f"https://api.telegram.org/bot{self.bot_token}"
        
        # 测试连接
        self.test_connection()
    
    def load_config(self):
        """加载Telegram配置"""
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, 'r') as f:
                    config = json.load(f)
                    self.bot_token = config.get('bot_token', '')
                    self.chat_id = config.get('chat_id', '')
                    logger.info("Telegram配置加载成功")
            except Exception as e:
                logger.error(f"加载Telegram配置失败: {e}")
                self.bot_token = ''
                self.chat_id = ''
        else:
            logger.warning(f"配置文件 {self.config_file} 不存在，请先配置Telegram Bot")
            self.bot_token = ''
            self.chat_id = ''
    
    def save_config(self):
        """保存Telegram配置"""
        try:
            config = {
                'bot_token': self.bot_token,
                'chat_id': self.chat_id,
                'updated_at': datetime.now().isoformat()
            }
            with open(self.config_file, 'w') as f:
                json.dump(config, f, indent=2)
            logger.info("Telegram配置保存成功")
        except Exception as e:
            logger.error(f"保存Telegram配置失败: {e}")
    
    def test_connection(self):
        """测试Telegram连接"""
        if not self.bot_token or not self.chat_id:
            logger.warning("Telegram Bot未配置，消息将不会发送")
            return False
        
        try:
            url = f"{self.base_url}/getMe"
            response = requests.get(url, timeout=10)
            if response.status_code == 200:
                bot_info = response.json()
                logger.info(f"Telegram Bot连接成功: {bot_info['result']['username']}")
                self.send_message("🤖 交易机器人已启动")
                return True
            else:
                logger.error(f"Telegram连接失败: {response.status_code}")
                return False
        except Exception as e:
            logger.error(f"Telegram连接异常: {e}")
            return False
    
    def send_message(self, message: str, parse_mode: str = 'HTML') -> bool:
        """
        发送文本消息
        
        Args:
            message: 消息内容
            parse_mode: 解析模式 (HTML, Markdown)
        
        Returns:
            bool: 是否发送成功
        """
        if not self.bot_token or not self.chat_id:
            return False
        
        try:
            url = f"{self.base_url}/sendMessage"
            payload = {
                'chat_id': self.chat_id,
                'text': message,
                'parse_mode': parse_mode
            }
            response = requests.post(url, json=payload, timeout=10)
            
            if response.status_code == 200:
                logger.debug(f"消息发送成功: {message[:50]}...")
                return True
            else:
                logger.error(f"消息发送失败: {response.status_code} - {response.text}")
                return False
        except Exception as e:
            logger.error(f"发送消息异常: {e}")
            return False
    
    def send_photo(self, photo_path: str, caption: str = None) -> bool:
        """发送图片"""
        if not self.bot_token or not self.chat_id:
            return False
        
        try:
            url = f"{self.base_url}/sendPhoto"
            with open(photo_path, 'rb') as photo:
                files = {'photo': photo}
                data = {'chat_id': self.chat_id}
                if caption:
                    data['caption'] = caption
                response = requests.post(url, files=files, data=data, timeout=30)
            
            return response.status_code == 200
        except Exception as e:
            logger.error(f"发送图片失败: {e}")
            return False
    
    def send_trade_signal(self, signal_info: Dict[str, Any]) -> bool:
        """发送交易信号通知"""
        signal_type = signal_info.get('signal', 0)
        prob = signal_info.get('prob', 0.5)
        price = signal_info.get('price', 0)
        trend = signal_info.get('trend', 'unknown')
        signal_source = signal_info.get('signal_type', 0)
        rsi = signal_info.get('rsi', 50)
        signal_reason = signal_info.get('signal_reason', '')
        
        if signal_type == 0:
            return False
        
        if signal_type == 1:
            direction = "🟢 做多 (LONG)"
            emoji = "📈"
        else:
            direction = "🔴 做空 (SHORT)"
            emoji = "📉"
        
        confidence = abs(prob - 0.5) * 200
        confidence_bar = "█" * int(confidence / 10) + "░" * (10 - int(confidence / 10))
        
        signal_text = {
            1: "🔺 上涨笔",
            -1: "🔻 下跌笔",
            0: "⚪ 无信号"
        }.get(signal_source, "⚪ 无信号")
        
        message = f"""
{emoji} <b>【交易信号】</b> {emoji}

<b>方向：</b> {direction}
<b>价格：</b> ${price:,.2f}
<b>AI概率：</b> {prob:.3f} ({confidence:.1f}%)
<code>{confidence_bar}</code>

<b>缠论信号：</b> {signal_text}
<b>RSI：</b> {rsi:.1f}
<b>趋势判断：</b> {'📈 上涨趋势' if trend == 'long' else '📉 下跌趋势'}

<b>信号说明：</b> {signal_reason}

⏰ <b>时间：</b> {get_beijing_time()}
        """
        
        return self.send_message(message.strip())
    
    def send_open_position(self, position_info: Dict[str, Any]) -> bool:
        """
        发送开仓通知 - 修复版
        
        Args:
            position_info: 持仓信息，应包含以下字段：
                - type: 'long' or 'short'
                - entry_price: 开仓价格
                - size_usdt: 开仓价值(USDT)
                - b_size: 基础货币数量(BTC/ETH)
                - stop_loss: 止损价格
                - tp1: 止盈1价格
                - tp2: 止盈2价格
                - entry_prob: AI概率
        """
        # 获取必要字段，提供默认值
        position_type = position_info.get('type', 'long')
        entry_price = position_info.get('entry_price', 0)
        size_usdt = position_info.get('size_usdt', 0)
        b_size = position_info.get('b_size', 0)
        stop_loss = position_info.get('stop_loss', 0)
        tp1 = position_info.get('tp1', 0)
        tp2 = position_info.get('tp2', 0)
        prob = position_info.get('entry_prob', 0.5)
        
        # 验证必要字段
        if entry_price == 0:
            logger.error(f"开仓通知缺少entry_price: {position_info}")
            entry_price = position_info.get('entry_price', 0)
        
        if size_usdt == 0:
            logger.error(f"开仓通知缺少size_usdt: {position_info}")
            size_usdt = position_info.get('size_usdt', 0)
        
        # 计算风险和收益
        if position_type == 'long':
            direction = "🟢 多仓开仓"
            emoji = "🚀"
            if entry_price > 0 and stop_loss > 0 and b_size > 0:
                risk_amount = (entry_price - stop_loss) * b_size
            else:
                risk_amount = 0
        else:
            direction = "🔴 空仓开仓"
            emoji = "💀"
            if entry_price > 0 and stop_loss > 0 and b_size > 0:
                risk_amount = (stop_loss - entry_price) * b_size
            else:
                risk_amount = 0
        
        # 计算风险百分比
        if size_usdt > 0:
            risk_percent = (risk_amount / size_usdt) * 100
        else:
            risk_percent = 0
        
        # 计算预期收益（基于TP1和TP2各50%）
        if position_type == 'long' and entry_price > 0 and b_size > 0:
            expected_profit = ((tp1 - entry_price) * b_size * 0.5 + 
                              (tp2 - entry_price) * b_size * 0.5)
        elif position_type == 'short' and entry_price > 0 and b_size > 0:
            expected_profit = ((entry_price - tp1) * b_size * 0.5 + 
                              (entry_price - tp2) * b_size * 0.5)
        else:
            expected_profit = 0
        
        # 风险收益比
        risk_reward_ratio = abs(expected_profit / risk_amount) if risk_amount > 0 else 0
        
        # AI置信度
        confidence = abs(prob - 0.5) * 200
        
        message = f"""
{emoji} <b>{direction}</b> {emoji}

<b>📊 开仓信息：</b>
• 开仓价格: ${entry_price:,.2f}
• 开仓数量: {b_size:.4f} {self.symbol.replace('USDT', '')}
• 合约价值: ${size_usdt:,.2f}

<b>🎯 止盈止损：</b>
• 止损价格: ${stop_loss:,.2f}
• 止盈目标1: ${tp1:,.2f} (50%仓位)
• 止盈目标2: ${tp2:,.2f} (50%仓位)

<b>💰 风险收益：</b>
• 单笔风险: ${risk_amount:,.2f} ({risk_percent:.2f}%)
• 预期收益: ${expected_profit:,.2f}
• 风险收益比: 1:{risk_reward_ratio:.2f}
• AI置信度: {confidence:.1f}%

⏰ <b>开仓时间：</b> {get_beijing_time()}
        """
        
        return self.send_message(message.strip())
    
    def send_close_position(self, close_info: Dict[str, Any]) -> bool:
        """
        发送平仓通知 - 修复版
        
        Args:
            close_info: 平仓信息，应包含以下字段：
                - type: 'long' or 'short'
                - price: 平仓价格
                - entry_price: 开仓价格
                - size_usdt: 平仓价值(USDT)
                - b_size: 平仓数量(BTC/ETH)
                - pnl: 盈亏金额(USDT)
                - reason: 平仓原因
        """
        position_type = close_info.get('type', 'long')
        close_price = close_info.get('price', 0)
        entry_price = close_info.get('entry_price', 0)
        size_usdt = close_info.get('size_usdt', 0)
        b_size = close_info.get('b_size', 0)
        pnl = close_info.get('pnl', 0)
        reason = close_info.get('reason', '手动平仓')
        
        # 验证必要字段
        if close_price == 0:
            logger.error(f"平仓通知缺少price: {close_info}")
            close_price = close_info.get('price', 0)
        
        # 计算收益率
        if size_usdt > 0:
            pnl_percent = (pnl / size_usdt) * 100
        else:
            pnl_percent = 0
        
        # 计算价格变化
        if entry_price > 0:
            if position_type == 'long':
                price_change = ((close_price - entry_price) / entry_price) * 100
            else:
                price_change = ((entry_price - close_price) / entry_price) * 100
        else:
            price_change = 0
        
        # 盈亏表情
        if pnl > 0:
            emoji = "💰"
            status = "盈利"
            color = "🟢"
        elif pnl < 0:
            emoji = "💸"
            status = "亏损"
            color = "🔴"
        else:
            emoji = "⚪"
            status = "平本"
            color = "⚪"
        
        # 平仓原因映射
        reason_map = {
            'TP1完全止盈': '🎯 第一目标止盈',
            'TP2止盈': '🏆 第二目标止盈',
            '止损': '⚠️ 止损触发',
            '手动平仓': '✋ 手动平仓'
        }
        reason_text = reason_map.get(reason, reason)
        
        message = f"""
{emoji} <b>{color} 平仓通知 {color}</b> {emoji}

<b>📊 平仓信息：</b>
• 方向: {'多仓' if position_type == 'long' else '空仓'}
• 平仓原因: {reason_text}
• 平仓价格: ${close_price:,.2f}
• 开仓价格: ${entry_price:,.2f}
• 价格变化: {price_change:+.2f}%

<b>📈 盈亏情况：</b>
• 盈亏金额: ${pnl:+,.2f}
• 收益率: {pnl_percent:+.2f}%
• 状态: {status}
• 平仓数量: {b_size:.4f} {self.symbol.replace('USDT', '')}

<b>📊 累计统计：</b>
• 当前资金: ${self.balance:,.2f}
• 总交易次数: {self.total_trades}
• 胜率: {self.win_rate:.1f}%

⏰ <b>平仓时间：</b> {get_beijing_time()}
        """
        
        return self.send_message(message.strip())
    
    def send_daily_report(self, stats: Dict[str, Any]) -> bool:
        """发送每日统计报告"""
        date = datetime.now().strftime('%Y-%m-%d')
        
        # 构建持仓信息
        positions_text = ""
        for pos in stats.get('positions', []):
            pnl_emoji = "🟢" if pos.get('pnl_percent', 0) > 0 else "🔴" if pos.get('pnl_percent', 0) < 0 else "⚪"
            positions_text += f"\n{pnl_emoji} {pos['type'].upper()}: ${pos.get('price', 0):,.2f} "
            positions_text += f"({pos.get('pnl_percent', 0):+.1f}%) - ${pos.get('size_usdt', 0):,.0f}"
        
        if not positions_text:
            positions_text = "无持仓"
        
        message = f"""
📊 <b>【交易日报 - {date}】</b> 📊

<b>📈 资金状况：</b>
• 初始资金: ${stats.get('initial_balance', 0):,.2f}
• 当前资金: ${stats.get('current_balance', 0):,.2f}
• 当日盈亏: ${stats.get('daily_pnl', 0):+,.2f}
• 当日收益率: {stats.get('daily_return', 0):+.2f}%
• 累计收益率: {stats.get('total_return', 0):+.2f}%

<b>🎯 交易统计：</b>
• 今日交易: {stats.get('daily_trades', 0)} 笔
• 累计交易: {stats.get('total_trades', 0)} 笔
• 今日胜率: {stats.get('daily_win_rate', 0):.1f}%
• 累计胜率: {stats.get('total_win_rate', 0):.1f}%

<b>📊 绩效指标：</b>
• 最大回撤: {stats.get('max_drawdown', 0):.2f}%
• 盈亏比: {stats.get('profit_factor', 0):.2f}
• 夏普比率: {stats.get('sharpe_ratio', 0):.2f}

<b>🏆 最佳/最差交易：</b>
• 最佳盈利: ${stats.get('best_trade', 0):+,.2f}
• 最大亏损: ${stats.get('worst_trade', 0):+,.2f}

<b>📋 当前持仓：</b>
{positions_text}

<b>💡 系统状态：</b>
• 运行时间: {stats.get('runtime', 'N/A')}
• 信号质量: {stats.get('signal_quality', 'N/A')}

⏰ <b>报告时间：</b> {get_beijing_time()}
        """
        
        return self.send_message(message.strip())
    
    def send_error_alert(self, error_msg: str, error_type: str = "ERROR") -> bool:
        """发送错误警报"""
        message = f"""
⚠️ <b>【系统警报 - {error_type}】</b> ⚠️

<b>错误信息：</b>
<code>{error_msg[:500]}</code>

<b>时间：</b> {get_beijing_time()}

请立即检查系统！
        """
        
        return self.send_message(message.strip())


# 全局Telegram实例
telegram_bot = None


def init_telegram(bot_token: str = None, chat_id: str = None, symbol=None):
    """初始化Telegram Bot"""
    global telegram_bot
    telegram_bot = TelegramBot(bot_token, chat_id, symbol=symbol)
    return telegram_bot


def get_telegram_bot():
    """获取Telegram Bot实例"""
    return telegram_bot


if __name__ == "__main__":
    bot_token = ""
    chat_id = ""
    bot = init_telegram(bot_token, chat_id)



    
