
class BuySellPoint:

    @staticmethod
    def second_buy(bis, zs_list, tol=0.002):  # tol 允许 0.2% 偏差
        if len(bis) < 2 or len(zs_list) == 0:
            return None

        last_bi = bis[-1]
        last_zs = zs_list[-1]

        # 方向向上，且回踩中枢下沿附近即可
        if last_bi["direction"] == "up":
            if last_bi["end"]["price"] >= last_zs["low"] and last_bi["end"]["price"] <= last_zs["high"]*(1-tol):
                return {"type":"second_buy","price":last_bi["end"]["price"]}

        return None

    @staticmethod
    def second_sell(bis, zs_list, tol=0.002):  # tol 允许 0.2% 偏差
        if len(bis) < 2 or len(zs_list) == 0:
            return None

        last_bi = bis[-1]
        last_zs = zs_list[-1]

        # 方向向下，且回踩中枢上沿附近即可
        if last_bi["direction"] == "down":
            if last_bi["end"]["price"] <= last_zs["high"] and last_bi["end"]["price"] >= last_zs["low"]*(1-tol):
                return {"type":"second_sell","price":last_bi["end"]["price"]}

        return None