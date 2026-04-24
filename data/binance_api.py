import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from binance.client import Client
import pandas as pd
from config import API_KEY, API_SECRET
import time

client = Client(API_KEY, API_SECRET)


def get_klines(symbol, interval, total_limit=5000):
    """
    分页拉取 Binance K 线数据
    symbol: 交易对，例如 'BTCUSDT'
    interval: K线周期，例如 '15m'
    total_limit: 总共想获取多少条 K 线
    """
    all_klines = []
    limit_per_request = 1000  # Binance API 单次最大限制
    start_time = None

    while len(all_klines) < total_limit:
        # 本次请求数量
        fetch_limit = min(limit_per_request, total_limit - len(all_klines))
        klines = client.get_klines(
            symbol=symbol,
            interval=interval,
            limit=fetch_limit,
            startTime=start_time
        )
        if not klines:
            break

        all_klines.extend(klines)
        # 下一次请求的起始时间 = 上一次数据最后一条 K 线收盘时间 + 1 ms
        start_time = klines[-1][6] + 1  # close_time 列索引 6

        # 避免请求过快被限流
        time.sleep(0.1)

    df = pd.DataFrame(all_klines, columns=[
        'time','open','high','low','close','volume',
        'close_time','qav','trades','tbbav','tbqav','ignore'
    ])

    # 转 float
    for col in ['open','high','low','close','volume']:
        df[col] = df[col].astype(float)

    # 转时间戳
    df['time'] = pd.to_datetime(df['time'], unit='ms')
    df['close_time'] = pd.to_datetime(df['close_time'], unit='ms')
    # print(len(df))

    return df


if __name__ == "__main__":
    df = get_klines("ETHUSDT", "15m", 1000)
    print(df)
