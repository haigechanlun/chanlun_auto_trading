import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(__file__)))

import time
import pandas as pd
from binance.client import Client
import requests

from config import API_KEY, API_SECRET

# ========= 配置 =========
TELEGRAM_TOKEN = ""
CHAT_ID = ""

SYMBOLS = [
    "BTCUSDT",
    "ETHUSDT",
    "XAUUSDT",
]

INTERVALS = {
    "5m": Client.KLINE_INTERVAL_5MINUTE,
    "15m": Client.KLINE_INTERVAL_15MINUTE,
    "30m": Client.KLINE_INTERVAL_30MINUTE,
    "1h": Client.KLINE_INTERVAL_1HOUR,
}

LIMIT = 200

client = Client(API_KEY, API_SECRET)

# ========= 状态缓存 =========

last_alert_9 = {s: {i: None for i in INTERVALS} for s in SYMBOLS}
last_alert_13 = {s: {i: None for i in INTERVALS} for s in SYMBOLS}

# K线时间缓存（用于判断是否新K线）
last_kline_time = {s: {i: None for i in INTERVALS} for s in SYMBOLS}


# ========= telegram =========

def send_telegram(msg):

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

    try:
        requests.post(url, data={
            "chat_id": CHAT_ID,
            "text": msg
        })

    except:
        pass


# ========= 获取K线 =========

def get_klines(symbol, interval):

    klines = client.futures_klines(
        symbol=symbol,
        interval=interval,
        limit=LIMIT
    )

    df = pd.DataFrame(klines, columns=[
        "open_time",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "close_time",
        "qav",
        "num_trades",
        "taker_base",
        "taker_quote",
        "ignore"
    ])

    df["close"] = df["close"].astype(float)

    return df


# ========= TD Setup =========

def td_setup(df, period=9):

    closes = df["close"].values

    n = len(closes)

    if n < period + 4:
        return 0

    buy = True
    sell = True

    for i in range(1, period + 1):

        if closes[-i] > closes[-i - 4]:
            buy = False

        if closes[-i] < closes[-i - 4]:
            sell = False

    if buy:
        return 1

    if sell:
        return -1

    return 0


# ========= 主扫描 =========

print("TD Scanner 启动")

while True:

    try:

        for symbol in SYMBOLS:

            for name, interval in INTERVALS.items():

                df = get_klines(symbol, interval)
                print(df)

                current_kline = df["open_time"].iloc[-1]

                # 只在新K线出现时计算
                if last_kline_time[symbol][name] == current_kline:
                    continue

                last_kline_time[symbol][name] = current_kline

                price = df["close"].iloc[-1]

                # ===== TD9 =====

                signal9 = td_setup(df, 9)

                if signal9 != 0 and signal9 != last_alert_9[symbol][name]:

                    last_alert_9[symbol][name] = signal9

                    if signal9 == 1:
                        msg = f"""
🟢 TD9 BUY

Symbol : {symbol}
TF     : {name}
Price  : {price}
"""
                    else:
                        msg = f"""
🔴 TD9 SELL

Symbol : {symbol}
TF     : {name}
Price  : {price}
"""

                    print(msg)
                    send_telegram(msg)

                # ===== TD13 =====

                signal13 = td_setup(df, 13)

                if signal13 != 0 and signal13 != last_alert_13[symbol][name]:

                    last_alert_13[symbol][name] = signal13

                    if signal13 == 1:
                        msg = f"""
🟢 TD13 BUY

Symbol : {symbol}
TF     : {name}
Price  : {price}
"""
                    else:
                        msg = f"""
🔴 TD13 SELL

Symbol : {symbol}
TF     : {name}
Price  : {price}
"""

                    print(msg)
                    send_telegram(msg)

        time.sleep(15)

    except Exception as e:

        print("error:", e)

        time.sleep(10)