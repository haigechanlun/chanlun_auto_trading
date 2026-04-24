class ZhongShu:

    @staticmethod
    def detect(bis):

        zs_list = []

        if len(bis) < 3:
            return zs_list

        for i in range(2, len(bis)):

            b1 = bis[i-2]
            b2 = bis[i-1]
            b3 = bis[i]

            high = min(
                b1["end"]["price"],
                b2["end"]["price"],
                b3["end"]["price"]
            )

            low = max(
                b1["start"]["price"],
                b2["start"]["price"],
                b3["start"]["price"]
            )

            if high > low:

                zs_list.append({
                    "high":high,
                    "low":low
                })

        return zs_list