class Fractal:

    @staticmethod
    def detect(df):

        fractals = []

        for i in range(2, len(df)-2):

            if df.high[i] > df.high[i-1] and df.high[i] > df.high[i+1]:

                fractals.append({
                    "type":"top",
                    "index":i,
                    "price":df.high[i]
                })

            if df.low[i] < df.low[i-1] and df.low[i] < df.low[i+1]:

                fractals.append({
                    "type":"bottom",
                    "index":i,
                    "price":df.low[i]
                })

        return fractals


if __name__ == "__main__":
    from data.binance_api import get_klines
    df = get_klines("ETHUSDT", "15m")

    # 缠论结构
    fractals = Fractal.detect(df)
    bis = Bi.generate(fractals)
    zs_list = ZhongShu.detect(bis)

    # 二买/二卖信号
    buy_signal = BuySellPoint.second_buy(bis, zs_list)
    sell_signal = BuySellPoint.second_sell(bis, zs_list)
